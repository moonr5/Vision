"""
Scale Engine — Signal Fusion Layer.
Combines edge alerts, cloud history, and external data into one
unified decision stream for downstream consumers.
"""

from typing import Dict, Any, List, Optional, Callable
from datetime import datetime, timedelta
from collections import defaultdict
from enum import Enum


class SignalSource(Enum):
    EDGE = "edge"            # ESP32 direct
    CLOUD_CEP = "cloud_cep"  # Complex Event Processing
    ANOMALY = "anomaly"      # Anomaly Detection
    BEHAVIOR = "behavior"    # Behavior Inference
    MAINTENANCE = "maintenance"  # Predictive Maintenance
    EXTERNAL = "external"    # Traffic, weather, etc.


class SignalPriority(Enum):
    CRITICAL = 1
    HIGH = 2
    MEDIUM = 3
    LOW = 4
    INFO = 5


class SignalFusionEngine:
    """
    Fuses signals from multiple sources into a unified decision stream.

    Design:
      - Each source publishes signals independently
      - Fusion engine deduplicates, correlates, and prioritizes
      - Output is a single ranked stream of actionable decisions
      - Conflicts are resolved by source trust weights
    """

    # Source trust weights (0-1, higher = more trusted)
    SOURCE_WEIGHTS = {
        SignalSource.EDGE: 0.95,         # ESP32 is ground truth
        SignalSource.CLOUD_CEP: 0.85,    # CEP is verified rules
        SignalSource.ANOMALY: 0.70,      # Statistical — may have false positives
        SignalSource.BEHAVIOR: 0.75,
        SignalSource.MAINTENANCE: 0.80,
        SignalSource.EXTERNAL: 0.60,     # Third-party data — least trusted
    }

    def __init__(self):
        self._signals: List[Dict] = []
        self._fused_decisions: List[Dict] = []
        self._subscribers: List[Callable] = []
        self._correlation_window = timedelta(minutes=5)

    # ── Signal ingestion ─────────────────────────────────────────────────

    def ingest(self, source: SignalSource, signal_type: str,
               payload: Dict[str, Any], priority: SignalPriority = SignalPriority.MEDIUM):
        """Ingest a signal from any source."""
        signal = {
            "id": len(self._signals) + 1,
            "source": source.value,
            "type": signal_type,
            "payload": payload,
            "priority": priority.value,
            "trust_weight": self.SOURCE_WEIGHTS.get(source, 0.5),
            "timestamp": datetime.utcnow().isoformat(),
            "device_id": payload.get("device_id", ""),
            "driver_id": payload.get("driver_id", ""),
        }
        self._signals.append(signal)
        if len(self._signals) > 10000:
            self._signals = self._signals[-5000:]

        # Fuse with recent related signals
        fused = self._fuse(signal)
        if fused:
            self._fused_decisions.append(fused)
            if len(self._fused_decisions) > 2000:
                self._fused_decisions = self._fused_decisions[-1000:]

            # Notify subscribers
            for sub in self._subscribers:
                try:
                    sub(fused)
                except Exception:
                    pass

    # ── Fusion logic ─────────────────────────────────────────────────────

    def _fuse(self, new_signal: Dict) -> Optional[Dict]:
        """
        Correlate new signal with recent related signals.
        If multiple sources report the same issue, confidence increases.
        """
        now = datetime.utcnow()
        device_id = new_signal.get("device_id", "")

        # Find related signals within correlation window
        related = []
        for s in self._signals[-200:]:
            if s["id"] == new_signal["id"]:
                continue
            try:
                s_ts = datetime.fromisoformat(s["timestamp"].replace("Z", "+00:00"))
                if (now - s_ts) > self._correlation_window:
                    continue
            except Exception:
                continue

            # Same device + similar type = correlated
            if s.get("device_id") == device_id and self._types_related(new_signal["type"], s["type"]):
                related.append(s)

        if not related:
            # Single-source signal — lower confidence
            return {
                **new_signal,
                "confidence": new_signal["trust_weight"],
                "corroborated_by": 0,
                "corroborating_sources": [],
                "decision": self._make_decision(new_signal, 1),
            }

        # Multi-source corroboration
        sources = list(set(s["source"] for s in related)) + [new_signal["source"]]
        confidence = min(1.0, sum(self.SOURCE_WEIGHTS.get(SignalSource(s), 0.5) for s in sources) / 3)

        return {
            **new_signal,
            "confidence": round(confidence, 2),
            "corroborated_by": len(related),
            "corroborating_sources": list(set(sources)),
            "decision": self._make_decision(new_signal, len(related) + 1),
        }

    def _make_decision(self, signal: Dict, corroboration_count: int) -> str:
        """Generate an actionable decision from a fused signal."""
        p = signal["priority"]
        conf = signal.get("confidence", signal["trust_weight"])

        if p <= SignalPriority.CRITICAL.value and corroboration_count >= 2:
            return "IMMEDIATE_ACTION_REQUIRED"
        elif p <= SignalPriority.HIGH.value and corroboration_count >= 1:
            return "ESCALATE_TO_DISPATCH"
        elif p <= SignalPriority.MEDIUM.value:
            return "LOG_AND_MONITOR" if corroboration_count >= 2 else "LOG_ONLY"
        else:
            return "RECORD_FOR_ANALYTICS"

    def _types_related(self, t1: str, t2: str) -> bool:
        """Check if two signal types are semantically related."""
        groups = [
            {"fuel_theft", "fuel", "fuel_level_drop", "cap_open"},
            {"security", "breach", "tamper", "mag", "door"},
            {"speed", "speeding", "harsh_braking", "aggressive"},
            {"temperature", "overheat", "coolant", "engine_temp"},
            {"maintenance", "mil", "check_engine", "service"},
            {"geofence", "route_deviation", "zone", "corridor"},
        ]
        for group in groups:
            if any(g in t1.lower() for g in group) and any(g in t2.lower() for g in group):
                return True
        return False

    # ── Query ────────────────────────────────────────────────────────────

    def get_fused_decisions(self, limit: int = 50, min_confidence: float = 0.0) -> List[Dict]:
        """Get recent fused decisions, optionally filtered by confidence."""
        decisions = [d for d in self._fused_decisions if d.get("confidence", 0) >= min_confidence]
        return decisions[-limit:]

    def get_device_decisions(self, device_id: str, limit: int = 20) -> List[Dict]:
        """Get recent decisions for a specific device."""
        return [d for d in self._fused_decisions if d.get("device_id") == device_id][-limit:]

    def get_decision_stats(self) -> Dict[str, Any]:
        """Aggregate statistics on fused decisions."""
        decisions = self._fused_decisions[-500:]
        by_source = defaultdict(int)
        by_decision = defaultdict(int)
        by_confidence = {"high": 0, "medium": 0, "low": 0}
        for d in decisions:
            by_source[d.get("source", "?")] += 1
            by_decision[d.get("decision", "?")] += 1
            conf = d.get("confidence", 0)
            if conf >= 0.8:
                by_confidence["high"] += 1
            elif conf >= 0.5:
                by_confidence["medium"] += 1
            else:
                by_confidence["low"] += 1
        return {
            "total_signals": len(self._signals),
            "fused_decisions": len(self._fused_decisions),
            "by_source": dict(by_source),
            "by_decision": dict(by_decision),
            "by_confidence": by_confidence,
        }

    def subscribe(self, callback: Callable):
        self._subscribers.append(callback)
