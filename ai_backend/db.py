import os
from typing import Optional, Dict, Any
import asyncpg

_pool: Optional[asyncpg.Pool] = None


async def init_pool():
    global _pool
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        print("[DB] DATABASE_URL not set — DB queries will be skipped")
        return
    _pool = await asyncpg.create_pool(dsn, ssl="require", min_size=1, max_size=5)
    print("[DB] asyncpg pool ready")


async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


def _pool_available() -> bool:
    return _pool is not None


async def get_fleet_snapshot() -> Dict[str, Any]:
    """Return a summary dict matching the shape that FleetAnalyzer._build_context_text expects."""
    if not _pool_available():
        return {}

    async with _pool.acquire() as conn:
        drivers_row = await conn.fetchrow(
            "SELECT COUNT(*) AS total, "
            "COUNT(*) FILTER (WHERE status = 'active') AS active, "
            "ROUND(AVG(safety_score)::numeric, 1) AS avg_score "
            "FROM drivers"
        )
        top_drivers = await conn.fetch(
            "SELECT name, safety_score FROM drivers "
            "WHERE status = 'active' ORDER BY safety_score DESC LIMIT 3"
        )
        bottom_drivers = await conn.fetch(
            "SELECT name, safety_score FROM drivers "
            "WHERE status = 'active' ORDER BY safety_score ASC LIMIT 3"
        )
        orders_row = await conn.fetchrow(
            "SELECT COUNT(*) AS total, "
            "COUNT(*) FILTER (WHERE status IN ('pending','in_transit')) AS active "
            "FROM orders"
        )
        devices_row = await conn.fetchrow(
            "SELECT COUNT(*) AS total, "
            "COUNT(*) FILTER (WHERE status = 'online') AS online "
            "FROM devices"
        )
        alerts = await conn.fetch(
            "SELECT event_type AS type, description AS event, device_id "
            "FROM events WHERE severity IN ('critical','warning') "
            "AND timestamp > NOW() - INTERVAL '24 hours' "
            "ORDER BY timestamp DESC LIMIT 10"
        )
        events_row = await conn.fetchrow(
            "SELECT COUNT(*) AS total, "
            "COUNT(*) FILTER (WHERE severity = 'critical') AS critical "
            "FROM events WHERE timestamp > NOW() - INTERVAL '24 hours'"
        )

    return {
        "drivers": {
            "total": drivers_row["total"],
            "active": drivers_row["active"],
            "avgScore": float(drivers_row["avg_score"]) if drivers_row["avg_score"] else "N/A",
        },
        "topDrivers": [{"name": r["name"], "safety_score": r["safety_score"]} for r in top_drivers],
        "bottomDrivers": [{"name": r["name"], "safety_score": r["safety_score"]} for r in bottom_drivers],
        "orders": {"total": orders_row["total"], "active": orders_row["active"]},
        "devices": {"total": devices_row["total"], "online": devices_row["online"]},
        "alerts": [dict(r) for r in alerts],
        "events": {"total": events_row["total"], "critical": events_row["critical"]},
    }


async def get_report_data() -> Dict[str, Any]:
    """Return full data needed to render the PDF report."""
    if not _pool_available():
        return {"drivers": [], "orders": [], "events": [], "stats": {}}

    async with _pool.acquire() as conn:
        drivers = await conn.fetch(
            "SELECT name, phone, license_number, status, safety_score, "
            "total_distance, total_trips FROM drivers ORDER BY safety_score DESC"
        )
        orders = await conn.fetch(
            "SELECT o.order_number, c.name AS customer, o.status, "
            "o.pickup_location, o.delivery_location, o.created_at "
            "FROM orders o LEFT JOIN customers c ON o.customer_id = c.id "
            "ORDER BY o.created_at DESC LIMIT 50"
        )
        events = await conn.fetch(
            "SELECT e.timestamp, e.event_type, e.description, e.severity, "
            "d.name AS driver_name, e.device_id "
            "FROM events e LEFT JOIN drivers d ON e.driver_id = d.id "
            "WHERE e.timestamp > NOW() - INTERVAL '7 days' "
            "ORDER BY e.timestamp DESC LIMIT 100"
        )
        stats = await conn.fetchrow(
            "SELECT "
            "(SELECT COUNT(*) FROM drivers WHERE status = 'active') AS active_drivers, "
            "(SELECT COUNT(*) FROM orders WHERE status IN ('pending','in_transit')) AS active_orders, "
            "(SELECT COUNT(*) FROM devices WHERE status = 'online') AS online_devices, "
            "(SELECT COUNT(*) FROM events WHERE severity = 'critical' "
            "  AND timestamp > NOW() - INTERVAL '24 hours') AS critical_events_24h"
        )

    return {
        "drivers": [dict(r) for r in drivers],
        "orders": [dict(r) for r in orders],
        "events": [dict(r) for r in events],
        "stats": dict(stats) if stats else {},
    }
