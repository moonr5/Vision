"""
Scale Engine — Time-Series Engine.
Optimized queries for TimescaleDB/ClickHouse/InfluxDB on high-volume telemetry.
Supports downsampling, rollups, continuous aggregates, and window functions.
"""

from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
import asyncio

from scale_engine import db


class TimeseriesEngine:
    """
    High-performance time-series query engine for fleet telemetry.
    Uses TimescaleDB continuous aggregates + window functions.
    """

    # ── Continuous aggregates (TimescaleDB) ──────────────────────────────

    async def setup_continuous_aggregates(self):
        """Create TimescaleDB continuous aggregates for common rollups."""
        if not db.available():
            return
        async with db._pool.acquire() as conn:
            aggs = [
                # Hourly telemetry rollup per device
                """CREATE MATERIALIZED VIEW IF NOT EXISTS cagg_telemetry_hourly
                   WITH (timescaledb.continuous) AS
                   SELECT device_id,
                     time_bucket('1 hour', timestamp) AS bucket,
                     COUNT(*) AS point_count,
                     AVG(speed) AS avg_speed,
                     MAX(speed) AS max_speed,
                     AVG(obd_rpm) AS avg_rpm,
                     AVG(fuel_level) AS avg_fuel_level,
                     AVG(obd_engine_load) AS avg_engine_load,
                     AVG(obd_throttle) AS avg_throttle,
                     SUM(distance_km) AS total_distance_km
                   FROM telemetry
                   GROUP BY device_id, bucket""",

                # Daily driver behavior rollup
                """CREATE MATERIALIZED VIEW IF NOT EXISTS cagg_behavior_daily
                   WITH (timescaledb.continuous) AS
                   SELECT driver_id,
                     time_bucket('1 day', timestamp) AS bucket,
                     COUNT(*) AS total_events,
                     COUNT(*) FILTER (WHERE event_type = 'CRITICAL') AS critical,
                     COUNT(*) FILTER (WHERE event_type = 'WARNING') AS warning,
                     COUNT(*) FILTER (WHERE event_type = 'INFO') AS info,
                     AVG(safety_score) AS avg_safety_score
                   FROM driver_behavior_history
                   GROUP BY driver_id, bucket""",
            ]
            for sql in aggs:
                try:
                    await conn.execute(sql)
                except Exception:
                    pass

    # ── High-performance queries ─────────────────────────────────────────

    async def get_telemetry_timeseries(
        self,
        device_id: str,
        start: datetime,
        end: datetime,
        interval: str = "5 minutes",
    ) -> List[Dict[str, Any]]:
        """Get downsampled telemetry time series with TimescaleDB time_bucket."""
        if not db.available():
            return []

        async with db._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""SELECT
                     time_bucket($3::INTERVAL, timestamp) AS bucket,
                     AVG(speed) AS avg_speed,
                     MAX(speed) AS max_speed,
                     AVG(obd_rpm) AS avg_rpm,
                     AVG(fuel_level) AS avg_fuel,
                     AVG(obd_engine_load) AS avg_load,
                     AVG(obd_coolant_temp) AS avg_coolant_temp,
                     COUNT(*) AS samples
                   FROM telemetry
                   WHERE device_id = $1
                     AND timestamp BETWEEN $2 AND $4
                   GROUP BY bucket
                   ORDER BY bucket""",
                device_id, start, interval, end,
            )
        return [dict(r) for r in rows]

    async def get_fleet_aggregates(
        self, window: str = "1 hour",
    ) -> Dict[str, Any]:
        """Real-time fleet-wide aggregates over a sliding window."""
        if not db.available():
            return {}

        async with db._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""SELECT
                     COUNT(DISTINCT device_id) AS active_devices,
                     AVG(speed) AS fleet_avg_speed,
                     MAX(speed) AS fleet_max_speed,
                     PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY speed) AS p95_speed,
                     AVG(fuel_level) AS fleet_avg_fuel,
                     COUNT(*) FILTER (WHERE fuel_theft_detected) AS fuel_thefts,
                     COUNT(*) FILTER (WHERE obd_mil) AS mil_active
                   FROM telemetry
                   WHERE timestamp > NOW() - $1::INTERVAL""",
                window,
            )
        return dict(row) if row else {}

    async def get_driver_trend(self, driver_id: str, days: int = 30) -> List[Dict[str, Any]]:
        """Daily safety score trend for a driver."""
        if not db.available():
            return []

        async with db._pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT
                     time_bucket('1 day', timestamp) AS day,
                     COUNT(*) AS events,
                     COUNT(*) FILTER (WHERE event_type = 'CRITICAL') AS critical,
                     COUNT(*) FILTER (WHERE event_type = 'WARNING') AS warning,
                     MIN(safety_score) AS min_score
                   FROM driver_behavior_history
                   WHERE driver_id = $1 AND timestamp > NOW() - ($2 || ' days')::INTERVAL
                   GROUP BY day ORDER BY day""",
                driver_id, str(days),
            )
        return [dict(r) for r in rows]

    async def get_route_heatmap(
        self, start_lat: float, start_lng: float,
        end_lat: float, end_lng: float, days: int = 30,
    ) -> List[Dict[str, Any]]:
        """Get a heatmap of events along a route corridor."""
        if not db.available():
            return []

        async with db._pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT lat, lng, type, event, COUNT(*) AS weight
                   FROM events
                   WHERE lat IS NOT NULL AND lng IS NOT NULL
                     AND created_at > NOW() - ($5 || ' days')::INTERVAL
                     AND ST_DWithin(
                       ST_SetSRID(ST_MakePoint(lng, lat), 4326)::geography,
                       ST_MakeLine(
                         ST_SetSRID(ST_MakePoint($2, $1), 4326),
                         ST_SetSRID(ST_MakePoint($4, $3), 4326)
                       )::geography,
                       1000
                     )
                   GROUP BY lat, lng, type, event""",
                start_lat, start_lng, end_lat, end_lng, str(days),
            )
        return [dict(r) for r in rows]
