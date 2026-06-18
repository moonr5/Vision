"""
Scale Engine — Event-Sourced Fleet State Engine.
Rebuilds live vehicle/order/driver state from immutable event streams.
Every state change is an event — state is a projection of the event log.
"""

import json
import asyncio
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from collections import defaultdict


class FleetStateEngine:
    """
    Event-sourced state engine for the entire fleet.

    Principles:
      - State = projection of events
      - Every mutation is logged as an immutable event
      - Current state can be rebuilt from any point in the event stream
      - Supports snapshotting for fast recovery
    """

    EVENT_TYPES = [
        "DEVICE_ONLINE", "DEVICE_OFFLINE",
        "TELEMETRY_RECEIVED",
        "ORDER_CREATED", "ORDER_STATUS_CHANGED", "ORDER_ASSIGNED",
        "DRIVER_ASSIGNED", "DRIVER_STATUS_CHANGED",
        "GEOFENCE_ENTERED", "GEOFENCE_EXITED",
        "FUEL_THEFT_DETECTED", "SECURITY_BREACH",
        "TRIP_STARTED", "TRIP_ENDED",
        "BEHAVIOR_EVENT", "MAINTENANCE_ALERT",
    ]

    def __init__(self):
        # Current projected state
        self._state: Dict[str, Any] = {
            "devices": {},
            "drivers": {},
            "orders": {},
            "trips": {},
            "geofences": {},
            "event_log": [],
            "snapshot_version": 0,
            "last_event_id": 0,
        }
        self._event_id = 0
        self._subscribers: List[callable] = []

    # ── Event ingestion ──────────────────────────────────────────────────

    async def apply_event(self, event_type: str, payload: Dict[str, Any]) -> int:
        """Apply a single event to the state projection."""
        if event_type not in self.EVENT_TYPES:
            raise ValueError(f"Unknown event type: {event_type}")

        self._event_id += 1
        event = {
            "id": self._event_id,
            "type": event_type,
            "payload": payload,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Immutable append to event log
        self._state["event_log"].append(event)
        self._state["last_event_id"] = self._event_id

        # Project state change
        handler = getattr(self, f"_handle_{event_type.lower()}", None)
        if handler:
            handler(payload)

        # Notify subscribers
        for sub in self._subscribers:
            try:
                await sub(event)
            except Exception:
                pass

        return self._event_id

    async def replay_events(self, events: List[Dict[str, Any]]):
        """Rebuild state from a historical event stream."""
        self._state = {
            "devices": {}, "drivers": {}, "orders": {},
            "trips": {}, "geofences": {},
            "event_log": [], "snapshot_version": 0, "last_event_id": 0,
        }
        for event in sorted(events, key=lambda e: e.get("timestamp", "")):
            await self.apply_event(event["type"], event.get("payload", {}))

    # ── State query API ──────────────────────────────────────────────────

    def get_device_state(self, device_id: str) -> Dict[str, Any]:
        return self._state["devices"].get(device_id, {"status": "unknown"})

    def get_driver_state(self, driver_id: str) -> Dict[str, Any]:
        return self._state["drivers"].get(driver_id, {"status": "unknown"})

    def get_order_state(self, order_id: str) -> Dict[str, Any]:
        return self._state["orders"].get(order_id, {"status": "unknown"})

    def get_active_trips(self) -> List[Dict[str, Any]]:
        return [t for t in self._state["trips"].values() if t.get("status") == "active"]

    def get_full_state(self) -> Dict[str, Any]:
        return {
            "devices": dict(self._state["devices"]),
            "drivers": dict(self._state["drivers"]),
            "orders": dict(self._state["orders"]),
            "active_trips": self.get_active_trips(),
            "last_event_id": self._state["last_event_id"],
            "snapshot_version": self._state["snapshot_version"],
        }

    def snapshot(self) -> Dict[str, Any]:
        """Create a point-in-time snapshot for fast recovery."""
        self._state["snapshot_version"] += 1
        return self.get_full_state()

    def subscribe(self, callback):
        """Register a callback to be notified on every event."""
        self._subscribers.append(callback)

    # ── Event handlers (projections) ─────────────────────────────────────

    def _handle_device_online(self, p: Dict):
        did = p["device_id"]
        self._state["devices"][did] = {**self._state["devices"].get(did, {}), **p, "status": "online", "last_seen": datetime.now(timezone.utc).isoformat()}

    def _handle_device_offline(self, p: Dict):
        did = p["device_id"]
        self._state["devices"][did] = {**self._state["devices"].get(did, {}), "status": "offline"}

    def _handle_telemetry_received(self, p: Dict):
        did = p.get("device_id", "")
        if did not in self._state["devices"]:
            self._state["devices"][did] = {"status": "online"}
        self._state["devices"][did].update({
            "lat": p.get("lat"), "lng": p.get("lng"),
            "speed": p.get("speed"), "heading": p.get("heading"),
            "fuel_level": p.get("fuel_level"),
            "last_seen": datetime.now(timezone.utc).isoformat(),
        })

    def _handle_order_created(self, p: Dict):
        oid = p["order_id"]
        self._state["orders"][oid] = {**p, "status": "created", "created_at": datetime.now(timezone.utc).isoformat()}

    def _handle_order_status_changed(self, p: Dict):
        oid = p["order_id"]
        if oid in self._state["orders"]:
            self._state["orders"][oid]["status"] = p["new_status"]

    def _handle_driver_assigned(self, p: Dict):
        did = p["driver_id"]
        self._state["drivers"][did] = {**self._state["drivers"].get(did, {}), **p}

    def _handle_trip_started(self, p: Dict):
        tid = p.get("trip_id", f"trip_{self._event_id}")
        self._state["trips"][tid] = {**p, "status": "active", "started_at": datetime.now(timezone.utc).isoformat()}

    def _handle_trip_ended(self, p: Dict):
        tid = p["trip_id"]
        if tid in self._state["trips"]:
            self._state["trips"][tid]["status"] = "completed"

    def _handle_geofence_entered(self, p: Dict): pass  # Future: proximity alerts
    def _handle_geofence_exited(self, p: Dict): pass
    def _handle_fuel_theft_detected(self, p: Dict): pass
    def _handle_security_breach(self, p: Dict): pass
    def _handle_behavior_event(self, p: Dict): pass
    def _handle_maintenance_alert(self, p: Dict): pass
    def _handle_driver_status_changed(self, p: Dict):
        did = p["driver_id"]
        if did in self._state["drivers"]:
            self._state["drivers"][did]["status"] = p["new_status"]
    def _handle_order_assigned(self, p: Dict):
        oid = p["order_id"]
        if oid in self._state["orders"]:
            self._state["orders"][oid]["driver_id"] = p.get("driver_id")
            self._state["orders"][oid]["device_id"] = p.get("device_id")
