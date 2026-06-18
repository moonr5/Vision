"""
Scale Engine — Data Quality Pipeline.
Detects missing GPS, OBD drift, duplicate packets, corrupt payloads,
stale sensors, and timestamp anomalies. Emits quality scores per device.
"""

import asyncio
from typing import Dict, Any, List, Optional, Set
from datetime import datetime, timedelta
from collections import defaultdict

from scale_engine import db


class DataQualityPipeline:
    """
    Continuous data quality monitoring for all telemetry streams.
    Each check runs independently; results are aggregated into a quality score (0-100).
    """

    CHECKS = [
        "missing_gps",
        "gps_zero_while_moving",
        "obd_speed_drift",
        "duplicate_packets",
        "stale_device",
        "sensor_stuck",
        "timestamp_future",
        "fuel_level_jump",
        "rpm_zero_while_moving",
    ]

    def __init__(self):
        self._check_results: Dict[str, List[Dict]] = defaultdict(list)
        self._device_scores: Dict[str, float] = {}

    async def run_all_checks(self) -> Dict[str, Any]:
        """Run all quality checks and return a comprehensive report."""
        issues = await db.detect_quality_issues()

        report = {
            "overall_quality_score": 100,
            "checks_run": self.CHECKS,
            "issues": issues,
            "devices_affected": len(set(i.get("device_id", "") for i in issues if i.get("device_id"))),
            "recommendations": [],
            "run_at": datetime.utcnow().isoformat(),
        }

        # Deduct from quality score
        severity_deductions = {"CRITICAL": 10, "WARNING": 5, "INFO": 2}
        for issue in issues:
            report["overall_quality_score"] -= severity_deductions.get(issue.get("severity", "INFO"), 2)

        report["overall_quality_score"] = max(0, report["overall_quality_score"])

        # Recommendations
        for issue in issues:
            t = issue.get("type", "")
            if t == "missing_gps_engine_on":
                report["recommendations"].append("Check GPS antenna connections on devices with missing GPS while engine running")
            elif t == "speed_drift":
                report["recommendations"].append("Calibrate OBD speed sensors — significant drift detected vs GPS")
            elif t == "stale_device":
                report["recommendations"].append(f"Device {issue.get('name', '?')} is stale — investigate connectivity")
            elif t == "duplicate_packets":
                report["recommendations"].append("Investigate MQTT QoS settings — duplicate packets detected")

        self._check_results["latest"] = issues
        return report

    async def score_device(self, device_id: str, hours: int = 24) -> Dict[str, Any]:
        """Compute a quality score (0-100) for a single device."""
        if not db.available():
            return {"device_id": device_id, "score": None, "reason": "DB unavailable"}

        score = 100
        deductions = []

        async with db._pool.acquire() as conn:
            # Check GPS availability
            gps = await conn.fetchrow(
                """SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE lat IS NULL OR lat = 0) AS missing
                   FROM telemetry WHERE device_id = $1
                   AND timestamp > NOW() - ($2 || ' hours')::INTERVAL""",
                device_id, str(hours),
            )
            if gps and gps["total"] > 0:
                missing_pct = gps["missing"] / gps["total"]
                if missing_pct > 0.5:
                    score -= 30
                    deductions.append(f"GPS missing {missing_pct*100:.0f}% of the time")
                elif missing_pct > 0.1:
                    score -= 10
                    deductions.append(f"GPS missing {missing_pct*100:.0f}% of the time")

            # Check sensor health
            sensors = await conn.fetchrow(
                """SELECT AVG(sensor_s1) AS s1_avg, AVG(sensor_s2) AS s2_avg,
                   AVG(sensor_mag1) AS mag1_avg, AVG(sensor_mag2) AS mag2_avg
                   FROM telemetry WHERE device_id = $1
                   AND timestamp > NOW() - ($2 || ' hours')::INTERVAL""",
                device_id, str(hours),
            )
            for sensor, avg in [("S1", sensors["s1_avg"]), ("S2", sensors["s2_avg"]), ("MAG1", sensors["mag1_avg"]), ("MAG2", sensors["mag2_avg"])]:
                if avg is not None and avg == 0:
                    score -= 10
                    deductions.append(f"Sensor {sensor} stuck at 0 (alert state)")

        self._device_scores[device_id] = max(0, score)
        return {"device_id": device_id, "quality_score": max(0, score), "deductions": deductions}

    async def get_fleet_quality(self) -> Dict[str, Any]:
        """Get quality scores for all active devices."""
        if not db.available():
            return {"fleet_score": None, "devices": []}

        async with db._pool.acquire() as conn:
            rows = await conn.fetch("SELECT id, name FROM devices WHERE status = 'online'")

        scores = []
        for r in rows:
            s = await self.score_device(r["id"])
            scores.append(s)

        fleet_avg = sum(s["quality_score"] for s in scores if s["quality_score"] is not None) / max(len(scores), 1)
        return {"fleet_quality_score": round(fleet_avg, 1), "devices": scores}
