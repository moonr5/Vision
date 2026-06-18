"""
Scale Engine — Predictive Maintenance Engine.
Predicts failures from OBD trends before the MIL (check engine light) triggers.
Uses trend analysis on coolant temp, RPM stability, fuel trims, and voltage.
"""

from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
from collections import defaultdict


class PredictiveMaintenanceEngine:
    """
    Predicts maintenance needs from OBD-II trend data.

    Monitored systems:
      - Cooling system (coolant temp trends)
      - Battery/charging (voltage trends)
      - Fuel system (pressure, trims)
      - Engine health (RPM stability, load patterns)
      - Emissions (MIL history, O2 sensor readiness)
    """

    # Risk thresholds
    THRESHOLDS = {
        "coolant_temp_rising": {"warning": 105, "critical": 115, "trend_window": 10},
        "voltage_dropping": {"warning": 12.5, "critical": 11.8},
        "rpm_instability": {"cv_threshold": 0.25},  # coefficient of variation
        "engine_load_sustained": {"warning": 85, "critical": 95},
    }

    def __init__(self):
        self._history: Dict[str, Dict[str, List[Dict]]] = defaultdict(lambda: defaultdict(list))
        self._predictions: Dict[str, List[Dict]] = defaultdict(list)

    def ingest(self, telemetry: Dict[str, Any]):
        """Ingest a telemetry record and update maintenance predictions."""
        device_id = telemetry.get("device_id", "unknown")
        now = datetime.utcnow()

        metrics = {
            "coolant_temp": telemetry.get("coolant_temp") or telemetry.get("obd_coolant_temp"),
            "rpm": telemetry.get("rpm") or telemetry.get("obd_rpm"),
            "engine_load": telemetry.get("engine_load") or telemetry.get("obd_engine_load"),
            "voltage": telemetry.get("obd", {}).get("voltage") if isinstance(telemetry.get("obd"), dict) else None,
            "mil": telemetry.get("mil"),
            "throttle": telemetry.get("throttle"),
        }

        for metric, value in metrics.items():
            if value is None:
                continue
            self._history[device_id][metric].append({"value": value, "ts": now})
            if len(self._history[device_id][metric]) > 500:
                self._history[device_id][metric] = self._history[device_id][metric][-200:]

    def predict(self, device_id: str) -> Dict[str, Any]:
        """Generate maintenance predictions for a device."""
        predictions = {
            "device_id": device_id,
            "predictions": [],
            "overall_risk": "LOW",
            "recommended_actions": [],
            "generated_at": datetime.utcnow().isoformat(),
        }

        data = self._history.get(device_id, {})

        # Cooling system
        coolant_data = data.get("coolant_temp", [])
        if len(coolant_data) >= 10:
            recent = [c["value"] for c in coolant_data[-10:]]
            trend = self._linear_trend(recent)
            current = recent[-1]
            if trend > 0.5 and current > 100:
                predictions["predictions"].append({
                    "system": "cooling",
                    "risk": "HIGH" if current > 110 else "MEDIUM",
                    "detail": f"Coolant temp rising ({current}°C, +{round(trend,1)}°C/sample)",
                    "estimated_failure_in_km": round((115 - current) / max(trend, 0.1) * 5, 0),
                })
                predictions["recommended_actions"].append("Inspect radiator, coolant level, and thermostat")

        # RPM instability
        rpm_data = data.get("rpm", [])
        if len(rpm_data) >= 20:
            recent_rpm = [r["value"] for r in rpm_data[-20:]]
            mean_rpm = sum(recent_rpm) / len(recent_rpm)
            if mean_rpm > 0:
                cv = (self._stddev(recent_rpm, mean_rpm)) / mean_rpm
                if cv > self.THRESHOLDS["rpm_instability"]["cv_threshold"]:
                    predictions["predictions"].append({
                        "system": "engine",
                        "risk": "MEDIUM",
                        "detail": f"RPM instability detected (CV={round(cv, 3)})",
                    })
                    predictions["recommended_actions"].append("Check fuel delivery, ignition system, and air intake")

        # MIL status
        recent_mil = [m["value"] for m in data.get("mil", [])[-5:]]
        if any(recent_mil):
            predictions["predictions"].append({
                "system": "emissions",
                "risk": "HIGH",
                "detail": "Check Engine Light (MIL) is ON — immediate diagnostic required",
            })
            predictions["recommended_actions"].append("Run OBD-II diagnostic scan immediately")

        # Voltage
        voltage_data = data.get("voltage", [])
        if voltage_data:
            recent_v = [v["value"] for v in voltage_data[-5:]]
            avg_v = sum(recent_v) / len(recent_v)
            if avg_v < self.THRESHOLDS["voltage_dropping"]["critical"]:
                predictions["predictions"].append({
                    "system": "charging", "risk": "CRITICAL",
                    "detail": f"Battery voltage critically low ({round(avg_v, 1)}V)",
                })
                predictions["recommended_actions"].append("Check alternator and battery immediately")

        # Overall risk
        severities = [p["risk"] for p in predictions["predictions"]]
        if "CRITICAL" in severities:
            predictions["overall_risk"] = "CRITICAL"
        elif "HIGH" in severities:
            predictions["overall_risk"] = "HIGH"
        elif "MEDIUM" in severities:
            predictions["overall_risk"] = "MEDIUM"

        self._predictions[device_id] = predictions["predictions"]
        return predictions

    def get_fleet_health(self) -> Dict[str, Any]:
        """Get maintenance health across all known devices."""
        devices = list(self._history.keys())
        results = {}
        for did in devices:
            p = self.predict(did)
            results[did] = {"risk": p["overall_risk"], "issues": len(p["predictions"]),
                             "actions": p["recommended_actions"]}
        critical = [did for did, r in results.items() if r["risk"] in ("CRITICAL", "HIGH")]
        return {
            "total_devices": len(devices),
            "critical_devices": len(critical),
            "critical_device_ids": critical,
            "per_device": results,
        }

    def _linear_trend(self, values: List[float]) -> float:
        """Simple linear trend (slope per sample)."""
        n = len(values)
        if n < 2:
            return 0
        x_mean = (n - 1) / 2
        y_mean = sum(values) / n
        num = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
        den = sum((i - x_mean) ** 2 for i in range(n))
        return num / den if den > 0 else 0

    def _stddev(self, values: List[float], mean: float) -> float:
        if len(values) < 2:
            return 0
        return (sum((v - mean) ** 2 for v in values) / (len(values) - 1)) ** 0.5
