"""
Scale Engine — Federated Learning Coordinator.
Improves models across fleets without moving raw data.
Edge devices compute local model updates; cloud aggregates them.
"""

import json
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from collections import defaultdict
import math


class FederatedLearningCoordinator:
    """
    Coordinates federated learning across a fleet of ESP32 edge devices.

    How it works:
      1. Cloud sends a global model (behavior thresholds, scoring weights)
      2. Each device computes local updates from its own data
      3. Devices send ONLY the model updates (gradients), not raw data
      4. Cloud aggregates updates via Federated Averaging (FedAvg)
      5. Updated global model is pushed back to devices

    This preserves privacy — raw telemetry never leaves the device.
    """

    def __init__(self):
        self._global_model: Dict[str, Any] = {
            "version": "1.0.0",
            "parameters": self._default_parameters(),
            "num_devices_contributing": 0,
            "round": 0,
            "last_aggregated": None,
        }
        self._local_updates: Dict[int, List[Dict]] = defaultdict(list)  # round → updates
        self._device_contributions: Dict[str, int] = defaultdict(int)
        self._current_round = 0

    def _default_parameters(self) -> Dict[str, float]:
        """Default global behavior model parameters."""
        return {
            "harsh_braking_threshold": 15.0,
            "aggressive_launch_throttle": 90.0,
            "aggressive_launch_speed": 30.0,
            "cold_engine_rpm": 3000.0,
            "cold_engine_temp": 70.0,
            "engine_lugging_load": 85.0,
            "engine_lugging_rpm": 1500.0,
            "excessive_idling_time": 180.0,
            "excessive_idling_rpm": 500.0,
            "speeding_threshold": 110.0,
            "harsh_braking_penalty": 5.0,
            "aggressive_launch_penalty": 4.0,
            "speeding_penalty": 6.0,
        }

    # ── Global model management ──────────────────────────────────────────

    def get_global_model(self) -> Dict[str, Any]:
        """Return the current global model for distribution to devices."""
        return {
            **self._global_model,
            "distributed_at": datetime.utcnow().isoformat(),
            "round": self._current_round,
        }

    def update_global_model(self, new_params: Dict[str, float], version: str = None):
        """Manually update the global model (e.g., from expert tuning)."""
        self._global_model["parameters"].update(new_params)
        if version:
            self._global_model["version"] = version
        self._global_model["last_aggregated"] = datetime.utcnow().isoformat()

    # ── Federated round ─────────────────────────────────────────────────

    def start_round(self) -> int:
        """Begin a new federated learning round."""
        self._current_round += 1
        return self._current_round

    def submit_local_update(
        self, device_id: str, round_num: int,
        parameter_deltas: Dict[str, float],
        num_local_samples: int,
    ) -> Dict[str, Any]:
        """
        Receive a local model update from an edge device.

        parameter_deltas: {param_name: delta_value} — the gradient-like update
        num_local_samples: number of data points used to compute this update

        The cloud never sees the raw data — only the parameter updates.
        """
        self._local_updates[round_num].append({
            "device_id": device_id,
            "deltas": parameter_deltas,
            "num_samples": num_local_samples,
            "submitted_at": datetime.utcnow().isoformat(),
        })
        self._device_contributions[device_id] += 1

        return {
            "round": round_num,
            "device_id": device_id,
            "accepted": True,
            "total_updates_this_round": len(self._local_updates[round_num]),
        }

    def aggregate(self, round_num: int, min_devices: int = 2) -> Dict[str, Any]:
        """
        Aggregate local updates using Federated Averaging.
        Returns the new global model.
        """
        updates = self._local_updates.get(round_num, [])
        if len(updates) < min_devices:
            return {"error": f"Need at least {min_devices} devices — got {len(updates)}"}

        # Weighted average: each device's contribution is weighted by its sample count
        total_samples = sum(u["num_samples"] for u in updates)

        aggregated_deltas = {}
        for param in self._global_model["parameters"]:
            weighted_sum = 0
            for u in updates:
                delta = u["deltas"].get(param, 0)
                weight = u["num_samples"] / max(total_samples, 1)
                weighted_sum += delta * weight
            aggregated_deltas[param] = weighted_sum

        # Apply aggregated deltas to global model (with learning rate)
        lr = 0.1  # conservative learning rate for federated updates
        for param, delta in aggregated_deltas.items():
            current = self._global_model["parameters"].get(param, 0)
            # Clamp to reasonable ranges
            new_val = current + lr * delta
            new_val = self._clamp(param, new_val)
            self._global_model["parameters"][param] = round(new_val, 2)

        self._global_model["num_devices_contributing"] += len(updates)
        self._global_model["round"] = round_num
        self._global_model["last_aggregated"] = datetime.utcnow().isoformat()
        self._global_model["version"] = f"1.{round_num}.0"

        return {
            "round": round_num,
            "devices_aggregated": len(updates),
            "total_samples": total_samples,
            "new_model_version": self._global_model["version"],
            "parameter_changes": {
                param: round(aggregated_deltas.get(param, 0), 4)
                for param in self._global_model["parameters"]
                if abs(aggregated_deltas.get(param, 0)) > 0.001
            },
            "global_model": self._global_model,
        }

    # ── Differential privacy ─────────────────────────────────────────────

    def add_noise(self, deltas: Dict[str, float], epsilon: float = 1.0) -> Dict[str, float]:
        """
        Add Laplace noise for differential privacy.
        Higher epsilon = less privacy but more accuracy.
        """
        import random
        noised = {}
        for param, delta in deltas.items():
            scale = 1.0 / max(epsilon, 0.1)
            noise = random.gauss(0, scale)
            noised[param] = delta + noise
        return noised

    # ── Query ───────────────────────────────────────────────────────────

    def get_fleet_contribution_stats(self) -> Dict[str, Any]:
        """Get per-device contribution statistics."""
        top_contributors = sorted(
            self._device_contributions.items(),
            key=lambda x: x[1], reverse=True,
        )[:10]

        return {
            "total_devices": len(self._device_contributions),
            "total_contributions": sum(self._device_contributions.values()),
            "top_contributors": [{"device": d, "rounds": c} for d, c in top_contributors],
            "current_round": self._current_round,
        }

    # ── Helpers ──────────────────────────────────────────────────────────

    def _clamp(self, param: str, value: float) -> float:
        """Clamp parameter values to valid ranges."""
        ranges = {
            "harsh_braking_threshold": (5, 30),
            "aggressive_launch_throttle": (50, 98),
            "aggressive_launch_speed": (10, 50),
            "cold_engine_rpm": (1500, 5000),
            "cold_engine_temp": (40, 90),
            "engine_lugging_load": (50, 98),
            "engine_lugging_rpm": (800, 2500),
            "excessive_idling_time": (60, 600),
            "excessive_idling_rpm": (300, 1000),
            "speeding_threshold": (60, 160),
        }
        lo, hi = ranges.get(param, (0, 10000))
        return max(lo, min(hi, value))
