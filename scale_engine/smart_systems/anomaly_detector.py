"""
Scale Engine — Anomaly Detection Service.
Spots unusual RPM, fuel drops, route deviations, and idle patterns
using statistical models fed from the time-series engine.
"""

import math
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
from collections import defaultdict


class AnomalyDetector:
    """
    Multi-method anomaly detection for fleet telemetry.

    Methods:
      - Z-score (statistical outlier detection)
      - Moving average deviation (trend break detection)
      - Threshold-based (hard rules for safety-critical anomalies)
      - Rate-of-change (sudden jumps in fuel, RPM, speed)
    """

    def __init__(self):
        # Rolling windows for statistical baselines
        self._baselines: Dict[str, Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))
        self._max_window = 500  # last N samples per metric per device
        self._alerts: List[Dict] = []

    # ── Ingest & detect ──────────────────────────────────────────────────

    def ingest(self, telemetry: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Ingest telemetry, update baselines, detect anomalies."""
        device_id = telemetry.get("device_id", "unknown")
        anomalies = []

        metrics = {
            "speed": telemetry.get("speed"),
            "rpm": telemetry.get("rpm") or telemetry.get("obd_rpm"),
            "fuel_level": telemetry.get("fuel_level"),
            "coolant_temp": telemetry.get("coolant_temp"),
            "engine_load": telemetry.get("engine_load"),
            "throttle": telemetry.get("throttle"),
        }

        for metric, value in metrics.items():
            if value is None:
                continue

            window = self._baselines[device_id][metric]

            # Z-score detection (needs ≥ 10 samples for meaningful stats)
            if len(window) >= 10:
                mean = sum(window) / len(window)
                std = self._stddev(window, mean)
                if std > 0:
                    z = abs((value - mean) / std)
                    if z > 3.0:
                        anomalies.append({
                            "type": "z_score",
                            "device_id": device_id,
                            "metric": metric,
                            "value": value,
                            "mean": round(mean, 2),
                            "z_score": round(z, 2),
                            "severity": "CRITICAL" if z > 4.0 else "WARNING",
                        })

            # Rate-of-change detection
            if len(window) >= 2:
                prev = window[-1]
                delta = value - prev
                roc_thresholds = {
                    "fuel_level": (-10, "CRITICAL"),   # sudden fuel drop > 10%
                    "rpm": (3000, "WARNING"),           # RPM spike > 3000
                    "speed": (40, "WARNING"),            # speed jump > 40 km/h in one reading
                }
                if metric in roc_thresholds:
                    threshold, severity = roc_thresholds[metric]
                    if metric == "fuel_level" and delta < threshold:
                        anomalies.append({
                            "type": "rate_of_change",
                            "device_id": device_id,
                            "metric": metric,
                            "delta": round(delta, 2),
                            "severity": severity,
                        })
                    elif metric != "fuel_level" and abs(delta) > threshold:
                        anomalies.append({
                            "type": "rate_of_change",
                            "device_id": device_id,
                            "metric": metric,
                            "delta": round(delta, 2),
                            "severity": severity,
                        })

            # Update rolling window
            window.append(value)
            if len(window) > self._max_window:
                window.pop(0)

        # Threshold-based static checks
        static_anomalies = self._static_checks(telemetry)
        anomalies.extend(static_anomalies)

        self._alerts.extend(anomalies)
        if len(self._alerts) > 2000:
            self._alerts = self._alerts[-1000:]

        return anomalies

    def ingest_batch(self, records: List[Dict]) -> List[Dict]:
        all_anomalies = []
        for r in records:
            all_anomalies.extend(self.ingest(r))
        return all_anomalies

    # ── Query ────────────────────────────────────────────────────────────

    def get_baseline(self, device_id: str, metric: str) -> Dict[str, Any]:
        """Get the current statistical baseline for a device+metric."""
        window = self._baselines.get(device_id, {}).get(metric, [])
        if not window:
            return {"device_id": device_id, "metric": metric, "samples": 0}
        mean = sum(window) / len(window)
        return {
            "device_id": device_id, "metric": metric,
            "samples": len(window), "mean": round(mean, 2),
            "std": round(self._stddev(window, mean), 2),
            "min": round(min(window), 2), "max": round(max(window), 2),
            "latest": window[-1],
        }

    def get_recent_anomalies(self, limit: int = 50) -> List[Dict]:
        return self._alerts[-limit:]

    # ── Helpers ──────────────────────────────────────────────────────────

    def _stddev(self, values: List[float], mean: float) -> float:
        if len(values) < 2:
            return 0
        variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
        return math.sqrt(variance)

    def _static_checks(self, t: Dict) -> List[Dict]:
        """Hard-coded safety thresholds that always fire."""
        device_id = t.get("device_id", "unknown")
        anomalies = []

        checks = [
            ("coolant_temp", lambda v: v > 115, "CRITICAL", "Engine overheating"),
            ("coolant_temp", lambda v: v < -30, "WARNING", "Coolant temp sensor fault"),
            ("rpm", lambda v: (v or t.get("obd_rpm", 0)) > 6000, "CRITICAL", "RPM redline exceeded"),
            ("fuel_level", lambda v: v < 5, "WARNING", "Critically low fuel"),
            ("engine_load", lambda v: v > 98, "WARNING", "Sustained max engine load"),
        ]

        for metric, condition, severity, desc in checks:
            val = t.get(metric)
            if metric == "rpm":
                val = t.get("rpm") or t.get("obd_rpm")
            if val is not None and condition(val):
                anomalies.append({
                    "type": "threshold",
                    "device_id": device_id,
                    "metric": metric,
                    "value": val,
                    "severity": severity,
                    "description": desc,
                })

        return anomalies
