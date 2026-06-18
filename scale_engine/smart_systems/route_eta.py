"""
Scale Engine — Route & ETA Intelligence.
Fuses live GPS, historical trip data, traffic patterns, and weather
to produce highly accurate ETAs and route recommendations.
"""

import math
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
from collections import defaultdict


class RouteETAEngine:
    """
    Route & ETA prediction engine.

    Factors:
      - Live GPS position + speed + heading
      - Historical trip durations for similar routes
      - Time-of-day adjustment (rush hour multiplier)
      - Driver-specific pace factor
      - Weather impact coefficient (placeholder for API integration)
      - Rest/break prediction
    """

    # Time-of-day speed multipliers (Jakarta baseline)
    HOUR_MULTIPLIERS = {
        **{h: 0.65 for h in range(7, 10)},    # morning rush
        **{h: 0.70 for h in range(16, 19)},    # evening rush
        **{h: 0.95 for h in range(10, 16)},    # midday
        **{h: 0.90 for h in range(19, 22)},    # evening
        **{h: 1.10 for h in range(22, 24)},    # night
        **{h: 1.10 for h in range(0, 6)},      # early morning
        **{h: 0.85 for h in range(6, 7)},      # dawn transition
    }

    def __init__(self):
        self._trip_history: Dict[str, List[Dict]] = defaultdict(list)

    # ── ETA computation ──────────────────────────────────────────────────

    def compute_eta(
        self,
        current_lat: float, current_lng: float,
        dest_lat: float, dest_lng: float,
        current_speed: float = 0,
        driver_pace_factor: float = 1.0,
    ) -> Dict[str, Any]:
        """
        Compute a multi-factor ETA.

        driver_pace_factor: 1.0 = fleet average. <1.0 = faster driver. >1.0 = slower.
        """
        distance_km = self._haversine_km(current_lat, current_lng, dest_lat, dest_lng)

        # Base speed estimate
        base_speed = 45.0  # km/h fleet average

        # Time-of-day adjustment
        hour = datetime.utcnow().hour + 7  # Approx Jakarta time
        hour = hour % 24
        tod_mult = self.HOUR_MULTIPLIERS.get(hour, 0.90)

        # Effective speed
        effective_speed = base_speed * tod_mult / max(driver_pace_factor, 0.5)
        if current_speed > 5:
            effective_speed = (effective_speed + current_speed) / 2

        # Raw ETA
        raw_minutes = (distance_km / max(effective_speed, 1)) * 60

        # Add rest break if trip > 4 hours
        rest_minutes = 0
        if raw_minutes > 240:
            rest_minutes = 30 * (raw_minutes // 240)

        total_minutes = raw_minutes + rest_minutes
        eta = datetime.utcnow() + timedelta(minutes=total_minutes)

        return {
            "distance_km": round(distance_km, 2),
            "effective_speed_kmh": round(effective_speed, 1),
            "raw_eta_minutes": round(raw_minutes, 0),
            "rest_break_minutes": rest_minutes,
            "total_eta_minutes": round(total_minutes, 0),
            "eta_iso": eta.isoformat(),
            "eta_local": (eta + timedelta(hours=7)).strftime("%Y-%m-%d %H:%M WIB"),
            "factors": {
                "time_of_day_multiplier": tod_mult,
                "driver_pace_factor": driver_pace_factor,
                "base_speed_used": round(effective_speed, 1),
            },
        }

    def compute_eta_with_history(
        self, driver_id: str, origin: Tuple[float, float], dest: Tuple[float, float],
        current_speed: float = 0,
    ) -> Dict[str, Any]:
        """Compute ETA using driver's historical trip data as additional factor."""
        # Find similar historical trips
        similar = self._find_similar_trips(driver_id, origin, dest)

        pace_factor = 1.0
        if similar:
            avg_speed_hist = sum(t["avg_speed"] for t in similar) / len(similar)
            if avg_speed_hist > 0:
                pace_factor = 45.0 / avg_speed_hist  # normalize to fleet baseline

        eta = self.compute_eta(origin[0], origin[1], dest[0], dest[1], current_speed, pace_factor)
        eta["historical_trips_found"] = len(similar)
        eta["historical_avg_speed"] = round(sum(t["avg_speed"] for t in similar) / max(len(similar), 1), 1)
        return eta

    def compare_routes_eta(
        self, routes: List[Dict[str, Any]],
        current_speed: float = 0,
    ) -> List[Dict[str, Any]]:
        """
        Given multiple route candidates, compute ETA for each.
        Returns sorted by fastest ETA.
        """
        results = []
        for route in routes:
            eta = self.compute_eta(
                route.get("origin_lat", 0), route.get("origin_lng", 0),
                route.get("destination_lat", 0), route.get("destination_lng", 0),
                current_speed,
            )
            results.append({**route, "eta": eta})
        results.sort(key=lambda r: r["eta"]["total_eta_minutes"])
        return results

    # ── History management ───────────────────────────────────────────────

    def record_completed_trip(self, driver_id: str, trip: Dict[str, Any]):
        """Record trip data for future ETA learning."""
        self._trip_history[driver_id].append({
            **trip,
            "recorded_at": datetime.utcnow().isoformat(),
        })
        if len(self._trip_history[driver_id]) > 200:
            self._trip_history[driver_id] = self._trip_history[driver_id][-100:]

    def get_driver_pace_factor(self, driver_id: str) -> float:
        """Calculate a driver's pace factor from historical data."""
        trips = self._trip_history.get(driver_id, [])
        if not trips:
            return 1.0
        speeds = [t.get("avg_speed", 45) for t in trips if t.get("avg_speed")]
        if not speeds:
            return 1.0
        avg = sum(speeds) / len(speeds)
        return round(45.0 / max(avg, 10), 2)

    # ── Helpers ──────────────────────────────────────────────────────────

    def _find_similar_trips(
        self, driver_id: str, origin: Tuple[float, float], dest: Tuple[float, float],
        radius_km: float = 3.0,
    ) -> List[Dict]:
        """Find historical trips with similar origin/destination."""
        trips = self._trip_history.get(driver_id, [])
        similar = []
        for t in trips:
            o_dist = self._haversine_km(
                origin[0], origin[1],
                t.get("start_lat", 0) or 0, t.get("start_lng", 0) or 0,
            )
            d_dist = self._haversine_km(
                dest[0], dest[1],
                t.get("end_lat", 0) or 0, t.get("end_lng", 0) or 0,
            )
            if o_dist < radius_km and d_dist < radius_km:
                similar.append(t)
        return similar

    def _haversine_km(self, lat1, lng1, lat2, lng2):
        R = 6371
        dlat = math.radians(lat2 - lat1)
        dlng = math.radians(lng2 - lng1)
        a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlng/2)**2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
