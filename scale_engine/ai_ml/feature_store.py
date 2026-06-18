"""
Scale Engine — Feature Store.
Precomputed driver, vehicle, and route features for fast AI queries.
Features are versioned, cached, and served via a simple API.
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from collections import defaultdict
import json
import hashlib


class FeatureStore:
    """
    Centralized feature store for ML model consumption.

    Features are organized by entity type:
      - driver_features: safety score trend, event rates, pace, fuel eff
      - vehicle_features: health indicators, MIL history, fuel patterns
      - route_features: distance, hazards, historical performance, time-of-day profiles
      - segment_features: individual route segment characteristics

    All features are versioned so models can pin to specific feature sets.
    """

    def __init__(self):
        self._store: Dict[str, Dict[str, Any]] = defaultdict(dict)
        self._version = 1

    # ── Driver features ──────────────────────────────────────────────────

    def compute_driver_features(self, driver: Dict[str, Any]) -> Dict[str, Any]:
        """Compute ML-ready feature vector for a driver."""
        safety = driver.get("safety_score", 80) or 80
        total_events = driver.get("total_events", 0) or 0

        features = {
            "safety_score": safety,
            "safety_score_normalized": safety / 100,
            "total_events_90d": total_events,
            "event_rate_per_day": round(total_events / max(90, 1), 3),

            # Individual event rates
            "speeding_rate": driver.get("speeding_count", 0) or 0,
            "harsh_braking_rate": driver.get("harsh_braking_count", 0) or 0,
            "aggressive_launch_rate": driver.get("aggressive_launch_count", 0) or 0,
            "idling_rate": driver.get("excessive_idling_count", 0) or 0,

            # Performance
            "fuel_efficiency_kmpl": driver.get("fuel_efficiency", 7.0) or 7.0,
            "avg_speed_kmh": driver.get("avg_speed", 45.0) or 45.0,
            "total_distance_km": driver.get("total_distance_km", 0) or 0,
            "total_trips": driver.get("total_trips", 0) or 0,

            # Derived
            "risk_category": "high" if safety < 60 else ("medium" if safety < 80 else "low"),
            "experience_level": "veteran" if (driver.get("total_trips", 0) or 0) > 100 else ("mid" if (driver.get("total_trips", 0) or 0) > 20 else "novice"),
        }

        driver_id = driver.get("driver_id") or driver.get("id")
        self._store["driver"][driver_id] = {
            "features": features,
            "version": self._version,
            "computed_at": datetime.utcnow().isoformat(),
        }
        return features

    # ── Vehicle features ─────────────────────────────────────────────────

    def compute_vehicle_features(self, vehicle: Dict[str, Any]) -> Dict[str, Any]:
        """Compute ML-ready feature vector for a vehicle."""
        features = {
            "fuel_level_pct": vehicle.get("fuel_level", 50) or 50,
            "mil_active": vehicle.get("mil", False),
            "coolant_temp_c": vehicle.get("coolant_temp", 90) or 90,
            "engine_load_pct": vehicle.get("engine_load", 30) or 30,
            "rpm": vehicle.get("rpm", 0) or 0,
            "speed_kmh": vehicle.get("speed", 0) or 0,
            "engine_running": (vehicle.get("rpm", 0) or 0) > 400,
            "in_motion": (vehicle.get("speed", 0) or 0) > 1,
        }

        device_id = vehicle.get("device_id") or vehicle.get("id")
        self._store["vehicle"][device_id] = {
            "features": features,
            "version": self._version,
            "computed_at": datetime.utcnow().isoformat(),
        }
        return features

    # ── Route features ───────────────────────────────────────────────────

    def compute_route_features(self, route: Dict[str, Any]) -> Dict[str, Any]:
        """Compute ML-ready feature vector for a route."""
        distance = route.get("distance_km", 50) or 50
        hazards = route.get("hazard_zones", []) or []

        features = {
            "distance_km": distance,
            "distance_log": __import__("math").log(max(distance, 0.1)),
            "estimated_duration_min": route.get("estimated_duration_min", 60) or 60,
            "avg_speed_kmh": route.get("avg_speed_kmh", 45) or 45,
            "hazard_count": len(hazards),
            "hazard_density_per_km": round(len(hazards) / max(distance, 0.1), 3),
            "high_severity_hazards": sum(1 for h in hazards if h.get("severity") == "high"),
            "hazard_profile": route.get("hazard_profile", "mixed"),
            "fuel_estimate_l": route.get("fuel_estimate_l", distance / 8) or distance / 8,
        }

        route_id = route.get("route_id", hashlib.md5(str(route).encode()).hexdigest()[:8])
        self._store["route"][route_id] = {
            "features": features,
            "version": self._version,
            "computed_at": datetime.utcnow().isoformat(),
        }
        return features

    # ── Query API ────────────────────────────────────────────────────────

    def get_features(self, entity_type: str, entity_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve precomputed features for an entity."""
        entry = self._store.get(entity_type, {}).get(entity_id)
        return entry["features"] if entry else None

    def get_feature_vector(self, entity_type: str, entity_id: str, feature_names: List[str] = None) -> List[float]:
        """Get features as a flat numeric vector (for ML inference)."""
        features = self.get_features(entity_type, entity_id)
        if not features:
            return []

        numeric = []
        for k, v in sorted(features.items()):
            if isinstance(v, str):
                continue
            if feature_names and k not in feature_names:
                continue
            try:
                numeric.append(float(v))
            except (TypeError, ValueError):
                pass
        return numeric

    def get_all_entities(self, entity_type: str) -> List[Dict[str, Any]]:
        """Get all features for an entity type."""
        return [
            {"entity_id": eid, **entry["features"]}
            for eid, entry in self._store.get(entity_type, {}).items()
        ]

    def stats(self) -> Dict[str, Any]:
        return {
            "version": self._version,
            "driver_features": len(self._store.get("driver", {})),
            "vehicle_features": len(self._store.get("vehicle", {})),
            "route_features": len(self._store.get("route", {})),
        }
