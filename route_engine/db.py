"""
Route Engine — PostgreSQL database layer.
Connects to the same database as the main backend (server.js) and ai_backend.
All queries use asyncpg for non-blocking access.
"""

import os
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field

import asyncpg

_pool: Optional[asyncpg.Pool] = None


# ── Pool lifecycle ──────────────────────────────────────────────────────────

async def init_pool():
    global _pool
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        print("[RouteEngine:DB] DATABASE_URL not set — DB queries will be skipped")
        return
    _pool = await asyncpg.create_pool(dsn, ssl="require", min_size=1, max_size=5)
    print("[RouteEngine:DB] asyncpg pool ready")


async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        print("[RouteEngine:DB] pool closed")


def _pool_available() -> bool:
    return _pool is not None


# ── Data shapes ─────────────────────────────────────────────────────────────

@dataclass
class DriverBehaviorProfile:
    driver_id: str
    driver_name: str
    safety_score: int
    total_events: int
    harsh_braking_count: int = 0
    aggressive_launch_count: int = 0
    cold_engine_abuse_count: int = 0
    engine_lugging_count: int = 0
    excessive_idling_count: int = 0
    speeding_count: int = 0
    avg_speed: float = 0.0
    max_speed: float = 0.0
    fuel_efficiency: float = 0.0         # avg km/L
    total_distance_km: float = 0.0
    total_trips: int = 0
    route_safety_tags: List[str] = field(default_factory=list)
    raw_events: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class RouteSegment:
    """A segment of a route with telemetry-based performance metrics."""
    lat: float
    lng: float
    speed: float = 0.0
    heading: float = 0.0
    engine_load: float = 0.0
    throttle: float = 0.0
    fuel_flow: float = 0.0
    event_count: int = 0


@dataclass
class RouteCandidate:
    """One possible route between origin and destination."""
    route_id: str
    route_name: str
    origin_lat: float
    origin_lng: float
    destination_lat: float
    destination_lng: float
    distance_km: float = 0.0
    estimated_duration_min: float = 0.0
    avg_speed_kmh: float = 0.0
    fuel_estimate_l: float = 0.0
    safety_score: float = 100.0          # 0-100, higher = safer
    behavior_risk_index: float = 0.0     # 0-1, lower = better
    segment_count: int = 0
    segments: List[RouteSegment] = field(default_factory=list)
    hazard_zones: List[Dict[str, Any]] = field(default_factory=list)
    ai_rationale: str = ""


# ── Queries ─────────────────────────────────────────────────────────────────

async def get_driver_behavior_profile(driver_id: str) -> Optional[Dict[str, Any]]:
    """Fetch full driver behavior profile from the database."""
    if not _pool_available():
        return None

    async with _pool.acquire() as conn:
        # Driver basics
        driver = await conn.fetchrow(
            """SELECT d.id, d.name, d.safety_score, d.vehicle_plate,
                      d.behavior_events_json, d.behavior_metrics_json
               FROM drivers d WHERE d.id = $1""",
            driver_id,
        )
        if not driver:
            return None

        # Behavior history (last 90 days)
        events = await conn.fetch(
            """SELECT event_name, event_type, event_details, metrics_json, timestamp
               FROM driver_behavior_history
               WHERE driver_id = $1 AND timestamp > NOW() - INTERVAL '90 days'
               ORDER BY timestamp DESC""",
            driver_id,
        )

        # Trip stats
        trip_stats = await conn.fetchrow(
            """SELECT COUNT(*) AS total_trips,
                      COALESCE(SUM(total_distance_km), 0) AS total_distance_km,
                      COALESCE(AVG(fuel_efficiency), 0) AS avg_fuel_efficiency,
                      COALESCE(AVG(avg_speed), 0) AS avg_speed,
                      COALESCE(MAX(max_speed), 0) AS max_speed
               FROM trips
               WHERE driver_id = $1 AND status = 'completed'""",
            driver_id,
        )

    # Categorise events
    event_counts = {
        "Harsh Braking": 0, "Aggressive Launch": 0, "Cold Engine Abuse": 0,
        "Engine Lugging": 0, "Excessive Idling": 0, "Speeding": 0,
    }
    for e in events:
        name = e["event_name"] or ""
        for key in event_counts:
            if key.lower() in name.lower():
                event_counts[key] += 1
                break

    raw_events = [dict(e) for e in events]

    return {
        "driver_id": driver["id"],
        "driver_name": driver["name"],
        "safety_score": driver["safety_score"] or 100,
        "total_events": len(events),
        "harsh_braking_count": event_counts["Harsh Braking"],
        "aggressive_launch_count": event_counts["Aggressive Launch"],
        "cold_engine_abuse_count": event_counts["Cold Engine Abuse"],
        "engine_lugging_count": event_counts["Engine Lugging"],
        "excessive_idling_count": event_counts["Excessive Idling"],
        "speeding_count": event_counts["Speeding"],
        "avg_speed": round(float(trip_stats["avg_speed"] or 0), 1),
        "max_speed": round(float(trip_stats["max_speed"] or 0), 1),
        "fuel_efficiency": round(float(trip_stats["avg_fuel_efficiency"] or 0), 2),
        "total_distance_km": round(float(trip_stats["total_distance_km"] or 0), 1),
        "total_trips": trip_stats["total_trips"] or 0,
        "raw_events": raw_events,
    }


async def get_telemetry_segments(
    device_id: str, hours: int = 168
) -> List[Dict[str, Any]]:
    """Fetch telemetry points for a device to reconstruct route segments."""
    if not _pool_available():
        return []

    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT lat, lng, speed, heading, obd_engine_load, obd_throttle,
                      COALESCE(fuel_flow_out, 0) AS fuel_flow,
                      COALESCE(obd_rpm, 0) AS rpm
               FROM telemetry
               WHERE device_id = $1
                 AND lat IS NOT NULL AND lng IS NOT NULL
                 AND timestamp > NOW() - ($2 || ' hours')::INTERVAL
               ORDER BY timestamp ASC""",
            device_id, str(hours),
        )
    return [dict(r) for r in rows]


async def get_route_events(
    driver_id: str, route_lat: float, route_lng: float, radius_km: float = 0.5
) -> List[Dict[str, Any]]:
    """Find behavior events that occurred near a given route corridor."""
    if not _pool_available():
        return []

    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT e.type, e.category, e.event, e.details, e.speed,
                      e.lat, e.lng, e.created_at,
                      d.name AS driver_name, d.safety_score
               FROM events e
               LEFT JOIN drivers d ON e.driver_id = d.id
               WHERE e.driver_id = $1
                 AND e.lat IS NOT NULL AND e.lng IS NOT NULL
                 AND e.created_at > NOW() - INTERVAL '90 days'
               ORDER BY e.created_at DESC
               LIMIT 200""",
            driver_id,
        )
    return [dict(r) for r in rows]


async def get_historical_trips(
    driver_id: Optional[str] = None,
    device_id: Optional[str] = None,
    status: str = "completed",
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Fetch historical trip records with optional driver/device filter."""
    if not _pool_available():
        return []

    conditions = ["status = $1"]
    params: List[Any] = [status]
    idx = 2

    if driver_id:
        conditions.append(f"driver_id = ${idx}")
        params.append(driver_id)
        idx += 1
    if device_id:
        conditions.append(f"device_id = ${idx}")
        params.append(device_id)
        idx += 1

    where = " AND ".join(conditions)

    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT t.*, d.name AS driver_name, d.safety_score
                FROM trips t
                LEFT JOIN drivers d ON t.driver_id = d.id
                WHERE {where}
                ORDER BY t.ended_at DESC NULLS LAST
                LIMIT {limit}""",
            *params,
        )
    return [dict(r) for r in rows]


async def get_driver_route_history(
    driver_id: str, origin_lat: float, origin_lng: float,
    dest_lat: float, dest_lng: float, radius_km: float = 2.0,
) -> List[Dict[str, Any]]:
    """Find past trips that started/ended near the given points for this driver."""
    if not _pool_available():
        return []

    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT t.*, d.name AS driver_name, d.safety_score,
                      d.vehicle_plate
               FROM trips t
               JOIN drivers d ON t.driver_id = d.id
               WHERE t.driver_id = $1
                 AND t.status = 'completed'
                 AND t.start_lat IS NOT NULL AND t.start_lng IS NOT NULL
                 AND t.end_lat IS NOT NULL AND t.end_lng IS NOT NULL
               ORDER BY t.ended_at DESC NULLS LAST
               LIMIT 20""",
            driver_id,
        )
    return [dict(r) for r in rows]


async def get_fleet_behavior_summary() -> Dict[str, Any]:
    """Aggregate behavior data across all active drivers for benchmarking."""
    if not _pool_available():
        return {}

    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT
                 COUNT(*) FILTER (WHERE status = 'active') AS active_drivers,
                 ROUND(AVG(safety_score)::numeric, 1) AS fleet_avg_score,
                 MIN(safety_score) AS fleet_min_score,
                 MAX(safety_score) AS fleet_max_score
               FROM drivers"""
        )
        behaviour_summary = await conn.fetch(
            """SELECT event_name, COUNT(*) AS count
               FROM driver_behavior_history
               WHERE timestamp > NOW() - INTERVAL '30 days'
               GROUP BY event_name
               ORDER BY count DESC"""
        )

    return {
        "active_drivers": row["active_drivers"],
        "fleet_avg_score": float(row["fleet_avg_score"] or 0),
        "fleet_min_score": row["fleet_min_score"] or 0,
        "fleet_max_score": row["fleet_max_score"] or 0,
        "top_events_30d": [dict(r) for r in behaviour_summary],
    }
