"""
Scale Engine — MLOps Stack.
Versioning, A/B testing, drift detection, rollback for AI models.
"""

import hashlib
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
from collections import defaultdict
from enum import Enum


class ModelStatus(Enum):
    ACTIVE = "active"
    SHADOW = "shadow"        # Deployed alongside active, not serving traffic
    CHALLENGER = "challenger"  # A/B test candidate
    ARCHIVED = "archived"
    FAILED = "failed"


class MLOpsManager:
    """
    MLOps for fleet AI models.

    Capabilities:
      - Model registry with versioning
      - A/B test deployment
      - Drift detection on input features
      - Performance monitoring
      - Automated rollback on degradation
    """

    def __init__(self):
        self._registry: Dict[str, Dict[str, Dict]] = defaultdict(dict)  # model_name → version → model
        self._deployments: Dict[str, Dict] = {}  # model_name → deployment config
        self._metrics: Dict[str, List[Dict]] = defaultdict(list)
        self._drift_alerts: List[Dict] = []

    # ── Registry ─────────────────────────────────────────────────────────

    def register(self, model: Dict[str, Any]):
        """Register a model version."""
        name = model["name"]
        version = model.get("version", "v1")
        model["status"] = ModelStatus.ACTIVE.value
        model["registered_at"] = datetime.utcnow().isoformat()
        self._registry[name][version] = model
        self._deployments[name] = {"active_version": version, "shadow_versions": [], "challenger_version": None}

    def get_active(self, model_name: str) -> Optional[Dict]:
        """Get the currently active model version."""
        dep = self._deployments.get(model_name, {})
        version = dep.get("active_version", "v1")
        return self._registry.get(model_name, {}).get(version)

    def list_versions(self, model_name: str) -> List[Dict]:
        """List all versions of a model."""
        return [
            {"version": v, "status": m.get("status"), "registered_at": m.get("registered_at")}
            for v, m in self._registry.get(model_name, {}).items()
        ]

    # ── A/B Testing ──────────────────────────────────────────────────────

    def deploy_challenger(self, model_name: str, version: str, traffic_split: float = 0.1):
        """
        Deploy a challenger model for A/B testing.
        traffic_split: fraction of predictions routed to challenger (0.0-1.0)
        """
        if model_name not in self._registry:
            return {"error": f"Model {model_name} not found"}
        if version not in self._registry[model_name]:
            return {"error": f"Version {version} not found"}

        self._registry[model_name][version]["status"] = ModelStatus.CHALLENGER.value
        self._deployments[model_name]["challenger_version"] = version
        self._deployments[model_name]["traffic_split"] = traffic_split

        return {
            "model": model_name,
            "active": self._deployments[model_name]["active_version"],
            "challenger": version,
            "traffic_split": traffic_split,
        }

    def promote_challenger(self, model_name: str):
        """Promote challenger to active, archive old active."""
        dep = self._deployments.get(model_name)
        if not dep or not dep.get("challenger_version"):
            return {"error": "No challenger deployed"}

        old_active = dep["active_version"]
        new_active = dep["challenger_version"]

        self._registry[model_name][old_active]["status"] = ModelStatus.ARCHIVED.value
        self._registry[model_name][new_active]["status"] = ModelStatus.ACTIVE.value

        dep["active_version"] = new_active
        dep["challenger_version"] = None
        dep["traffic_split"] = 0

        return {"model": model_name, "promoted": new_active, "archived": old_active}

    def rollback(self, model_name: str) -> Dict:
        """Rollback to the previous active version."""
        versions = list(self._registry.get(model_name, {}).keys())
        archived = [v for v in versions if self._registry[model_name][v].get("status") == "archived"]
        if not archived:
            return {"error": "No archived version to rollback to"}

        rollback_to = archived[-1]
        current = self._deployments[model_name]["active_version"]
        self._registry[model_name][rollback_to]["status"] = ModelStatus.ACTIVE.value
        self._registry[model_name][current]["status"] = ModelStatus.FAILED.value
        self._deployments[model_name]["active_version"] = rollback_to

        return {"model": model_name, "rolled_back_to": rollback_to, "failed": current}

    # ── Drift detection ──────────────────────────────────────────────────

    def detect_drift(
        self, model_name: str, current_features: List[float],
        baseline_features: List[float],
    ) -> Dict[str, Any]:
        """
        Detect feature drift using simple distribution comparison.
        Returns drift score and alert if significant.
        """
        if len(current_features) != len(baseline_features):
            return {"drift_detected": False, "error": "Feature dimension mismatch"}

        # Compute per-feature mean shift
        drift_scores = []
        for curr, base in zip(current_features, baseline_features):
            if base != 0:
                drift = abs(curr - base) / (abs(base) + 0.001)
                drift_scores.append(drift)
            else:
                drift_scores.append(0)

        max_drift = max(drift_scores) if drift_scores else 0

        alert = max_drift > 0.3
        if alert:
            self._drift_alerts.append({
                "model": model_name, "max_drift": max_drift,
                "detected_at": datetime.utcnow().isoformat(),
            })

        return {
            "model": model_name,
            "drift_detected": alert,
            "max_feature_drift": round(max_drift, 4),
            "per_feature_drift": [round(d, 4) for d in drift_scores],
            "recommendation": "Retrain model" if alert else "No action needed",
        }

    # ── Monitoring ───────────────────────────────────────────────────────

    def record_metric(self, model_name: str, metric: str, value: float):
        """Record a performance metric for a model."""
        self._metrics[model_name].append({
            "metric": metric, "value": value,
            "timestamp": datetime.utcnow().isoformat(),
        })

    def get_model_health(self, model_name: str) -> Dict[str, Any]:
        """Get health report for a model."""
        metrics = self._metrics.get(model_name, [])
        if not metrics:
            return {"model": model_name, "status": "unknown", "note": "No metrics recorded"}

        recent = [m for m in metrics if (datetime.utcnow() - datetime.fromisoformat(m["timestamp"].replace("Z", "+00:00"))).total_seconds() < 86400]
        by_metric = defaultdict(list)
        for m in recent:
            by_metric[m["metric"]].append(m["value"])

        health = {"model": model_name, "metrics": {}}
        for metric, values in by_metric.items():
            health["metrics"][metric] = {
                "avg": round(sum(values) / len(values), 4),
                "min": round(min(values), 4),
                "max": round(max(values), 4),
                "count": len(values),
            }
        return health

    def get_drift_alerts(self, limit: int = 20) -> List[Dict]:
        return self._drift_alerts[-limit:]
