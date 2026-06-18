"""
Scale Engine — Edge Model Management Backend.
Pushes updated behavior/threshold models to ESP32 devices safely.
Manages model versions, staged rollouts, and rollback.
"""

import json
import hashlib
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from dataclasses import dataclass


@dataclass
class EdgeModel:
    model_id: str
    model_name: str
    version: str
    target_device_type: str  # "esp32_obd2", "esp32_gps", "all"
    parameters: Dict[str, Any]  # The actual threshold values / weights
    checksum: str
    min_firmware_version: str = "2.0"
    created_at: str = ""


class EdgeModelManager:
    """
    Manages edge model lifecycle:

    1. Define model (thresholds, weights)
    2. Version it
    3. Stage rollout to target devices
    4. Push via MQTT (the ESP32 listens for model update messages)
    5. Verify device acknowledgment
    6. Rollback if errors detected
    """

    def __init__(self):
        self._models: Dict[str, List[EdgeModel]] = {}  # model_name → versions
        self._device_model_map: Dict[str, str] = {}     # device_id → active model_version
        self._rollout_state: Dict[str, Dict] = {}        # rollout_id → state

    # ── Model creation ───────────────────────────────────────────────────

    def create_behavior_model(
        self, version: str, thresholds: Dict[str, float],
        min_fw: str = "2.0",
    ) -> EdgeModel:
        """Create a new behavior analysis model for edge deployment."""
        params = {
            "harsh_braking_threshold": thresholds.get("harsh_braking", 15.0),
            "aggressive_launch_throttle": thresholds.get("aggressive_launch", 90),
            "aggressive_launch_speed": thresholds.get("launch_speed_max", 30),
            "cold_engine_rpm": thresholds.get("cold_engine_rpm", 3000),
            "cold_engine_temp": thresholds.get("cold_engine_temp", 70),
            "engine_lugging_load": thresholds.get("engine_lugging_load", 85),
            "engine_lugging_rpm": thresholds.get("engine_lugging_rpm", 1500),
            "excessive_idling_time": thresholds.get("excessive_idling_sec", 180),
            "excessive_idling_rpm": thresholds.get("excessive_idling_rpm_threshold", 500),
            "speeding_threshold": thresholds.get("speeding", 110),
            "score_penalties": {
                "harsh_braking": -5,
                "aggressive_launch": -4,
                "cold_engine_abuse": -3,
                "engine_lugging": -4,
                "excessive_idling": -2,
                "speeding": -6,
            },
        }

        payload = json.dumps(params, sort_keys=True)
        checksum = hashlib.sha256(payload.encode()).hexdigest()[:16]

        model = EdgeModel(
            model_id=f"behavior_{version}",
            model_name="driver_behavior",
            version=version,
            target_device_type="esp32_obd2",
            parameters=params,
            checksum=checksum,
            min_firmware_version=min_fw,
            created_at=datetime.utcnow().isoformat(),
        )

        self._models.setdefault("driver_behavior", []).append(model)
        return model

    def create_sensor_threshold_model(
        self, version: str, sensor_config: Dict[str, Any],
    ) -> EdgeModel:
        """Create a sensor threshold configuration model."""
        params = {
            "s1_debounce_ms": sensor_config.get("s1_debounce", 50),
            "s2_debounce_ms": sensor_config.get("s2_debounce", 50),
            "mag1_sensitivity": sensor_config.get("mag1_sensitivity", 1),
            "mag2_sensitivity": sensor_config.get("mag2_sensitivity", 1),
            "fuel_theft_check_interval_s": sensor_config.get("fuel_theft_interval", 3),
            "fuel_theft_speed_threshold": sensor_config.get("fuel_theft_speed_max", 2),
        }

        payload = json.dumps(params, sort_keys=True)
        checksum = hashlib.sha256(payload.encode()).hexdigest()[:16]

        model = EdgeModel(
            model_id=f"sensor_{version}",
            model_name="sensor_thresholds",
            version=version,
            target_device_type="all",
            parameters=params,
            checksum=checksum,
            created_at=datetime.utcnow().isoformat(),
        )

        self._models.setdefault("sensor_thresholds", []).append(model)
        return model

    # ── Rollout management ───────────────────────────────────────────────

    def stage_rollout(self, model_name: str, version: str,
                      target_devices: List[str], rollout_pct: int = 10) -> Dict[str, Any]:
        """
        Stage a gradual rollout of a model version to target devices.
        rollout_pct: percentage of target devices to update this wave
        """
        model = self._get_model(model_name, version)
        if not model:
            return {"error": f"Model {model_name} v{version} not found"}

        num_to_update = max(1, int(len(target_devices) * rollout_pct / 100))
        devices_this_wave = target_devices[:num_to_update]

        rollout_id = f"rollout_{model_name}_{version}_{datetime.utcnow().strftime('%Y%m%d%H%M')}"

        self._rollout_state[rollout_id] = {
            "model_name": model_name,
            "version": version,
            "checksum": model.checksum,
            "target_devices": target_devices,
            "devices_updated": [],
            "devices_pending": devices_this_wave,
            "devices_failed": [],
            "rollout_pct": rollout_pct,
            "status": "staged",
            "created_at": datetime.utcnow().isoformat(),
        }

        return {
            "rollout_id": rollout_id,
            "devices_to_update": devices_this_wave,
            "model_version": version,
            "checksum": model.checksum,
            "update_command": self._build_mqtt_update_message(model),
        }

    def confirm_device_update(self, rollout_id: str, device_id: str,
                              reported_checksum: str, success: bool = True):
        """Record a device's acknowledgment of a model update."""
        rollout = self._rollout_state.get(rollout_id)
        if not rollout:
            return {"error": "Rollout not found"}

        if success and reported_checksum == rollout["checksum"]:
            rollout["devices_updated"].append(device_id)
            if device_id in rollout["devices_pending"]:
                rollout["devices_pending"].remove(device_id)
            self._device_model_map[device_id] = f"{rollout['model_name']}:{rollout['version']}"
        else:
            rollout["devices_failed"].append({
                "device_id": device_id,
                "expected_checksum": rollout["checksum"],
                "reported_checksum": reported_checksum,
            })

        remaining = len(rollout["devices_pending"])
        if remaining == 0:
            rollout["status"] = "completed"

        return {"rollout_id": rollout_id, "device_id": device_id, "success": success,
                "remaining_pending": remaining}

    def rollback_device(self, device_id: str, to_version: str) -> Dict:
        """Rollback a device to a previous model version."""
        current = self._device_model_map.get(device_id)
        self._device_model_map[device_id] = to_version
        return {"device_id": device_id, "rolled_back_to": to_version, "was": current}

    # ── Query ────────────────────────────────────────────────────────────

    def get_device_model_status(self, device_id: str) -> Dict[str, Any]:
        """Get the current edge model status for a device."""
        model_ref = self._device_model_map.get(device_id, "driver_behavior:v1.0")
        return {"device_id": device_id, "active_model": model_ref,
                "models_available": list(self._models.keys())}

    def get_rollout_status(self, rollout_id: str) -> Optional[Dict]:
        return self._rollout_state.get(rollout_id)

    def list_models(self) -> List[Dict]:
        result = []
        for name, versions in self._models.items():
            for v in versions:
                result.append({
                    "name": name, "version": v.version,
                    "target": v.target_device_type,
                    "checksum": v.checksum,
                    "created_at": v.created_at,
                })
        return result

    # ── Helpers ──────────────────────────────────────────────────────────

    def _get_model(self, name: str, version: str) -> Optional[EdgeModel]:
        for m in self._models.get(name, []):
            if m.version == version:
                return m
        return None

    def _build_mqtt_update_message(self, model: EdgeModel) -> Dict[str, Any]:
        """Build the MQTT message that triggers an ESP32 model update."""
        return {
            "type": "model_update",
            "model_name": model.model_name,
            "version": model.version,
            "parameters": model.parameters,
            "checksum": model.checksum,
            "min_firmware": model.min_firmware_version,
            "timestamp": datetime.utcnow().isoformat(),
        }
