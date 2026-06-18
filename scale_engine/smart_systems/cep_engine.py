"""
Scale Engine — Complex Event Processing (CEP) Engine.
Fuses multiple sensor streams into high-level event detection rules.
Rule: fuel_theft = (S1==0 AND speed<2 AND fuel_level dropping) OR (cap_open AND motion_detected)
"""

import asyncio
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime, timedelta
from collections import defaultdict
from dataclasses import dataclass, field
import json


@dataclass
class CEPRule:
    name: str
    description: str
    conditions: Dict[str, Any]  # key=field, value=expected or {"min":, "max":}
    window_seconds: int = 60    # time window for the rule
    min_occurrences: int = 1
    severity: str = "WARNING"
    action: str = "alert"       # alert | log | notify | all


class ComplexEventProcessor:
    """
    Real-time complex event processing engine.
    Evaluates multi-condition rules across sliding time windows.

    Built-in rules cover:
      - Fuel theft detection (sensor + speed + fuel level)
      - Security breach (mag sensor + door + GPS loss)
      - Cargo tampering (S1 + S2 + motion)
      - Driver distress (no movement + engine on + long duration)
      - Route deviation (GPS outside corridor + no geofence match)
    """

    BUILTIN_RULES = [
        CEPRule(
            name="fuel_theft_complex",
            description="Fuel cap open while stationary + fuel level dropping",
            conditions={"s1": 0, "speed": {"max": 2}, "fuel_level_delta": {"max": -2}},
            window_seconds=120, min_occurrences=2, severity="CRITICAL",
        ),
        CEPRule(
            name="cargo_security_breach",
            description="Both door sensors triggered while in motion",
            conditions={"s1": 0, "s2": 0, "speed": {"min": 5}},
            window_seconds=30, severity="CRITICAL",
        ),
        CEPRule(
            name="magnetic_tampering",
            description="Magnetic sensor triggered + GPS fix lost",
            conditions={"mag1": 0, "loc": 0},
            window_seconds=60, severity="CRITICAL",
        ),
        CEPRule(
            name="excessive_idling_prolonged",
            description="Engine running, no movement for > 10 minutes",
            conditions={"speed": 0, "rpm": {"min": 400}},
            window_seconds=600, min_occurrences=5, severity="WARNING",
        ),
        CEPRule(
            name="route_deviation",
            description="GPS outside expected corridor + not in any geofence",
            conditions={"geofence_inside": False, "speed": {"min": 10}},
            window_seconds=300, min_occurrences=2, severity="WARNING",
        ),
        CEPRule(
            name="harsh_braking_chain",
            description="Multiple harsh braking events in a short window",
            conditions={"harsh_braking": True},
            window_seconds=300, min_occurrences=3, severity="WARNING",
        ),
        CEPRule(
            name="engine_stress_combo",
            description="High RPM + high load + high coolant temp simultaneously",
            conditions={"rpm": {"min": 4000}, "engine_load": {"min": 80}, "coolant_temp": {"min": 105}},
            window_seconds=60, severity="WARNING",
        ),
    ]

    def __init__(self):
        self._rules: Dict[str, CEPRule] = {r.name: r for r in self.BUILTIN_RULES}
        self._windows: Dict[str, Dict[str, List[Dict]]] = defaultdict(lambda: defaultdict(list))
        self._triggered: List[Dict[str, Any]] = []  # Circular buffer of triggered events
        self._alert_callbacks: List[Callable] = []

    # ── Rule management ──────────────────────────────────────────────────

    def add_rule(self, rule: CEPRule):
        self._rules[rule.name] = rule

    def remove_rule(self, name: str):
        self._rules.pop(name, None)

    def get_rules(self) -> List[Dict]:
        return [{"name": r.name, "desc": r.description, "severity": r.severity} for r in self._rules.values()]

    # ── Event ingestion ──────────────────────────────────────────────────

    def ingest(self, telemetry: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Ingest a telemetry record and evaluate all CEP rules.
        Returns list of triggered alerts.
        """
        triggered = []
        now = datetime.utcnow()

        # Enrich telemetry with derived fields
        enriched = self._enrich(telemetry)

        # Evaluate each rule
        for rule in self._rules.values():
            window_key = f"{rule.name}:{telemetry.get('device_id', 'unknown')}"
            window = self._windows[window_key]

            # Add to sliding window
            window[rule.name].append({"data": enriched, "ts": now})

            # Prune old entries
            cutoff = now - timedelta(seconds=rule.window_seconds)
            window[rule.name] = [e for e in window[rule.name] if e["ts"] > cutoff]

            # Evaluate conditions
            matches = self._evaluate_rule(rule, window[rule.name])

            if len(matches) >= rule.min_occurrences:
                alert = {
                    "rule": rule.name,
                    "severity": rule.severity,
                    "device_id": telemetry.get("device_id"),
                    "matched_conditions": rule.conditions,
                    "occurrences": len(matches),
                    "window_seconds": rule.window_seconds,
                    "triggered_at": now.isoformat(),
                    "action": rule.action,
                }
                triggered.append(alert)
                self._triggered.append(alert)
                if len(self._triggered) > 1000:
                    self._triggered = self._triggered[-500:]

                # Fire callbacks
                for cb in self._alert_callbacks:
                    try:
                        cb(alert)
                    except Exception:
                        pass

        return triggered

    def ingest_batch(self, batch: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Process a batch of telemetry records."""
        all_alerts = []
        for record in batch:
            alerts = self.ingest(record)
            all_alerts.extend(alerts)
        return all_alerts

    # ── Query API ────────────────────────────────────────────────────────

    def get_recent_alerts(self, limit: int = 50) -> List[Dict]:
        return self._triggered[-limit:]

    def get_fleet_alert_summary(self) -> Dict[str, Any]:
        """Summarize recent alerts across the fleet."""
        recent = self._triggered[-200:]
        by_rule = defaultdict(int)
        by_device = defaultdict(int)
        by_severity = defaultdict(int)
        for a in recent:
            by_rule[a["rule"]] += 1
            by_device[a.get("device_id", "?")] += 1
            by_severity[a["severity"]] += 1
        return {
            "total_alerts_triggered": len(self._triggered),
            "recent_alerts": len(recent),
            "by_rule": dict(by_rule),
            "by_device": dict(by_device),
            "by_severity": dict(by_severity),
        }

    def on_alert(self, callback: Callable):
        """Register a callback for triggered alerts."""
        self._alert_callbacks.append(callback)

    # ── Internal ─────────────────────────────────────────────────────────

    def _enrich(self, telemetry: Dict) -> Dict:
        """Add derived fields for rule evaluation."""
        enriched = dict(telemetry)
        enriched["harsh_braking"] = telemetry.get("speed_delta", 0) < -15
        enriched["rpm"] = telemetry.get("rpm") or telemetry.get("obd_rpm", 0)
        enriched["speed"] = telemetry.get("speed") or 0
        enriched["engine_load"] = telemetry.get("engine_load") or telemetry.get("obd_engine_load", 0)
        enriched["coolant_temp"] = telemetry.get("coolant_temp") or telemetry.get("obd_coolant_temp", 0)
        enriched["geofence_inside"] = telemetry.get("geofence_inside", True)
        return enriched

    def _evaluate_rule(self, rule: CEPRule, window: List[Dict]) -> List[Dict]:
        """Check which entries in the window match all rule conditions."""
        matches = []
        for entry in window:
            data = entry["data"]
            if self._match_conditions(rule.conditions, data):
                matches.append(entry)
        return matches

    def _match_conditions(self, conditions: Dict, data: Dict) -> bool:
        """Check if all conditions match."""
        for field, expected in conditions.items():
            actual = data.get(field)
            if actual is None:
                return False
            if isinstance(expected, dict):
                if "min" in expected and actual < expected["min"]:
                    return False
                if "max" in expected and actual > expected["max"]:
                    return False
            elif isinstance(expected, bool):
                if bool(actual) != expected:
                    return False
            elif actual != expected:
                return False
        return True
