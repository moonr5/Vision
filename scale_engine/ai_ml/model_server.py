"""
Scale Engine — Model Serving Layer.
Low-latency inference API for scoring, alerting, and coaching.
Exposes trained models via a unified prediction interface.
"""

import asyncio
from typing import Dict, Any, List, Optional
from datetime import datetime
from collections import defaultdict


class ModelServer:
    """
    Unified model serving layer.

    Models served:
      - Driver risk scoring (real-time)
      - Fuel consumption prediction (per route)
      - Maintenance risk assessment (per device)
      - Route safety scoring (per driver+route pairing)
      - Behavior event likelihood (per driver)

    All predictions include confidence intervals and feature importance
    where available.
    """

    def __init__(self):
        self._models: Dict[str, Any] = {}
        self._inference_count: Dict[str, int] = defaultdict(int)
        self._latency_ms: Dict[str, List[float]] = defaultdict(list)
        self._cache: Dict[str, Dict] = {}  # Simple prediction cache

    # ── Model registration ───────────────────────────────────────────────

    def register_model(self, name: str, model: Dict[str, Any]):
        """Register a trained model for serving."""
        self._models[name] = model

    def get_model(self, name: str) -> Optional[Dict[str, Any]]:
        return self._models.get(name)

    # ── Inference endpoints ──────────────────────────────────────────────

    def predict_driver_risk(self, driver_features: Dict[str, Any]) -> Dict[str, Any]:
        """Predict risk level for a driver."""
        model = self._models.get("driver_risk_classifier")
        if not model:
            return {"risk": "unknown", "confidence": 0, "note": "Model not trained"}

        features = [
            (driver_features.get("safety_score", 80) or 80) / 100,
            min((driver_features.get("total_events", 0) or 0) / 50, 1.0),
            min((driver_features.get("speeding_count", 0) or 0) / 10, 1.0),
            min((driver_features.get("harsh_braking_count", 0) or 0) / 10, 1.0),
        ]

        score = sum(w * f for w, f in zip(model["weights"], features))
        risk = "LOW" if score > 0.5 else ("MEDIUM" if score > 0 else "HIGH")
        confidence = min(abs(score) * 1.5, 1.0)

        self._record_inference("driver_risk")
        return {
            "risk_level": risk,
            "score": round(score, 3),
            "confidence": round(confidence, 2),
            "model_version": model.get("version", "?"),
        }

    def predict_fuel_consumption(self, route: Dict[str, Any], driver: Dict[str, Any] = None) -> Dict[str, Any]:
        """Predict fuel consumption for a route."""
        model = self._models.get("fuel_consumption_regressor")
        if not model:
            dist = route.get("distance_km", 50) or 50
            return {"fuel_estimate_l": round(dist / 8, 1), "confidence": 0.3, "note": "Model not trained — using baseline"}

        dist = route.get("distance_km", 50) or 50
        speed = route.get("avg_speed_kmh", 45) or 45
        features = [1.0, dist, 1.0 / max(speed, 1), speed]

        prediction = sum(c * f for c, f in zip(model["coefficients"], features))
        self._record_inference("fuel_consumption")

        return {
            "fuel_estimate_l": round(max(prediction, dist * 0.05), 1),
            "baseline_l": round(dist / 8, 1),
            "confidence": round(1.0 - model.get("mae_liters", 2) / max(prediction, 1), 2),
            "model_version": model.get("version", "?"),
        }

    def predict_maintenance_risk(self, telemetry: Dict[str, Any]) -> Dict[str, Any]:
        """Predict maintenance risk from telemetry."""
        model = self._models.get("maintenance_risk_scorer")
        rules = model["rules"] if model else {"coolant_temp_warning": 105, "coolant_temp_critical": 115}

        temp = telemetry.get("coolant_temp", 90) or 90
        voltage = (telemetry.get("obd", {}) or {}).get("voltage", 14.0) if isinstance(telemetry.get("obd"), dict) else 14.0
        mil = telemetry.get("mil", False)

        risk_score = 0
        if temp > rules.get("coolant_temp_critical", 115):
            risk_score += 40
        elif temp > rules.get("coolant_temp_warning", 105):
            risk_score += 20
        if voltage < rules.get("voltage_critical", 11.8):
            risk_score += 30
        if mil:
            risk_score += 25

        risk = "CRITICAL" if risk_score >= 40 else ("HIGH" if risk_score >= 20 else ("MEDIUM" if risk_score >= 10 else "LOW"))

        self._record_inference("maintenance_risk")
        return {"risk_level": risk, "score": risk_score, "factors": {"coolant_temp": temp, "voltage": voltage, "mil": mil}}

    def predict_route_safety(self, route: Dict[str, Any], driver: Dict[str, Any]) -> Dict[str, Any]:
        """Predict safety risk for a driver-route pairing."""
        driver_score = (driver.get("safety_score", 80) or 80) / 100
        hazard_density = (len(route.get("hazard_zones", [])) / max(route.get("distance_km", 1), 1)) * 10
        risk = max(0, min(100, (1 - driver_score) * 60 + hazard_density * 20))

        self._record_inference("route_safety")
        return {
            "safety_score_projected": round(100 - risk, 1),
            "risk_level": "HIGH" if risk > 50 else ("MEDIUM" if risk > 25 else "LOW"),
            "contributing_factors": {
                "driver_safety_base": round(driver_score * 100, 0),
                "route_hazard_contribution": round(hazard_density * 20, 1),
            },
        }

    # ── Cache ────────────────────────────────────────────────────────────

    def cached_predict(self, cache_key: str, predict_fn, *args, ttl_seconds: int = 60):
        """Cache predictions to avoid redundant computation."""
        now = datetime.utcnow()
        if cache_key in self._cache:
            entry = self._cache[cache_key]
            if (now - entry["ts"]).total_seconds() < ttl_seconds:
                return entry["result"]
        result = predict_fn(*args)
        self._cache[cache_key] = {"result": result, "ts": now}
        return result

    # ── Stats ────────────────────────────────────────────────────────────

    def _record_inference(self, model_name: str):
        self._inference_count[model_name] += 1

    def stats(self) -> Dict[str, Any]:
        return {
            "models_served": list(self._models.keys()),
            "inference_counts": dict(self._inference_count),
            "cache_entries": len(self._cache),
        }
