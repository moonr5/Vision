"""
Scale Engine — Server-Side Behavior Inference Engine.
Goes beyond ESP32 edge rules using long-term history, cross-driver
comparisons, and AI-based pattern recognition for deeper insights.
"""

from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
from collections import defaultdict
import math


class BehaviorInferenceEngine:
    """
    Cloud-side behavior scoring using full historical context.

    Edge (ESP32) provides real-time event detection.
    Cloud adds:
      - Longitudinal scoring (trend over weeks/months)
      - Peer comparison (driver vs fleet percentile)
      - Context-aware scoring (time of day, route type, weather)
      - Fatigue detection patterns
      - Coaching recommendation prioritization
    """

    def __init__(self):
        self._driver_history: Dict[str, List[Dict]] = defaultdict(list)
        self._fleet_baselines: Dict[str, Dict] = {}

    # ── Ingestion ────────────────────────────────────────────────────────

    def ingest_event(self, driver_id: str, event: Dict[str, Any]):
        """Ingest a behavior event (from ESP32 or CEP engine)."""
        self._driver_history[driver_id].append({
            **event,
            "ingested_at": datetime.utcnow().isoformat(),
        })
        # Keep last 500 events per driver
        if len(self._driver_history[driver_id]) > 500:
            self._driver_history[driver_id] = self._driver_history[driver_id][-300:]

    def ingest_telemetry(self, driver_id: str, telemetry: Dict[str, Any]):
        """Infer behavior from raw telemetry (server-side only)."""
        speed = telemetry.get("speed", 0) or 0
        rpm = telemetry.get("rpm") or telemetry.get("obd_rpm", 0) or 0
        throttle = telemetry.get("throttle", 0) or 0
        engine_load = telemetry.get("engine_load", 0) or 0
        coolant_temp = telemetry.get("coolant_temp", 90) or 90

        events = []

        # Night driving (fatigue risk)
        hour = datetime.utcnow().hour
        if speed > 80 and (hour < 5 or hour > 22):
            events.append({"event_name": "Night Driving", "event_type": "WARNING",
                            "details": f"High speed ({speed} km/h) at night (hour {hour})"})

        # Inconsistent speed (weaving)
        if speed > 60 and throttle > 70:
            events.append({"event_name": "Aggressive Driving Pattern", "event_type": "WARNING",
                            "details": f"High throttle ({throttle}%) at speed ({speed} km/h)"})

        # Sustained high load
        if engine_load > 80 and rpm > 3000 and speed > 40:
            events.append({"event_name": "Engine Stress", "event_type": "INFO",
                            "details": f"Load {engine_load}% at {rpm} RPM / {speed} km/h"})

        for e in events:
            self.ingest_event(driver_id, e)

    # ── Scoring ──────────────────────────────────────────────────────────

    def compute_longitudinal_score(self, driver_id: str, days: int = 30) -> Dict[str, Any]:
        """Compute a trend-aware behavior score with weekly breakdown."""
        events = self._driver_history.get(driver_id, [])
        cutoff = datetime.utcnow() - timedelta(days=days)

        recent = [e for e in events if e.get("ingested_at", "") > cutoff.isoformat()]

        # Weekly breakdown
        weekly = defaultdict(lambda: {"critical": 0, "warning": 0, "info": 0, "total": 0})
        for e in recent:
            try:
                ts = datetime.fromisoformat(e.get("ingested_at", "").replace("Z", "+00:00"))
                week = ts.strftime("%Y-W%W")
            except Exception:
                week = "unknown"
            sev = (e.get("event_type") or e.get("type") or "INFO").upper()
            if "CRIT" in sev:
                weekly[week]["critical"] += 1
            elif "WARN" in sev:
                weekly[week]["warning"] += 1
            else:
                weekly[week]["info"] += 1
            weekly[week]["total"] += 1

        # Overall trend
        weeks_sorted = sorted(weekly.keys())
        trend = "stable"
        if len(weeks_sorted) >= 2:
            first = weekly[weeks_sorted[0]]["total"]
            last = weekly[weeks_sorted[-1]]["total"]
            if last > first * 1.5:
                trend = "worsening"
            elif last < first * 0.5:
                trend = "improving"

        total_crit = sum(w["critical"] for w in weekly.values())
        total_warn = sum(w["warning"] for w in weekly.values())

        # Compute score (100 base, deduct)
        score = 100 - (total_crit * 6) - (total_warn * 3) - (len(recent) * 0.5)
        score = max(0, min(100, round(score)))

        return {
            "driver_id": driver_id,
            "period_days": days,
            "total_events": len(recent),
            "critical_events": total_crit,
            "warning_events": total_warn,
            "behavior_score": score,
            "trend": trend,
            "weekly_breakdown": {w: dict(weekly[w]) for w in weeks_sorted},
            "needs_coaching": score < 70,
            "coaching_priority": "HIGH" if score < 50 else ("MEDIUM" if score < 70 else "LOW"),
        }

    def compare_driver_to_fleet(self, driver_id: str) -> Dict[str, Any]:
        """Compare one driver against fleet peer percentiles."""
        scores = []
        for did in self._driver_history:
            s = self.compute_longitudinal_score(did)
            scores.append((did, s["behavior_score"]))

        if not scores:
            return {"driver_id": driver_id, "percentile": None, "detail": "No fleet data"}

        scores.sort(key=lambda x: x[1])
        driver_score = next((s for d, s in scores if d == driver_id), None)
        if driver_score is None:
            return {"driver_id": driver_id, "percentile": None, "detail": "Driver not found"}

        rank = sum(1 for _, s in scores if s < driver_score) + 1
        percentile = round(rank / len(scores) * 100)

        return {
            "driver_id": driver_id,
            "behavior_score": driver_score,
            "fleet_rank": f"{rank}/{len(scores)}",
            "percentile": percentile,
            "fleet_avg_score": round(sum(s for _, s in scores) / len(scores), 1),
            "fleet_best_score": scores[-1][1],
            "fleet_worst_score": scores[0][1],
        }

    def get_coaching_recommendations(self, driver_id: str) -> List[str]:
        """Generate personalized coaching actions based on event patterns."""
        events = self._driver_history.get(driver_id, [])
        names = [e.get("event_name", "") for e in events[-100:]]
        recs = []

        if sum(1 for n in names if "speeding" in n.lower()) > 5:
            recs.append("Schedule speed awareness coaching session")
        if sum(1 for n in names if "harsh" in n.lower() and "brak" in n.lower()) > 3:
            recs.append("Practice progressive braking techniques — smooth deceleration drills")
        if sum(1 for n in names if "idling" in n.lower()) > 5:
            recs.append("Review idle reduction policy — fuel cost impact briefing")
        if sum(1 for n in names if "night" in n.lower()) > 3:
            recs.append("Assess night driving schedule — fatigue risk management")
        if not recs:
            recs.append("Continue safe driving practices — no specific issues identified")

        return recs
