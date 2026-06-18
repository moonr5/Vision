"""
Scale Engine — Forecasting Service.
Predicts fuel use, downtime, demand, and delivery delays using
historical trends + current telemetry.
"""

import math
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from collections import defaultdict


class ForecastingService:
    """
    Multi-horizon forecasting for fleet operations.

    Forecasts:
      - Fuel consumption (next trip, daily, weekly)
      - Vehicle downtime probability
      - Delivery delay probability
      - Driver availability
      - Demand spikes (time-of-day, day-of-week patterns)
    """

    def __init__(self):
        self._history: Dict[str, List[Dict]] = defaultdict(list)
        self._forecasts: Dict[str, Dict] = {}

    # ── Ingestion ────────────────────────────────────────────────────────

    def ingest(self, category: str, record: Dict[str, Any]):
        """Ingest historical data for a category."""
        self._history[category].append({**record, "ingested_at": datetime.utcnow().isoformat()})
        if len(self._history[category]) > 5000:
            self._history[category] = self._history[category][-2000:]

    # ── Forecasts ────────────────────────────────────────────────────────

    def forecast_fuel_consumption(
        self, driver_id: str, distance_km: float, hours_ahead: int = 24,
    ) -> Dict[str, Any]:
        """Predict fuel consumption for an upcoming trip."""
        # Get driver's historical fuel efficiency
        trips = self._history.get("trips", [])
        driver_trips = [t for t in trips if t.get("driver_id") == driver_id]

        if driver_trips:
            avg_eff = sum(t.get("fuel_efficiency", 7.0) or 7.0 for t in driver_trips) / len(driver_trips)
        else:
            avg_eff = 7.5  # fleet default

        fuel_liters = round(distance_km / max(avg_eff, 1), 1)
        cost = round(fuel_liters * 1.2, 2)  # $1.2/L

        return {
            "driver_id": driver_id,
            "distance_km": distance_km,
            "fuel_liters": fuel_liters,
            "estimated_cost_usd": cost,
            "efficiency_used_kmpl": round(avg_eff, 2),
            "confidence": "medium" if driver_trips else "low",
        }

    def forecast_downtime_risk(self, device_id: str) -> Dict[str, Any]:
        """Predict probability of vehicle downtime in next 7 days."""
        maintenance = self._history.get("maintenance", [])
        device_events = [m for m in maintenance if m.get("device_id") == device_id]

        # Simple risk model: recent MIL + high coolant temp → higher risk
        risk = 0.05  # base 5%
        for e in device_events[-10:]:
            if e.get("mil"):
                risk += 0.15
            if e.get("coolant_temp", 90) > 110:
                risk += 0.10
            if e.get("voltage", 14) < 12:
                risk += 0.10

        risk = min(risk, 0.95)

        return {
            "device_id": device_id,
            "downtime_probability_7d": round(risk, 2),
            "risk_level": "HIGH" if risk > 0.5 else ("MEDIUM" if risk > 0.2 else "LOW"),
            "estimated_downtime_hours": round(risk * 48, 0) if risk > 0.1 else 0,
        }

    def forecast_delivery_delay(
        self, route: Dict[str, Any], driver: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Predict delivery delay probability for a route+driver pairing."""
        distance = route.get("distance_km", 50) or 50
        duration = route.get("estimated_duration_min", 60) or 60
        driver_score = (driver.get("safety_score", 80) or 80) / 100

        # Delay factors
        hour = datetime.utcnow().hour + 7  # Jakarta time
        hour = hour % 24
        rush_hour = 1.3 if hour in range(7, 10) or hour in range(16, 19) else 1.0
        driver_factor = 1.0 + (1 - driver_score) * 0.5  # worse drivers = more delays
        distance_factor = 1.0 + max(0, (distance - 100) / 200)  # longer = more uncertainty

        adjusted_duration = duration * rush_hour * driver_factor * distance_factor
        delay_prob = min(0.8, (adjusted_duration / max(duration, 1) - 1) * 2)

        return {
            "planned_duration_min": duration,
            "adjusted_duration_min": round(adjusted_duration, 0),
            "delay_probability": round(delay_prob, 2),
            "factors": {
                "rush_hour_multiplier": rush_hour,
                "driver_factor": round(driver_factor, 2),
                "distance_factor": round(distance_factor, 2),
            },
        }

    def forecast_demand(self) -> Dict[str, Any]:
        """Forecast delivery demand for the next 24 hours."""
        orders = self._history.get("orders", [])
        now = datetime.utcnow()

        hourly = defaultdict(int)
        for o in orders:
            try:
                ts = datetime.fromisoformat(o.get("created_at", "").replace("Z", "+00:00"))
                if (now - ts).days < 60:  # last 60 days
                    h = ts.hour
                    hourly[h] += 1
            except Exception:
                pass

        # Normalize to get hourly distribution
        total = sum(hourly.values()) or 1
        distribution = {h: round(count / total, 3) for h, count in sorted(hourly.items())}

        # Peak hours
        peak_hours = sorted(distribution, key=distribution.get, reverse=True)[:3]

        return {
            "forecast_period": "next 24 hours",
            "expected_total_orders": sum(hourly.values()) // max(len(hourly) or 1, 1),
            "hourly_distribution": distribution,
            "peak_hours": peak_hours,
            "peak_hour_demand_pct": round(distribution.get(peak_hours[0], 0) * 100, 0) if peak_hours else 0,
        }

    def get_all_forecasts(self) -> Dict[str, Any]:
        return {
            "fuel": self._forecasts.get("fuel"),
            "downtime": self._forecasts.get("downtime"),
            "delay": self._forecasts.get("delay"),
            "demand": self._forecasts.get("demand"),
            "generated_at": datetime.utcnow().isoformat(),
        }
