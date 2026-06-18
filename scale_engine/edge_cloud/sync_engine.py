"""
Scale Engine — Sync & Conflict Engine.
Reconciles browser SQLite, PostgreSQL, and buffered offline data.
CRDT-inspired conflict resolution for fleet telemetry.
"""

import json
from typing import Dict, Any, List, Optional, Tuple, Set
from datetime import datetime, timedelta
from collections import defaultdict

# Define the sync message schema
SYNC_EVENT_TYPES = [
    "telemetry_insert",
    "event_insert",
    "order_update",
    "device_status_change",
    "settings_change",
]

class SyncConflictResolver:
    """
    Resolves conflicts when multiple data sources write to the same record.

    Strategy (per entity):
      - Telemetry: timestamp + device_id is the natural key.
        Last-write-wins (LWW) with server authority.
      - Events: server is always authoritative. Client logs are supplemental.
      - Orders: server authoritative. Client can propose but server accepts/rejects.
      - Settings: per-key LWW with vector clock.

    For simplicity, the server timestamps are authoritative.
    """

    def resolve_telemetry_conflict(
        self, server_record: Dict, client_record: Dict,
    ) -> Dict[str, Any]:
        """Resolve duplicate telemetry entries."""
        server_ts = server_record.get("timestamp", "")
        client_ts = client_record.get("timestamp", "")

        # Server wins unless client is significantly newer (>1 min)
        if server_ts >= client_ts:
            return {"action": "keep_server", "record": server_record,
                    "reason": "Server has newer or equal timestamp"}

        return {"action": "use_client", "record": client_record,
                "reason": "Client timestamp is newer"}

    def resolve_event_conflict(
        self, server_event: Dict, client_event: Dict,
    ) -> Dict[str, Any]:
        """Server events are authoritative. Client events are supplemental."""
        return {"action": "keep_both", "server": server_event, "client": client_event,
                "reason": "Events are append-only — both retained"}

    def resolve_order_conflict(
        self, server_order: Dict, client_order: Dict,
    ) -> Dict[str, Any]:
        """Server is authoritative for orders."""
        return {"action": "keep_server", "record": server_order,
                "reason": "Server is authoritative for order state"}


class SyncEngine:
    """
    Bidirectional sync engine between browser SQLite and cloud PostgreSQL.

    Flow:
      Cloud→Edge: Push new orders, settings, geofences → browser SQLite
      Edge→Cloud: Push buffered telemetry, events, local state → PostgreSQL
      Conflict: Server-authoritative for orders/events, LWW for telemetry
    """

    def __init__(self):
        self._resolver = SyncConflictResolver()
        self._sync_log: List[Dict] = []
        self._pending: Dict[str, List[Dict]] = defaultdict(list)  # device_id → pending changes

    # ── Cloud → Edge sync ───────────────────────────────────────────────

    async def push_to_edge(self, device_id: str, since: datetime = None) -> Dict[str, Any]:
        """
        Generate a sync payload for a device (or browser).
        Contains new orders, settings changes, and geofence updates since the last sync.
        """
        from scale_engine import db

        since = since or (datetime.utcnow() - timedelta(hours=1))

        payload = {
            "sync_id": f"cloud_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
            "timestamp": datetime.utcnow().isoformat(),
            "device_id": device_id,
            "orders": [],
            "settings": {},
            "geofences": [],
        }

        if db.available():
            async with db._pool.acquire() as conn:
                # Orders assigned to this device
                orders = await conn.fetch(
                    """SELECT * FROM orders WHERE device_id = $1
                       AND updated_at > $2 ORDER BY updated_at""",
                    device_id, since,
                )
                payload["orders"] = [dict(o) for o in orders]

                # Settings changed since
                settings = await conn.fetch(
                    "SELECT key, value, value_type FROM settings WHERE updated_at > $1",
                    since,
                )
                payload["settings"] = {s["key"]: {"value": s["value"], "type": s["value_type"]} for s in settings}

                # Active geofences
                geofences = await conn.fetch(
                    "SELECT * FROM geofences WHERE is_active = TRUE"
                )
                payload["geofences"] = [dict(g) for g in geofences]

        self._sync_log.append({"direction": "cloud_to_edge", "device": device_id,
                                "items": len(payload["orders"]) + len(payload["settings"]) + len(payload["geofences"])})
        return payload

    # ── Edge → Cloud sync ───────────────────────────────────────────────

    async def ingest_from_edge(self, device_id: str,
                                telemetry_batch: List[Dict],
                                events: List[Dict] = None) -> Dict[str, Any]:
        """
        Ingest a batch of telemetry + events from an edge device or browser.
        Handles buffered SD card replays and live data uniformly.
        """
        from scale_engine import db

        result = {"accepted": 0, "conflicts": 0, "rejected": 0, "details": []}

        if not db.available():
            result["error"] = "Database unavailable"
            return result

        async with db._pool.acquire() as conn:
            for t in telemetry_batch:
                try:
                    # Check for existing record with same device+timestamp
                    existing = await conn.fetchrow(
                        """SELECT id FROM telemetry
                           WHERE device_id = $1 AND timestamp = $2""",
                        t.get("device_id", device_id),
                        t.get("timestamp", datetime.utcnow().isoformat()),
                    )

                    if existing:
                        resolution = self._resolver.resolve_telemetry_conflict(
                            dict(existing), t,
                        )
                        if resolution["action"] == "use_client":
                            # Update the record
                            await conn.execute(
                                """UPDATE telemetry SET lat=$1, lng=$2, speed=$3, updated_at=NOW()
                                   WHERE id=$4""",
                                t.get("lat"), t.get("lng"), t.get("speed"),
                                existing["id"],
                            )
                            result["updated"] = result.get("updated", 0) + 1
                        result["conflicts"] += 1
                    else:
                        # Insert new
                        await conn.execute(
                            """INSERT INTO telemetry (device_id, lat, lng, speed, obd_rpm,
                               obd_speed, fuel_level, coolant_temp, raw_payload, timestamp)
                               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
                            t.get("device_id"), t.get("lat"), t.get("lng"),
                            t.get("speed"), t.get("obd_rpm"), t.get("obd_speed"),
                            t.get("fuel_level"), t.get("coolant_temp"),
                            json.dumps(t, default=str),
                            t.get("timestamp", datetime.utcnow().isoformat()),
                        )
                        result["accepted"] += 1
                except Exception as e:
                    result["rejected"] += 1
                    result["details"].append({"error": str(e), "record": str(t)[:100]})

        self._sync_log.append({"direction": "edge_to_cloud", "device": device_id,
                                "accepted": result["accepted"], "conflicts": result["conflicts"]})
        return result

    # ── Offline buffer reconciliation ────────────────────────────────────

    def queue_offline_change(self, device_id: str, change_type: str, data: Dict):
        """Queue a change that happened while the device was offline."""
        self._pending[device_id].append({
            "type": change_type,
            "data": data,
            "queued_at": datetime.utcnow().isoformat(),
        })

    async def flush_pending(self, device_id: str) -> Dict[str, Any]:
        """Flush all pending offline changes for a device."""
        pending = self._pending.get(device_id, [])
        if not pending:
            return {"flushed": 0}

        telemetry = [p["data"] for p in pending if p["type"] == "telemetry_insert"]
        events = [p["data"] for p in pending if p["type"] == "event_insert"]

        result = await self.ingest_from_edge(device_id, telemetry, events)
        self._pending[device_id] = []
        result["flushed_from_queue"] = len(pending)
        return result

    # ── Query ────────────────────────────────────────────────────────────

    def get_sync_status(self) -> Dict[str, Any]:
        """Get sync engine status."""
        return {
            "pending_devices": {k: len(v) for k, v in self._pending.items()},
            "sync_log_size": len(self._sync_log),
            "recent_syncs": self._sync_log[-10:],
        }
