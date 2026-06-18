"""
Route Engine — Route Analyzer.
Generates route candidates between origin and destination, scores each one
using AI (Gemini) + behaviour data + historical performance to identify the
optimal route for a given driver.

Architecture:
  1. Generate N candidate routes (waypoint variations)
  2. Score each route across 5 dimensions:
       - Safety (driver behaviour fit, hazard density)
       - Efficiency (fuel, distance, duration)
       - Driver suitability (behaviour profile match)
       - Historical performance (past trips on similar routes)
       - Risk exposure (hazard zones × driver weaknesses)
  3. AI (Gemini) provides the final ranking rationale
  4. Return the winning route with full scoring breakdown
"""

import math
import hashlib
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field

import google.generativeai as genai

from behavior_integrator import BehaviorIntegrator


# ── AI system prompt ────────────────────────────────────────────────────────

ROUTE_SYSTEM_PROMPT = (
    "You are a fleet route optimization AI for SGU Logistics. "
    "Your job is to evaluate multiple route candidates for a delivery and "
    "recommend the single best route based on safety, efficiency, driver "
    "behaviour history, and risk exposure. "
    "Rules: "
    "  - Output ONLY valid JSON (no markdown, no code fences, no commentary). "
    "  - Rank routes from best (1) to worst. "
    "  - For each route, provide: rank, score (0-100), strengths (list), "
    "    weaknesses (list), and a one-sentence verdict. "
    "  - Then provide a 'recommendation' object with: winning_route_id, "
    "    summary (≤3 sentences), and key_factors (list of 2-4 decisive factors). "
    "  - Be data-driven — cite specific numbers from the provided context. "
    "  - Heavily penalise routes where the driver's behaviour weaknesses "
    "    match known route hazards."
)

ROUTE_SCORING_SCHEMA = {
    "type": "object",
    "properties": {
        "rankings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "route_id": {"type": "string"},
                    "rank": {"type": "integer"},
                    "score": {"type": "number"},
                    "strengths": {"type": "array", "items": {"type": "string"}},
                    "weaknesses": {"type": "array", "items": {"type": "string"}},
                    "verdict": {"type": "string"},
                },
                "required": ["route_id", "rank", "score", "strengths", "weaknesses", "verdict"],
            },
        },
        "recommendation": {
            "type": "object",
            "properties": {
                "winning_route_id": {"type": "string"},
                "summary": {"type": "string"},
                "key_factors": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["winning_route_id", "summary", "key_factors"],
        },
    },
    "required": ["rankings", "recommendation"],
}


class RouteAnalyzer:
    """Core route optimization engine."""

    # Scoring dimension weights
    DIMENSION_WEIGHTS = {
        "safety": 0.30,
        "driver_suitability": 0.25,
        "efficiency": 0.20,
        "historical_performance": 0.15,
        "risk_exposure": 0.10,
    }

    # Earth radius in km
    EARTH_RADIUS_KM = 6371.0

    def __init__(self, gemini_api_key: str):
        genai.configure(api_key=gemini_api_key)
        self._model = genai.GenerativeModel(
            model_name="gemini-2.0-flash",
            system_instruction=ROUTE_SYSTEM_PROMPT,
        )
        self._behavior = BehaviorIntegrator()

    # ── Public API ───────────────────────────────────────────────────────

    def generate_candidates(
        self,
        origin: Tuple[float, float],
        destination: Tuple[float, float],
        driver_profile: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Generate 3 route candidates between origin and destination.
        In production this would call a routing engine (OSRM, Google Directions, etc.).
        Here we generate realistic synthetic waypoints based on bearing + distance.
        """
        origin_lat, origin_lng = origin
        dest_lat, dest_lng = destination

        direct_distance = self._haversine_km(origin_lat, origin_lng, dest_lat, dest_lng)
        bearing = self._bearing_deg(origin_lat, origin_lng, dest_lat, dest_lng)

        candidates = []

        # Route A — Direct (shortest path)
        candidates.append(self._build_candidate(
            "A", "Direct Route",
            origin_lat, origin_lng, dest_lat, dest_lng,
            distance_mult=1.0,
            bearing=bearing,
            hazard_profile="mixed",
            direct_distance=direct_distance,
        ))

        # Route B — Scenic / Arterial (10-20% longer, fewer intersections, higher speed)
        candidates.append(self._build_candidate(
            "B", "Arterial Route",
            origin_lat, origin_lng, dest_lat, dest_lng,
            distance_mult=1.15,
            bearing=bearing,
            hazard_profile="high_speed",
            direct_distance=direct_distance,
        ))

        # Route C — Urban / Local (5-15% longer, more stops, lower speed, more congestion)
        candidates.append(self._build_candidate(
            "C", "Urban Route",
            origin_lat, origin_lng, dest_lat, dest_lng,
            distance_mult=1.08,
            bearing=bearing,
            hazard_profile="urban",
            direct_distance=direct_distance,
        ))

        # If a driver profile is available, pre-compute behaviour suitability for each candidate
        if driver_profile:
            for c in candidates:
                suitability = self._behavior.driver_suitability(driver_profile, c)
                c["driver_suitability"] = suitability
                c["behavior_risk_index"] = suitability["risk_index"]
                c["projected_safety_score"] = suitability["projected_safety_score"]
                c["suitable"] = suitability["suitable"]

        return candidates

    async def score_routes(
        self,
        candidates: List[Dict[str, Any]],
        driver_profile: Optional[Dict[str, Any]] = None,
        fleet_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Score all route candidates using AI + computed metrics.
        Returns ranked results with the winning route.
        """
        if not candidates:
            return {"error": "No candidates provided"}

        # Build comprehensive scoring context for the AI
        context_text = self._build_scoring_context(
            candidates, driver_profile, fleet_context
        )

        prompt = (
            f"Evaluate these {len(candidates)} route candidates for a fleet delivery. "
            f"For each route, score it 0-100 considering safety, efficiency, "
            f"driver suitability, and risk. Rank best to worst.\n\n"
            f"{context_text}"
        )

        try:
            response = self._model.generate_content(
                prompt,
                generation_config={
                    "temperature": 0.3,
                    "max_output_tokens": 1500,
                    "response_mime_type": "application/json",
                    "response_schema": ROUTE_SCORING_SCHEMA,
                },
            )
            ai_result = response.text
        except Exception as e:
            # Fallback: score without AI
            ai_result = self._fallback_scoring(candidates, driver_profile)

        # Merge AI scores back into candidates
        return self._merge_results(candidates, ai_result, driver_profile)

    def rank_drivers_for_route(
        self,
        driver_profiles: List[Dict[str, Any]],
        route: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Given a fixed route, rank all available drivers by suitability."""
        return self._behavior.compare_drivers(driver_profiles, route)

    # ── Candidate generation ─────────────────────────────────────────────

    def _build_candidate(
        self,
        route_key: str,
        route_name: str,
        origin_lat: float, origin_lng: float,
        dest_lat: float, dest_lng: float,
        distance_mult: float,
        bearing: float,
        hazard_profile: str,
        direct_distance: float,
    ) -> Dict[str, Any]:
        """Build one route candidate with synthetic waypoints and hazard zones."""
        route_distance = direct_distance * distance_mult

        # Estimate duration: direct ~ avg 45 km/h, arterial ~55 km/h, urban ~30 km/h
        speed_map = {"mixed": 45, "high_speed": 55, "urban": 30}
        avg_speed = speed_map.get(hazard_profile, 45)
        duration_min = (route_distance / avg_speed) * 60

        # Fuel estimate: rough 8 km/L base, adjust for profile
        fuel_base_l = route_distance / 8.0
        fuel_adj = {"mixed": 1.0, "high_speed": 1.05, "urban": 1.25}
        fuel_estimate = round(fuel_base_l * fuel_adj.get(hazard_profile, 1.0), 2)

        # Generate hazard zones along the route
        hazards = self._generate_hazard_zones(
            origin_lat, origin_lng, dest_lat, dest_lng, hazard_profile
        )

        # Generate synthetic segments
        segment_count = max(3, int(route_distance / 2))  # ~1 segment per 2 km
        segments = self._generate_segments(
            origin_lat, origin_lng, dest_lat, dest_lng,
            segment_count, hazard_profile,
        )

        route_id = hashlib.md5(
            f"{origin_lat:.4f}{origin_lng:.4f}{dest_lat:.4f}{dest_lng:.4f}{route_key}".encode()
        ).hexdigest()[:12]

        return {
            "route_id": route_id,
            "route_name": route_name,
            "route_key": route_key,
            "origin_lat": origin_lat,
            "origin_lng": origin_lng,
            "destination_lat": dest_lat,
            "destination_lng": dest_lng,
            "distance_km": round(route_distance, 2),
            "estimated_duration_min": round(duration_min, 1),
            "avg_speed_kmh": avg_speed,
            "fuel_estimate_l": fuel_estimate,
            "safety_score": 100.0,
            "behavior_risk_index": 0.0,
            "segment_count": segment_count,
            "segments": segments,
            "hazard_zones": hazards,
            "hazard_profile": hazard_profile,
            "ai_rationale": "",
        }

    def _generate_hazard_zones(
        self, lat1: float, lng1: float, lat2: float, lng2: float,
        profile: str,
    ) -> List[Dict[str, Any]]:
        """Generate synthetic hazard zones along the route based on profile."""
        hazards = []
        hazard_counts = {
            "mixed": [("sharp_curve", 1), ("school_zone", 1)],
            "high_speed": [("high_speed_zone", 2), ("sharp_curve", 1)],
            "urban": [("traffic_light_dense", 2), ("school_zone", 1), ("urban_congestion", 2)],
        }

        for hazard_type, count in hazard_counts.get(profile, []):
            for i in range(count):
                frac = 0.2 + (0.6 * i / max(count, 1))  # spread along route
                lat = lat1 + (lat2 - lat1) * frac
                lng = lng1 + (lng2 - lng1) * frac
                hazards.append({
                    "type": hazard_type,
                    "lat": round(lat, 5),
                    "lng": round(lng, 5),
                    "severity": "high" if hazard_type in ("sharp_curve", "school_zone") else "medium",
                })

        return hazards

    def _generate_segments(
        self, lat1: float, lng1: float, lat2: float, lng2: float,
        count: int, profile: str,
    ) -> List[Dict[str, Any]]:
        """Generate synthetic route segments with telemetry-like metrics."""
        segments = []
        speed_map = {"mixed": 45, "high_speed": 55, "urban": 30}
        base_speed = speed_map.get(profile, 45)

        for i in range(count):
            frac = (i + 1) / count
            lat = lat1 + (lat2 - lat1) * frac
            lng = lng1 + (lng2 - lng1) * frac
            # Vary speed slightly
            speed = base_speed + ((hash(f"{lat}{lng}{i}") % 20) - 10)
            segments.append({
                "lat": round(lat, 5),
                "lng": round(lng, 5),
                "speed": round(speed, 1),
                "heading": round(self._bearing_deg(lat1, lng1, lat2, lng2) + ((i % 5) - 2) * 3, 1),
                "engine_load": round(30 + (hash(f"load{i}") % 40), 1),
                "throttle": round(15 + (hash(f"thr{i}") % 30), 1),
                "fuel_flow": round(2.0 + (hash(f"fuel{i}") % 5) * 0.5, 2),
                "event_count": 0,
            })

        return segments

    # ── Scoring methods ──────────────────────────────────────────────────

    def _build_scoring_context(
        self,
        candidates: List[Dict[str, Any]],
        driver_profile: Optional[Dict[str, Any]],
        fleet_context: Optional[Dict[str, Any]],
    ) -> str:
        """Build a dense text context for the AI scoring prompt."""
        lines = []

        # Driver context
        if driver_profile:
            lines.append("DRIVER PROFILE:")
            lines.append(f"  Name: {driver_profile.get('driver_name', 'Unknown')}")
            lines.append(f"  Safety Score: {driver_profile.get('safety_score', 'N/A')}/100")
            lines.append(f"  Total Trips: {driver_profile.get('total_trips', 0)}")
            lines.append(f"  Avg Fuel Efficiency: {driver_profile.get('fuel_efficiency', 'N/A')} km/L")
            lines.append(f"  Behaviour Events (90d):")
            for key in ["speeding_count", "harsh_braking_count", "aggressive_launch_count",
                         "engine_lugging_count", "cold_engine_abuse_count", "excessive_idling_count"]:
                label = key.replace("_count", "").replace("_", " ").title()
                lines.append(f"    - {label}: {driver_profile.get(key, 0)}")

        # Fleet context
        if fleet_context:
            lines.append("\nFLEET BENCHMARKS:")
            lines.append(f"  Fleet Avg Safety Score: {fleet_context.get('fleet_avg_score', 'N/A')}")
            lines.append(f"  Active Drivers: {fleet_context.get('active_drivers', 0)}")

        # Route candidates
        lines.append(f"\nROUTE CANDIDATES ({len(candidates)}):")
        for c in candidates:
            lines.append(f"\n  Route {c['route_key']} — {c['route_name']}:")
            lines.append(f"    Distance: {c['distance_km']} km")
            lines.append(f"    Est. Duration: {c['estimated_duration_min']} min")
            lines.append(f"    Avg Speed: {c['avg_speed_kmh']} km/h")
            lines.append(f"    Fuel Estimate: {c['fuel_estimate_l']} L")
            lines.append(f"    Profile: {c.get('hazard_profile', 'unknown')}")
            lines.append(f"    Hazard Zones: {len(c.get('hazard_zones', []))}")
            for hz in c.get("hazard_zones", []):
                lines.append(f"      - {hz['type']} ({hz['severity']}) at {hz['lat']},{hz['lng']}")
            if "driver_suitability" in c:
                ds = c["driver_suitability"]
                lines.append(f"    Driver Suitability: {ds['recommendation']}")
                lines.append(f"    Projected Safety: {ds['projected_safety_score']}/100")
                lines.append(f"    Risk Index: {ds['risk_index']}")

        return "\n".join(lines)

    def _fallback_scoring(
        self,
        candidates: List[Dict[str, Any]],
        driver_profile: Optional[Dict[str, Any]],
    ) -> str:
        """Deterministic fallback scoring when Gemini is unavailable."""
        import json

        # Score each candidate on the 5 dimensions
        for c in candidates:
            scores = {}

            # Safety: inversely proportional to hazard count
            hazard_count = len(c.get("hazard_zones", []))
            scores["safety"] = max(0, 100 - hazard_count * 12)

            # Driver suitability: from pre-computed data if available
            if "projected_safety_score" in c:
                scores["driver_suitability"] = c["projected_safety_score"]
            else:
                scores["driver_suitability"] = 70

            # Efficiency: shorter + less fuel = better
            eff_score = 100 - (c["distance_km"] / 5) - (c["fuel_estimate_l"] * 2)
            scores["efficiency"] = max(0, min(100, eff_score))

            # Historical: default neutral if no data
            scores["historical_performance"] = 70

            # Risk exposure: lower is worse
            risk = c.get("behavior_risk_index", 0.3)
            scores["risk_exposure"] = max(0, 100 - risk * 100)

            # Weighted total
            total = sum(
                scores[dim] * self.DIMENSION_WEIGHTS[dim]
                for dim in self.DIMENSION_WEIGHTS
            )
            c["_total_score"] = round(total, 1)
            c["_dimension_scores"] = scores

        # Sort by total score
        candidates.sort(key=lambda c: c["_total_score"], reverse=True)

        # Build equivalent JSON
        rankings = []
        for rank, c in enumerate(candidates, 1):
            rankings.append({
                "route_id": c["route_id"],
                "rank": rank,
                "score": c["_total_score"],
                "strengths": [f"{c['route_name']}: {c['distance_km']}km, {c['estimated_duration_min']}min"],
                "weaknesses": [f"{len(c.get('hazard_zones',[]))} hazard zones on route"],
                "verdict": f"Route {c['route_key']} scores {c['_total_score']}/100 across all dimensions.",
            })

        result = {
            "rankings": rankings,
            "recommendation": {
                "winning_route_id": candidates[0]["route_id"],
                "summary": (
                    f"Route {candidates[0]['route_key']} ({candidates[0]['route_name']}) "
                    f"is recommended at {candidates[0]['_total_score']}/100."
                ),
                "key_factors": [
                    f"Shortest distance: {candidates[0]['distance_km']}km",
                    f"Duration: {candidates[0]['estimated_duration_min']}min",
                ],
            },
        }

        return json.dumps(result)

    def _merge_results(
        self,
        candidates: List[Dict[str, Any]],
        ai_result_text: str,
        driver_profile: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Merge AI rankings back into candidate data."""
        import json

        try:
            ai_result = json.loads(ai_result_text)
        except json.JSONDecodeError:
            # Try stripping potential markdown fences
            cleaned = ai_result_text.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1]
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3]
            try:
                ai_result = json.loads(cleaned.strip())
            except json.JSONDecodeError:
                ai_result = json.loads(self._fallback_scoring(candidates, driver_profile))

        rankings = ai_result.get("rankings", [])
        recommendation = ai_result.get("recommendation", {})

        # Build lookup from candidate route_id → candidate
        candidate_map = {c["route_id"]: c for c in candidates}

        # Merge AI scores into candidates
        enriched = []
        for r in rankings:
            route_id = r.get("route_id", "")
            c = candidate_map.get(route_id)
            if c:
                c["ai_score"] = r.get("score", 0)
                c["ai_rank"] = r.get("rank", 99)
                c["ai_strengths"] = r.get("strengths", [])
                c["ai_weaknesses"] = r.get("weaknesses", [])
                c["ai_verdict"] = r.get("verdict", "")
                c["ai_rationale"] = r.get("verdict", "")
                enriched.append(c)

        # Sort by AI rank
        enriched.sort(key=lambda c: c.get("ai_rank", 99))

        winning_id = recommendation.get("winning_route_id", "")
        winner = candidate_map.get(winning_id, enriched[0] if enriched else None)

        return {
            "candidates": enriched,
            "winning_route": winner,
            "recommendation_summary": recommendation.get("summary", ""),
            "key_factors": recommendation.get("key_factors", []),
            "total_candidates": len(candidates),
            "driver_profile_used": driver_profile is not None,
        }

    # ── Geospatial helpers ───────────────────────────────────────────────

    def _haversine_km(self, lat1: float, lng1: float, lat2: float, lng2: float) -> float:
        dlat = math.radians(lat2 - lat1)
        dlng = math.radians(lng2 - lng1)
        a = (
            math.sin(dlat / 2) ** 2
            + math.cos(math.radians(lat1))
            * math.cos(math.radians(lat2))
            * math.sin(dlng / 2) ** 2
        )
        return self.EARTH_RADIUS_KM * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    def _bearing_deg(self, lat1: float, lng1: float, lat2: float, lng2: float) -> float:
        dlat = math.radians(lat2 - lat1)
        dlng = math.radians(lng2 - lng1)
        y = math.sin(dlng) * math.cos(math.radians(lat2))
        x = math.cos(math.radians(lat1)) * math.sin(math.radians(lat2)) - \
            math.sin(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.cos(dlng)
        bearing = math.degrees(math.atan2(y, x))
        return (bearing + 360) % 360
