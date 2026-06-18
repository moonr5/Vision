"""
Scale Engine — Model Training Pipeline.
Continuous learning from telemetry, events, and outcomes.
Supports periodic retraining, incremental updates, and evaluation.
"""

import json
import hashlib
from typing import Dict, Any, List, Optional, Callable, Tuple
from datetime import datetime, timedelta
from collections import defaultdict
import math


class ModelTrainer:
    """
    Simplified model training pipeline for fleet ML.

    Models trained:
      - driver_risk_classifier: predicts high-risk drivers
      - fuel_consumption_regressor: predicts fuel use for route+driver
      - maintenance_risk_scorer: predicts near-term maintenance needs
      - route_safety_scorer: predicts safety risk for route+driver

    Uses simple online learning (SGD-like) — production would use
    scikit-learn / XGBoost / PyTorch.
    """

    def __init__(self):
        self._models: Dict[str, Dict[str, Any]] = {}
        self._training_history: List[Dict] = []

    # ── Model definitions ────────────────────────────────────────────────

    def train_driver_risk_classifier(
        self, drivers: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Train a simple logistic regression for driver risk classification.
        Features: safety_score, event_rates, experience_level
        Target: risk_category (high/medium/low)
        """
        features_list = []
        labels = []

        for d in drivers:
            safety = d.get("safety_score", 80) or 80
            events = d.get("total_events", 0) or 0
            speeding = d.get("speeding_count", 0) or 0
            harsh = d.get("harsh_braking_count", 0) or 0

            features_list.append([safety / 100, min(events / 50, 1.0), min(speeding / 10, 1.0), min(harsh / 10, 1.0)])
            risk = 0 if safety >= 80 else (1 if safety >= 60 else 2)  # 0=low, 1=med, 2=high
            labels.append(risk)

        if not features_list:
            return {"error": "No training data"}

        # Simple averaged weight model
        weights = [0.0] * len(features_list[0])
        n = len(features_list)
        lr = 0.01

        for epoch in range(50):
            for features, label in zip(features_list, labels):
                # Softmax prediction
                scores = [sum(w * f for w, f in zip(weights, features)) for _ in range(3)]
                # Simplified: direct weight update toward correct class
                for i in range(len(weights)):
                    weights[i] += lr * (features[i] * (1 if label == 0 else -0.3))

        accuracy = self._evaluate_classifier(features_list, labels, weights)

        model = {
            "name": "driver_risk_classifier",
            "type": "logistic_regression",
            "weights": weights,
            "feature_names": ["safety_normalized", "event_rate", "speeding_rate", "harsh_braking_rate"],
            "accuracy": accuracy,
            "trained_at": datetime.utcnow().isoformat(),
            "training_samples": n,
            "version": hashlib.md5(str(weights).encode()).hexdigest()[:8],
        }

        self._models["driver_risk_classifier"] = model
        self._training_history.append({"model": "driver_risk_classifier", "accuracy": accuracy, "samples": n})
        return model

    def train_fuel_consumption_regressor(
        self, trips: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Simple linear regression for fuel consumption prediction.
        Features: distance_km, avg_speed, driver_pace_factor, vehicle_weight
        Target: fuel_consumed_liters
        """
        if not trips:
            return {"error": "No trip data"}

        # Compute coefficients via simple OLS
        X, y = [], []
        for t in trips:
            dist = t.get("total_distance_km", 50) or 50
            speed = t.get("avg_speed", 45) or 45
            fuel = t.get("fuel_consumed", dist / 8) or dist / 8
            X.append([1.0, dist, 1.0 / max(speed, 1), speed])
            y.append(fuel)

        coef = self._simple_ols(X, y)
        mae = sum(abs(self._predict(X[i], coef) - y[i]) for i in range(len(y))) / max(len(y), 1)

        model = {
            "name": "fuel_consumption_regressor",
            "type": "linear_regression",
            "coefficients": coef,
            "intercept": coef[0],
            "feature_names": ["intercept", "distance_km", "inverse_speed", "speed"],
            "mae_liters": round(mae, 2),
            "trained_at": datetime.utcnow().isoformat(),
            "training_samples": len(trips),
            "version": hashlib.md5(str(coef).encode()).hexdigest()[:8],
        }

        self._models["fuel_consumption_regressor"] = model
        self._training_history.append({"model": "fuel_consumption_regressor", "mae": round(mae, 2), "samples": len(trips)})
        return model

    def train_maintenance_risk_scorer(
        self, telemetry_history: List[Dict],
    ) -> Dict[str, Any]:
        """Score maintenance risk based on OBD trends."""
        # Simplified: use thresholds from predictive maintenance engine
        model = {
            "name": "maintenance_risk_scorer",
            "type": "rule_based",
            "rules": {
                "coolant_temp_critical": 115,
                "coolant_temp_warning": 105,
                "voltage_critical": 11.8,
                "voltage_warning": 12.5,
                "rpm_cv_threshold": 0.25,
            },
            "trained_at": datetime.utcnow().isoformat(),
            "version": "1.0.0",
        }
        self._models["maintenance_risk_scorer"] = model
        return model

    # ── Model serving (inference) ────────────────────────────────────────

    def predict(self, model_name: str, features: List[float]) -> float:
        """Run inference with a trained model."""
        model = self._models.get(model_name)
        if not model:
            return 0.0

        if model["type"] == "logistic_regression":
            return sum(w * f for w, f in zip(model["weights"], features))
        elif model["type"] == "linear_regression":
            return self._predict(features, model["coefficients"])
        return 0.0

    def get_models(self) -> List[Dict[str, Any]]:
        """List all trained models."""
        return [{"name": m["name"], "type": m["type"], "version": m.get("version", "?"),
                 "trained_at": m.get("trained_at", "?")} for m in self._models.values()]

    # ── Helpers ──────────────────────────────────────────────────────────

    def _evaluate_classifier(self, X: List, y: List, weights: List) -> float:
        correct = 0
        for features, label in zip(X, y):
            score = sum(w * f for w, f in zip(weights, features))
            pred = 0 if score > 0.5 else (1 if score > 0 else 2)
            if pred == label:
                correct += 1
        return round(correct / max(len(y), 1), 3)

    def _simple_ols(self, X: List[List[float]], y: List[float]) -> List[float]:
        """Simple OLS regression (not numerically stable for production)."""
        n_features = len(X[0])
        coef = [0.0] * n_features
        lr = 0.001
        for _ in range(200):
            for i in range(len(X)):
                pred = self._predict(X[i], coef)
                error = pred - y[i]
                for j in range(n_features):
                    coef[j] -= lr * error * X[i][j]
        return coef

    def _predict(self, features: List[float], coef: List[float]) -> float:
        return sum(c * f for c, f in zip(coef, features))
