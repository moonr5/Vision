"""
Route Engine — Behavior Integrator.
Correlates driver behavior history with route characteristics to produce
risk profiles and suitability scores for driver-route pairings.

This is the bridge between the existing BehaviorAnalysis module data
(stored in driver_behavior_history) and the route optimization pipeline.
"""

from typing import Dict, Any, List, Optional, Tuple
import math


class BehaviorIntegrator:
    """
    Analyses driver behaviour history and produces metrics that feed into
    route scoring. The output answers questions like:

      - Is this driver safe enough for this route?
      - Which segments of this route are high-risk for this driver?
      - What behaviour patterns correlate with route characteristics?
    """

    # ── Risk weights (tuneable) ──────────────────────────────────────────

    WEIGHTS = {
        "speeding": 0.30,          # highest penalty — safety-critical
        "harsh_braking": 0.22,
        "aggressive_launch": 0.18,
        "engine_lugging": 0.12,
        "cold_engine_abuse": 0.10,
        "excessive_idling": 0.08,  # lowest — mostly fuel waste
    }

    # Severity multiplier per behaviour type
    SEVERITY_MAP = {
        "Speeding": 3, "Harsh Braking": 2, "Aggressive Launch": 2,
        "Engine Lugging": 2, "Cold Engine Abuse": 1, "Excessive Idling": 1,
    }

    def __init__(self):
        pass

    # ── Public API ───────────────────────────────────────────────────────

    def compute_risk_index(self, profile: Dict[str, Any]) -> float:
        """
        Compute a 0–1 behaviour risk index from a driver's profile.
        0 = no risk (perfect driver), 1 = extreme risk.
        """
        if not profile:
            return 0.5  # unknown driver → neutral

        # Normalise event counts against a reasonable ceiling per 90 days
        ceilings = {
            "speeding_count": 20, "harsh_braking_count": 30,
            "aggressive_launch_count": 25, "engine_lugging_count": 20,
            "cold_engine_abuse_count": 15, "excessive_idling_count": 30,
        }

        risk = 0.0
        for key, weight in self.WEIGHTS.items():
            db_key = f"{key.replace('_count', '')}_count"
            # Map WEIGHTS keys to profile keys — handle mapping
            mapped = self._map_key_to_profile(key)
            if mapped is None:
                continue
            count = profile.get(mapped, 0) or 0
            ceiling = ceilings.get(mapped, 30)
            normalised = min(count / max(ceiling, 1), 1.0)
            risk += weight * normalised

        return round(min(risk, 1.0), 4)

    def compute_safety_score(
        self, profile: Dict[str, Any], route_distance_km: float = 0.0
    ) -> float:
        """
        Project a driver's safety score for a specific route distance.
        Longer routes expose more risk — the score degrades with distance.
        """
        base_score = float(profile.get("safety_score", 100) or 100)
        if route_distance_km <= 0:
            return base_score

        # Degradation: each 100 km of route length reduces effective score
        # by a small factor based on risk index
        risk = self.compute_risk_index(profile)
        degradation = (route_distance_km / 100.0) * risk * 5.0
        projected = max(base_score - degradation, 0.0)
        return round(projected, 1)

    def driver_suitability(
        self,
        profile: Dict[str, Any],
        route_attributes: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Produce a full suitability report for a driver on a specific route.

        route_attributes may contain:
          - distance_km, estimated_duration_min, avg_speed_kmh
          - hazard_zones (list of dicts with type, lat, lng, severity)
          - segment_count
        """
        risk_index = self.compute_risk_index(profile)
        projected_safety = self.compute_safety_score(
            profile, route_attributes.get("distance_km", 0)
        )

        # Identify which behaviour types are concerns for this route
        concerns = self._identify_concerns(profile)

        # Match driver behaviour patterns to route hazard types
        route_hazards = route_attributes.get("hazard_zones", []) or []
        hazard_match = self._match_hazards_to_driver(profile, route_hazards)

        # Build recommendation
        recommendation, rationale = self._build_recommendation(
            risk_index, projected_safety, concerns, hazard_match,
            route_attributes.get("distance_km", 0),
        )

        return {
            "driver_id": profile.get("driver_id"),
            "driver_name": profile.get("driver_name"),
            "risk_index": risk_index,
            "projected_safety_score": projected_safety,
            "original_safety_score": profile.get("safety_score", 100),
            "behavioural_concerns": concerns,
            "route_hazard_matches": hazard_match,
            "recommendation": recommendation,
            "rationale": rationale,
            "suitable": projected_safety >= 60 and risk_index < 0.55,
        }

    def compare_drivers(
        self,
        drivers: List[Dict[str, Any]],
        route_attributes: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        Rank multiple drivers by suitability for a given route.
        Returns sorted list — best driver first.
        """
        scored = []
        for profile in drivers:
            suitability = self.driver_suitability(profile, route_attributes)
            scored.append(suitability)
        scored.sort(key=lambda s: (s["suitable"], -s["projected_safety_score"]), reverse=True)
        return scored

    # ── Internal helpers ─────────────────────────────────────────────────

    def _map_key_to_profile(self, weight_key: str) -> Optional[str]:
        """Map a WEIGHTS key to the profile dict key."""
        mapping = {
            "speeding": "speeding_count",
            "harsh_braking": "harsh_braking_count",
            "aggressive_launch": "aggressive_launch_count",
            "engine_lugging": "engine_lugging_count",
            "cold_engine_abuse": "cold_engine_abuse_count",
            "excessive_idling": "excessive_idling_count",
        }
        return mapping.get(weight_key)

    def _identify_concerns(self, profile: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Flag behaviour categories where the driver exceeds acceptable thresholds."""
        thresholds = {
            "speeding_count": 3,
            "harsh_braking_count": 5,
            "aggressive_launch_count": 4,
            "engine_lugging_count": 4,
            "cold_engine_abuse_count": 3,
            "excessive_idling_count": 8,
        }
        label_map = {
            "speeding_count": "Speeding",
            "harsh_braking_count": "Harsh Braking",
            "aggressive_launch_count": "Aggressive Launch",
            "engine_lugging_count": "Engine Lugging",
            "cold_engine_abuse_count": "Cold Engine Abuse",
            "excessive_idling_count": "Excessive Idling",
        }
        concerns = []
        for key, threshold in thresholds.items():
            count = profile.get(key, 0) or 0
            if count > threshold:
                severity = self.SEVERITY_MAP.get(label_map[key], 1)
                concerns.append({
                    "behaviour": label_map[key],
                    "count_90d": count,
                    "threshold": threshold,
                    "severity": severity,
                })
        concerns.sort(key=lambda c: c["severity"], reverse=True)
        return concerns

    def _match_hazards_to_driver(
        self, profile: Dict[str, Any], hazard_zones: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Cross-reference route hazards with driver behaviour weaknesses.
        E.g., a route with many sharp turns + a driver with harsh braking history = high match.
        """
        if not hazard_zones:
            return []

        concern_behaviours = {c["behaviour"].lower() for c in self._identify_concerns(profile)}
        matches = []

        hazard_type_to_behaviour = {
            "sharp_curve": "harsh braking",
            "steep_incline": "engine lugging",
            "high_speed_zone": "speeding",
            "urban_congestion": "excessive idling",
            "traffic_light_dense": "aggressive launch",
            "school_zone": "speeding",
            "mountain_pass": "engine lugging",
        }

        for zone in hazard_zones:
            zone_type = (zone.get("type") or "").lower()
            matched_behaviour = hazard_type_to_behaviour.get(zone_type, "")
            if matched_behaviour in concern_behaviours:
                matches.append({
                    "hazard_type": zone_type,
                    "hazard_location": {
                        "lat": zone.get("lat"),
                        "lng": zone.get("lng"),
                    },
                    "matched_behaviour": matched_behaviour,
                    "risk_level": "high" if matched_behaviour in ("speeding", "harsh braking") else "medium",
                })

        return matches

    def _build_recommendation(
        self,
        risk_index: float,
        projected_score: float,
        concerns: List[Dict],
        hazard_matches: List[Dict],
        distance_km: float,
    ) -> Tuple[str, str]:
        """Generate a human-readable recommendation and rationale."""
        if risk_index < 0.2 and projected_score >= 85:
            rec = "STRONGLY_RECOMMENDED"
            rationale = (
                f"Driver has a very low behaviour risk index ({risk_index}) "
                f"and a high projected safety score ({projected_score}/100). "
                f"No significant behavioural concerns for this route."
            )
        elif risk_index < 0.4 and projected_score >= 70:
            rec = "RECOMMENDED"
            rationale = (
                f"Driver shows acceptable risk levels (index {risk_index}) "
                f"with adequate projected safety ({projected_score}/100)."
            )
        elif risk_index < 0.55 and projected_score >= 60:
            rec = "CONDITIONAL"
            concern_names = [c["behaviour"] for c in concerns[:3]]
            rationale = (
                f"Driver is borderline suitable (risk {risk_index}, score {projected_score}). "
                f"Concerns: {', '.join(concern_names) if concern_names else 'none flagged'}. "
                f"Monitor closely on this route."
            )
        else:
            rec = "NOT_RECOMMENDED"
            rationale = (
                f"Driver has elevated risk (index {risk_index}) and low projected safety "
                f"({projected_score}/100). "
                f"Consider reassigning or requiring remedial coaching before this route."
            )

        if hazard_matches:
            matched = [m["hazard_type"] for m in hazard_matches]
            rationale += (
                f" ⚠ Route hazards ({', '.join(matched)}) align with this driver's "
                f"known behaviour weaknesses — elevated caution required."
            )

        return rec, rationale
