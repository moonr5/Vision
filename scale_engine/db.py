"""
Scale Engine — Shared PostgreSQL / TimescaleDB database layer.
Connects to the same database as server.js + ai_backend + route_engine.
Extends with TimescaleDB hypertables, PostGIS spatial queries, and
materialized views for high-performance analytics.
"""

import os
import json
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import asyncpg

_pool: Optional[asyncpg.Pool] = None


# ── Pool lifecycle ──────────────────────────────────────────────────────────

async def init_pool():
    global _pool
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        print("[ScaleEngine:DB] DATABASE_URL not set — running without DB")
        return
    _pool = await asyncpg.create_pool(
        dsn, ssl="require", min_size=2, max_size=10,
        command_timeout=30,
    )
    # Enable TimescaleDB extension if available (idempotent)
    try:
        async with _pool.acquire() as conn:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE")
            await conn.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        print("[ScaleEngine:DB] TimescaleDB + PostGIS extensions ready")
    except Exception:
        print("[ScaleEngine:DB] TimescaleDB/PostGIS not available — using standard PG")
    print("[ScaleEngine:DB] Connection pool ready")


async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


def available() -> bool:
    return _pool is not None


# ── TimescaleDB hypertable setup (idempotent) ───────────────────────────────

async def setup_hypertables():
    """Convert telemetry + events tables to TimescaleDB hypertables for time-series performance."""
    if not available():
        return
    async with _pool.acquire() as conn:
        migrations = [
            # Telemetry hypertable (partitioned by timestamp, 1-day chunks)
            "SELECT create_hypertable('telemetry', 'timestamp', if_not_exists => TRUE, chunk_time_interval => INTERVAL '1 day')",
            # Events hypertable
            "SELECT create_hypertable('events', 'event_time', if_not_exists => TRUE, chunk_time_interval => INTERVAL '1 day')",
            # Driver behavior hypertable
            "SELECT create_hypertable('driver_behavior_history', 'timestamp', if_not_exists => TRUE, chunk_time_interval => INTERVAL '7 days')",
            # Enable compression on telemetry older than 7 days
            "SELECT add_compression_policy('telemetry', INTERVAL '7 days', if_not_exists => TRUE)",
        ]
        for sql in migrations:
            try:
                await conn.execute(sql)
            except Exception:
                pass  # TimescaleDB may not be installed — non-fatal
    print("[ScaleEngine:DB] Hypertable setup complete")


# ── Schema registry tables ──────────────────────────────────────────────────

async def setup_schema_registry():
    """Create schema registry tables for telemetry contract enforcement."""
    if not available():
        return
    async with _pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_versions (
                id          SERIAL PRIMARY KEY,
                schema_name TEXT NOT NULL,
                version     INTEGER NOT NULL,
                schema_json JSONB NOT NULL,
                is_active   BOOLEAN DEFAULT TRUE,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(schema_name, version)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_violations (
                id          SERIAL PRIMARY KEY,
                schema_name TEXT,
                device_id   TEXT,
                payload     JSONB,
                violations  JSONB,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Create index on violations for fast query
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_schema_violations_time
            ON schema_violations(created_at DESC)
        """)


# ── Materialized views for analytics ────────────────────────────────────────

async def refresh_analytics_views():
    """Refresh materialized views for fast dashboard queries."""
    if not available():
        return
    async with _pool.acquire() as conn:
        views = [
            # Driver aggregate stats (last 30 days)
            """CREATE MATERIALIZED VIEW IF NOT EXISTS mv_driver_stats_30d AS
               SELECT d.id AS driver_id, d.name, d.safety_score,
                 COUNT(DISTINCT t.id) AS trip_count,
                 COALESCE(SUM(t.total_distance_km), 0) AS total_km,
                 COALESCE(AVG(t.fuel_efficiency), 0) AS avg_fuel_eff,
                 COALESCE(COUNT(bh.id) FILTER (WHERE bh.event_type = 'CRITICAL'), 0) AS critical_events,
                 COALESCE(COUNT(bh.id) FILTER (WHERE bh.event_type = 'WARNING'), 0) AS warning_events
               FROM drivers d
               LEFT JOIN trips t ON d.id = t.driver_id AND t.ended_at > NOW() - INTERVAL '30 days'
               LEFT JOIN driver_behavior_history bh ON d.id = bh.driver_id AND bh.timestamp > NOW() - INTERVAL '30 days'
               GROUP BY d.id, d.name, d.safety_score""",

            # Route segment performance
            """CREATE MATERIALIZED VIEW IF NOT EXISTS mv_route_performance AS
               SELECT t.driver_id, d.name AS driver_name,
                 ROUND(t.start_lat::numeric, 3) AS origin_lat_r,
                 ROUND(t.start_lng::numeric, 3) AS origin_lng_r,
                 ROUND(t.end_lat::numeric, 3) AS dest_lat_r,
                 ROUND(t.end_lng::numeric, 3) AS dest_lng_r,
                 COUNT(*) AS trip_count,
                 ROUND(AVG(t.total_distance_km)::numeric, 1) AS avg_distance_km,
                 ROUND(AVG(t.avg_speed)::numeric, 1) AS avg_speed,
                 ROUND(AVG(t.fuel_efficiency)::numeric, 2) AS avg_fuel_eff,
                 SUM(t.events_critical) AS total_critical
               FROM trips t
               JOIN drivers d ON t.driver_id = d.id
               WHERE t.status = 'completed' AND t.start_lat IS NOT NULL
               GROUP BY t.driver_id, d.name,
                 ROUND(t.start_lat::numeric, 3), ROUND(t.start_lng::numeric, 3),
                 ROUND(t.end_lat::numeric, 3), ROUND(t.end_lng::numeric, 3)""",
        ]
        for sql in views:
            try:
                await conn.execute(sql)
            except Exception as e:
                pass  # View might already exist or TimescaleDB not available


# ── Hot/Warm/Cold query helpers ─────────────────────────────────────────────

async def query_tiered(
    table: str,
    columns: str = "*",
    conditions: str = "1=1",
    params: List[Any] = None,
    tier: str = "hot",
) -> List[Dict[str, Any]]:
    """
    Query telemetry across storage tiers.
    - hot: last 7 days (live PG)
    - warm: 7-90 days (compressed hypertable chunks)
    - cold: 90+ days (archive table or S3 external — returns empty if not configured)
    """
    if not available():
        return []

    tier_sql = {
        "hot":  f"SELECT {columns} FROM {table} WHERE {conditions} AND timestamp > NOW() - INTERVAL '7 days'",
        "warm": f"SELECT {columns} FROM {table} WHERE {conditions} AND timestamp BETWEEN NOW() - INTERVAL '90 days' AND NOW() - INTERVAL '7 days'",
        "cold": f"SELECT {columns} FROM {table}_archive WHERE {conditions} AND timestamp <= NOW() - INTERVAL '90 days'",
    }

    sql = tier_sql.get(tier, tier_sql["hot"])
    try:
        async with _pool.acquire() as conn:
            rows = await conn.fetch(sql, *(params or []))
        return [dict(r) for r in rows]
    except Exception:
        return []


# ── Geospatial PostGIS helpers ──────────────────────────────────────────────

async def geofence_check(
    lat: float, lng: float, geofence_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Check if a point is inside any (or a specific) geofence using PostGIS."""
    if not available():
        return []

    async with _pool.acquire() as conn:
        if geofence_id:
            rows = await conn.fetch(
                """SELECT id, name, geometry_type,
                   ST_Distance(
                     ST_SetSRID(ST_MakePoint($2, $1), 4326)::geography,
                     ST_SetSRID(ST_MakePoint(center_lng, center_lat), 4326)::geography
                   ) AS distance_m
                   FROM geofences WHERE id = $3 AND is_active = TRUE""",
                lat, lng, geofence_id,
            )
        else:
            rows = await conn.fetch(
                """SELECT id, name, geometry_type,
                   ST_Distance(
                     ST_SetSRID(ST_MakePoint($2, $1), 4326)::geography,
                     ST_SetSRID(ST_MakePoint(center_lng, center_lat), 4326)::geography
                   ) AS distance_m,
                   radius_meters
                   FROM geofences WHERE is_active = TRUE
                   ORDER BY distance_m""",
                lat, lng,
            )
    return [dict(r) for r in rows]


async def route_corridor_query(
    start_lat: float, start_lng: float,
    end_lat: float, end_lng: float,
    buffer_meters: float = 500,
) -> List[Dict[str, Any]]:
    """Find events + hazards within a route corridor using PostGIS ST_DWithin."""
    if not available():
        return []

    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT e.*, d.name AS driver_name
               FROM events e
               LEFT JOIN drivers d ON e.driver_id = d.id
               WHERE e.lat IS NOT NULL AND e.lng IS NOT NULL
                 AND e.created_at > NOW() - INTERVAL '30 days'
                 AND ST_DWithin(
                   ST_SetSRID(ST_MakePoint(e.lng, e.lat), 4326)::geography,
                   ST_MakeLine(
                     ST_SetSRID(ST_MakePoint($2, $1), 4326),
                     ST_SetSRID(ST_MakePoint($4, $3), 4326)
                   )::geography,
                   $5
                 )
               ORDER BY e.created_at DESC
               LIMIT 200""",
            start_lat, start_lng, end_lat, end_lng, buffer_meters,
        )
    return [dict(r) for r in rows]


# ── Data quality checks ─────────────────────────────────────────────────────

async def detect_quality_issues(hours: int = 24) -> List[Dict[str, Any]]:
    """Run data quality checks on recent telemetry."""
    if not available():
        return []

    issues = []
    async with _pool.acquire() as conn:
        # Missing GPS but engine running
        missing_gps = await conn.fetchrow(
            """SELECT COUNT(*) AS count FROM telemetry
               WHERE timestamp > NOW() - ($1 || ' hours')::INTERVAL
                 AND (lat IS NULL OR lng IS NULL OR lat = 0)
                 AND obd_rpm > 400""",
            str(hours),
        )
        if missing_gps and missing_gps["count"] > 0:
            issues.append({"type": "missing_gps_engine_on", "count": missing_gps["count"], "severity": "WARNING"})

        # OBD speed vs GPS speed drift (>20% difference)
        speed_drift = await conn.fetchrow(
            """SELECT COUNT(*) AS count FROM telemetry
               WHERE timestamp > NOW() - ($1 || ' hours')::INTERVAL
                 AND obd_speed IS NOT NULL AND speed IS NOT NULL AND speed > 0
                 AND ABS(obd_speed - speed) / speed > 0.2""",
            str(hours),
        )
        if speed_drift and speed_drift["count"] > 0:
            issues.append({"type": "speed_drift", "count": speed_drift["count"], "severity": "INFO"})

        # Duplicate packets (same device, same timestamp)
        duplicates = await conn.fetchrow(
            """SELECT COUNT(*) - COUNT(DISTINCT (device_id, timestamp)) AS count
               FROM telemetry
               WHERE timestamp > NOW() - ($1 || ' hours')::INTERVAL""",
            str(hours),
        )
        if duplicates and duplicates["count"] > 0:
            issues.append({"type": "duplicate_packets", "count": duplicates["count"], "severity": "WARNING"})

        # Stale devices (no data > 10 minutes)
        stale = await conn.fetch(
            """SELECT id, name, last_seen FROM devices
               WHERE last_seen < NOW() - INTERVAL '10 minutes'
                 AND status = 'online'""",
        )
        for s in stale:
            issues.append({"type": "stale_device", "device_id": s["id"], "name": s["name"], "severity": "CRITICAL"})

    return issues
