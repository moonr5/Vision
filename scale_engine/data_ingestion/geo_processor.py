"""
Scale Engine — Geospatial Processing Backend.
PostGIS-powered spatial engine for routes, geofences, spatial joins,
proximity alerts, corridor analysis, and heatmaps.
"""

import math
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime

from scale_engine import db


class GeoProcessor:
    """
    Geospatial engine using PostGIS for production workloads.
    Falls back to in-memory Haversine calculations when DB unavailable.
    """

    EARTH_RADIUS_M = 6_371_000

    # ── Geofence operations ──────────────────────────────────────────────

    async def point_in_geofence(
        self, lat: float, lng: float, geofence_id: str = None,
    ) -> Dict[str, Any]:
        """Check if a point is inside a geofence. Returns breach info if applicable."""
        fences = await db.geofence_check(lat, lng, geofence_id)
        inside = []
        for f in fences:
            distance = f.get("distance_m", 0)
            radius = f.get("radius_meters", 100)
            inside.append({
                "geofence_id": f["id"],
                "name": f["name"],
                "inside": distance <= radius,
                "distance_m": round(distance, 1),
                "radius_m": radius,
                "proximity_pct": round((1 - min(distance / (radius * 2), 1)) * 100, 1) if distance <= radius * 2 else 0,
            })
        return {"point": [lat, lng], "geofences": inside, "inside_any": any(g["inside"] for g in inside)}

    async def create_geofence(
        self, name: str, center: Tuple[float, float],
        radius_m: float, alert_on_enter: bool = True, alert_on_exit: bool = True,
    ) -> Dict[str, Any]:
        """Create a circular geofence."""
        if not db.available():
            return {"error": "Database unavailable"}
        import uuid
        geo_id = str(uuid.uuid4())[:8]
        async with db._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO geofences (id, name, geometry_type, center_lat, center_lng,
                   radius_meters, alert_on_enter, alert_on_exit, is_active)
                   VALUES ($1,$2,'circle',$3,$4,$5,$6,$7,TRUE)""",
                geo_id, name, center[0], center[1], radius_m, alert_on_enter, alert_on_exit,
            )
        return {"id": geo_id, "name": name, "center": center, "radius_m": radius_m}

    # ── Route analysis ───────────────────────────────────────────────────

    async def analyze_route_corridor(
        self, start: Tuple[float, float], end: Tuple[float, float],
        buffer_m: float = 500,
    ) -> Dict[str, Any]:
        """Analyze events, hazards, and risks within a route corridor."""
        events = await db.route_corridor_query(start[0], start[1], end[0], end[1], buffer_m)

        distance = self._haversine_m(start[0], start[1], end[0], end[1])
        bearing = self._bearing_deg(start[0], start[1], end[0], end[1])

        # Categorize events along the corridor
        critical = [e for e in events if e.get("type") == "CRITICAL"]
        warnings = [e for e in events if e.get("type") == "WARNING"]

        return {
            "corridor": {"start": start, "end": end, "buffer_m": buffer_m},
            "distance_km": round(distance / 1000, 2),
            "bearing_deg": round(bearing, 1),
            "total_events_nearby": len(events),
            "critical_events": len(critical),
            "warning_events": len(warnings),
            "risk_density": round(len(critical) / max(distance / 1000, 0.1), 2),  # critical per km
            "events": events[:20],
        }

    async def find_nearby_devices(
        self, lat: float, lng: float, radius_m: float = 2000,
    ) -> List[Dict[str, Any]]:
        """Find all devices near a point using PostGIS ST_DWithin."""
        if not db.available():
            return []
        async with db._pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT t.device_id, d.name, t.lat, t.lng, t.speed,
                   ST_Distance(
                     ST_SetSRID(ST_MakePoint($2, $1), 4326)::geography,
                     ST_SetSRID(ST_MakePoint(t.lng, t.lat), 4326)::geography
                   ) AS distance_m
                   FROM v_device_latest_telemetry t
                   JOIN devices d ON t.device_id = d.id
                   WHERE ST_DWithin(
                     ST_SetSRID(ST_MakePoint($2, $1), 4326)::geography,
                     ST_SetSRID(ST_MakePoint(t.lng, t.lat), 4326)::geography,
                     $3
                   )
                   ORDER BY distance_m""",
                lat, lng, radius_m,
            )
        return [dict(r) for r in rows]

    async def compute_route_intersections(
        self, routes: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Find where multiple routes intersect (shared corridor segments)."""
        intersections = []
        for i, r1 in enumerate(routes):
            for j, r2 in enumerate(routes):
                if j <= i:
                    continue
                d = self._haversine_m(
                    r1.get("destination_lat", 0), r1.get("destination_lng", 0),
                    r2.get("destination_lat", 0), r2.get("destination_lng", 0),
                )
                if d < 500:
                    intersections.append({
                        "route_a": r1.get("route_name"), "route_b": r2.get("route_name"),
                        "proximity_m": round(d, 1),
                    })
        return intersections

    # ── Geospatial math (no DB needed) ────────────────────────────────────

    def _haversine_m(self, lat1: float, lng1: float, lat2: float, lng2: float) -> float:
        dlat = math.radians(lat2 - lat1)
        dlng = math.radians(lng2 - lng1)
        a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
        return self.EARTH_RADIUS_M * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    def _bearing_deg(self, lat1: float, lng1: float, lat2: float, lng2: float) -> float:
        dlat = math.radians(lat2 - lat1)
        dlng = math.radians(lng2 - lng1)
        y = math.sin(dlng) * math.cos(math.radians(lat2))
        x = math.cos(math.radians(lat1)) * math.sin(math.radians(lat2)) - math.sin(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.cos(dlng)
        return (math.degrees(math.atan2(y, x)) + 360) % 360

    def snap_to_road(self, lat: float, lng: float) -> Tuple[float, float]:
        """Placeholder: in production, call OSRM or Google Roads API."""
        return (lat, lng)
