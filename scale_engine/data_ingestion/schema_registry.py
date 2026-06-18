"""
Scale Engine — Schema Registry.
Enforces one telemetry contract across ESP32, backend, and AI consumers.
Validates incoming payloads, tracks schema versions, logs violations.
"""

import json
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime

from scale_engine import db

# Canonical telemetry schema v1 — single source of truth
TELEMETRY_SCHEMA_V1 = {
    "type": "object",
    "required": ["device_id"],
    "properties": {
        "device_id":    {"type": "string"},
        "lat":          {"type": "number", "minimum": -90, "maximum": 90},
        "lng":          {"type": "number", "minimum": -180, "maximum": 180},
        "speed":        {"type": "number", "minimum": 0, "maximum": 300},
        "speed_obd":    {"type": "number", "minimum": 0, "maximum": 300},
        "loc":          {"type": "integer", "minimum": 0, "maximum": 1},
        "sats":         {"type": "integer", "minimum": 0, "maximum": 50},
        "throttle":     {"type": "number", "minimum": 0, "maximum": 100},
        "fuel_level":   {"type": "number", "minimum": 0, "maximum": 100},
        "coolant_temp": {"type": "number", "minimum": -40, "maximum": 200},
        "engine_load":  {"type": "number", "minimum": 0, "maximum": 100},
        "mil":          {"type": "boolean"},
        "s1":           {"type": "integer", "minimum": 0, "maximum": 1},
        "s2":           {"type": "integer", "minimum": 0, "maximum": 1},
        "mag1":         {"type": "integer", "minimum": 0, "maximum": 1},
        "mag2":         {"type": "integer", "minimum": 0, "maximum": 1},
        "fuel": {
            "type": "object",
            "properties": {
                "theft_detected": {"type": "boolean"},
                "level_percent":  {"type": "number"},
            },
        },
        "obd": {
            "type": "object",
            "properties": {
                "rpm":          {"type": "number", "minimum": 0, "maximum": 12000},
                "speed":        {"type": "number", "minimum": 0, "maximum": 300},
                "engine_load":  {"type": "number", "minimum": 0, "maximum": 100},
                "coolant_temp": {"type": "number", "minimum": -40, "maximum": 200},
                "throttle":     {"type": "number", "minimum": 0, "maximum": 100},
                "fuel_level":   {"type": "number", "minimum": 0, "maximum": 100},
            },
        },
    },
}

# Registered schemas
SCHEMAS = {
    "telemetry_v1": TELEMETRY_SCHEMA_V1,
}


class SchemaRegistry:
    """
    Validates telemetry payloads against the canonical schema.
    Logs violations for monitoring and alerting.
    """

    def __init__(self):
        self._schemas: Dict[str, Dict] = dict(SCHEMAS)
        self._violation_count = 0

    def register(self, name: str, schema: Dict[str, Any], version: int = 1):
        key = f"{name}_v{version}"
        self._schemas[key] = schema

    def validate(
        self, payload: Dict[str, Any], schema_name: str = "telemetry_v1",
    ) -> Tuple[bool, List[str]]:
        """
        Validate a telemetry payload against a registered schema.
        Returns (is_valid, list_of_violations).
        """
        schema = self._schemas.get(schema_name)
        if not schema:
            return False, [f"Unknown schema: {schema_name}"]

        violations = []
        self._check_object(payload, schema, "", violations)
        self._violation_count += len(violations)
        return len(violations) == 0, violations

    async def validate_and_log(
        self, payload: Dict[str, Any], device_id: str = None,
        schema_name: str = "telemetry_v1",
    ) -> Tuple[bool, List[str]]:
        """Validate and persist violations to PostgreSQL for monitoring."""
        is_valid, violations = self.validate(payload, schema_name)

        if not is_valid and db.available():
            try:
                async with db._pool.acquire() as conn:
                    await conn.execute(
                        """INSERT INTO schema_violations
                           (schema_name, device_id, payload, violations, created_at)
                           VALUES ($1, $2, $3, $4, $5)""",
                        schema_name,
                        device_id or payload.get("device_id"),
                        json.dumps(payload, default=str),
                        json.dumps(violations),
                        datetime.utcnow(),
                    )
            except Exception:
                pass

        return is_valid, violations

    async def get_violation_stats(self, hours: int = 24) -> Dict[str, Any]:
        """Get schema violation statistics."""
        if not db.available():
            return {"total": self._violation_count, "by_device": []}
        async with db._pool.acquire() as conn:
            total = await conn.fetchrow(
                "SELECT COUNT(*) AS cnt FROM schema_violations WHERE created_at > NOW() - ($1 || ' hours')::INTERVAL",
                str(hours),
            )
            by_device = await conn.fetch(
                "SELECT device_id, COUNT(*) AS cnt FROM schema_violations WHERE created_at > NOW() - ($1 || ' hours')::INTERVAL GROUP BY device_id ORDER BY cnt DESC LIMIT 10",
                str(hours),
            )
        return {
            "total": total["cnt"] if total else 0,
            "by_device": [dict(r) for r in by_device],
        }

    def _check_object(
        self, obj: Any, schema: Dict, path: str, violations: List[str],
    ):
        """Recursive schema validation."""
        if schema.get("type") == "object":
            if not isinstance(obj, dict):
                violations.append(f"{path}: expected object, got {type(obj).__name__}")
                return
            for prop, prop_schema in schema.get("properties", {}).items():
                field_path = f"{path}.{prop}" if path else prop
                if prop in schema.get("required", []) and prop not in obj:
                    violations.append(f"{field_path}: required field missing")
                if prop in obj:
                    self._check_object(obj[prop], prop_schema, field_path, violations)
        elif schema.get("type") == "number":
            if not isinstance(obj, (int, float)) or isinstance(obj, bool):
                violations.append(f"{path}: expected number, got {type(obj).__name__}")
            elif "minimum" in schema and obj < schema["minimum"]:
                violations.append(f"{path}: {obj} < min {schema['minimum']}")
            elif "maximum" in schema and obj > schema["maximum"]:
                violations.append(f"{path}: {obj} > max {schema['maximum']}")
        elif schema.get("type") == "integer":
            if not isinstance(obj, int) or isinstance(obj, bool):
                violations.append(f"{path}: expected integer, got {type(obj).__name__}")
        elif schema.get("type") == "boolean":
            if not isinstance(obj, bool):
                violations.append(f"{path}: expected boolean, got {type(obj).__name__}")
        elif schema.get("type") == "string":
            if not isinstance(obj, str):
                violations.append(f"{path}: expected string, got {type(obj).__name__}")
