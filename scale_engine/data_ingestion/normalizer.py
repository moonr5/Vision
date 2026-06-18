"""
Scale Engine — Telemetry Normalization Layer.
Unifies GPS, OBD, sensors, and buffered SD replays into one canonical model.
Handles field mapping, unit conversion, timestamp alignment, and gap filling.
"""

from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone


class TelemetryNormalizer:
    """
    Normalizes heterogeneous telemetry inputs into a single consistent model.

    Input sources:
      - Live MQTT (ESP32 JSON)
      - Buffered SD card replays (ESP32 JSON with type="buffered")
      - Direct OBD-II frames
      - External GPS modules
      - Manual driver inputs

    Output: Always the canonical telemetry_v1 schema shape.
    """

    # Field mapping: canonical_name → [possible_source_keys]
    FIELD_MAP = {
        "device_id":    ["device_id", "deviceId", "dev_id", "id"],
        "lat":          ["lat", "latitude", "gps_lat", "gps.lat"],
        "lng":          ["lng", "lng", "longitude", "lon", "gps_lng", "gps.lng"],
        "speed":        ["speed", "gps_speed", "speed_kmh", "velocity"],
        "speed_obd":    ["speed_obd", "obd_speed", "obd.speed"],
        "loc":          ["loc", "gps_fix", "fix", "gps.loc", "location_fix"],
        "sats":         ["sats", "satellites", "gps.sats", "gps_sats"],
        "throttle":     ["throttle", "obd_throttle", "throttle_pos", "obd.throttle"],
        "fuel_level":   ["fuel_level", "fuel", "fuel_pct", "obd.fuel_level", "fuel.level_percent"],
        "coolant_temp": ["coolant_temp", "coolant", "ect", "obd.coolant_temp", "engine_temp"],
        "engine_load":  ["engine_load", "load", "obd.engine_load", "load_pct"],
        "mil":          ["mil", "check_engine", "cel", "obd.mil", "malfunction_indicator"],
        "rpm":          ["rpm", "obd_rpm", "engine_rpm", "obd.rpm"],
        "s1":           ["s1", "sensor_s1", "sensor1", "door_switch_1", "limit_switch_1"],
        "s2":           ["s2", "sensor_s2", "sensor2", "door_switch_2", "limit_switch_2"],
        "mag1":         ["mag1", "sensor_mag1", "magnetic1", "mag_sensor_1"],
        "mag2":         ["mag2", "sensor_mag2", "magnetic2", "mag_sensor_2"],
        "fuel_theft":   ["fuel_theft_detected", "fuel.theft_detected", "theft", "fuel_theft"],
    }

    def __init__(self):
        self._normalized_count = 0
        self._error_count = 0

    def normalize(self, raw: Dict[str, Any], source: str = "mqtt", original_timestamp: str = None) -> Dict[str, Any]:
        """
        Normalize any telemetry input into the canonical shape.

        Args:
            raw: The raw payload dict from any source
            source: "mqtt" | "buffered" | "obd_direct" | "gps_module" | "manual"
            original_timestamp: ISO timestamp from buffered data (preserves original time)

        Returns:
            Normalized dict matching telemetry_v1 schema
        """
        normalized = {
            "device_id": "unknown-device",
            "lat": None, "lng": None, "speed": None,
            "speed_obd": None, "loc": 0, "sats": 0,
            "throttle": None, "fuel_level": None,
            "coolant_temp": None, "engine_load": None,
            "mil": False, "rpm": None,
            "s1": 1, "s2": 1, "mag1": 1, "mag2": 1,
            "fuel_theft_detected": False,
        }

        # Flatten nested structures (obd.*, fuel.*, gps.*)
        flat = self._flatten(raw)

        # Map fields by priority
        for canonical, candidates in self.FIELD_MAP.items():
            for key in candidates:
                val = self._extract(flat, key)
                if val is not None:
                    normalized[canonical] = self._coerce(canonical, val)
                    break

        # Add metadata
        normalized["_source"] = source
        normalized["_normalized_at"] = datetime.now(timezone.utc).isoformat()

        if source == "buffered" and original_timestamp:
            normalized["_original_timestamp"] = original_timestamp
            # Use original timestamp for historical accuracy
            normalized["timestamp"] = original_timestamp

        # Unit conversions
        normalized = self._convert_units(normalized, source)

        # Detect type
        if raw.get("type") == "buffered":
            normalized["_replay"] = True

        self._normalized_count += 1
        return normalized

    def normalize_batch(self, records: List[Dict[str, Any]], source: str = "buffered") -> List[Dict[str, Any]]:
        """Normalize a batch of records (e.g., SD card replay)."""
        results = []
        for r in records:
            try:
                ts = r.get("original_timestamp") or r.get("timestamp") or r.get("time")
                n = self.normalize(r, source=source, original_timestamp=ts)
                results.append(n)
            except Exception as e:
                self._error_count += 1
        return results

    def stats(self) -> Dict[str, Any]:
        return {"normalized": self._normalized_count, "errors": self._error_count}

    # ── Internal helpers ─────────────────────────────────────────────────

    def _flatten(self, obj: Dict, prefix: str = "") -> Dict[str, Any]:
        """Flatten nested dicts using dot notation: obd.speed → obd_speed"""
        result = {}
        for k, v in obj.items():
            if isinstance(v, dict) and not prefix:
                for sk, sv in v.items():
                    result[f"{k}.{sk}"] = sv
            result[k] = v
        return result

    def _extract(self, data: Dict, key: str) -> Any:
        """Extract a value by key or dot-path."""
        if "." in key:
            parts = key.split(".")
            val = data
            for p in parts:
                if isinstance(val, dict):
                    val = val.get(p)
                else:
                    return None
            return val
        return data.get(key)

    def _coerce(self, field: str, val: Any) -> Any:
        """Coerce value to the expected type for the field."""
        if val is None:
            return None
        bool_fields = {"mil", "fuel_theft_detected", "fuel_theft"}
        int_fields = {"loc", "sats", "s1", "s2", "mag1", "mag2", "rpm"}
        float_fields = {"lat", "lng", "speed", "speed_obd", "throttle", "fuel_level", "coolant_temp", "engine_load"}

        if field in bool_fields:
            return bool(val)
        if field in int_fields:
            try:
                return int(float(val))
            except (ValueError, TypeError):
                return val
        if field in float_fields:
            try:
                return float(val)
            except (ValueError, TypeError):
                return val
        return val

    def _convert_units(self, data: Dict[str, Any], source: str) -> Dict[str, Any]:
        """Convert units to canonical: speed in km/h, temp in °C, etc."""
        # Speed: if source provides m/s, convert to km/h
        if source == "gps_module" and data.get("speed"):
            # Some GPS modules output m/s
            if data["speed"] < 5 and data.get("obd.speed", 0) > 30:
                data["speed"] = data["speed"] * 3.6  # m/s → km/h

        # Coolant temp: ensure °C (some OBD report raw byte value)
        if data.get("coolant_temp") and data["coolant_temp"] > 200:
            data["coolant_temp"] = data["coolant_temp"] - 40  # raw OBD byte → °C

        return data
