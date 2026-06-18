"""
Scale Engine — Digital Twin Backend.
Virtual model of each vehicle updating in real-time from live telemetry.
Maintains state, predicts near-future values, simulates what-if scenarios.
"""

import asyncio
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from dataclasses import dataclass, field


@dataclass
class VehicleTwin:
    vehicle_id: str
    device_id: str
    driver_id: Optional[str] = None

    # Live state (updated per telemetry reading)
    lat: float = 0.0
    lng: float = 0.0
    speed: float = 0.0
    heading: float = 0.0
    fuel_level: float = 100.0
    rpm: int = 0
    engine_load: float = 0.0
    coolant_temp: float = 90.0
    throttle: float = 0.0
    mil: bool = False

    # Derived state
    engine_running: bool = False
    in_motion: bool = False
    geofence_status: str = "unknown"

    # Health indicators
    fuel_rate_l_per_100km: float = 0.0
    estimated_range_km: float = 0.0
    engine_hours: float = 0.0
    odometer_km: float = 0.0

    # Predictive state
    predicted_fuel_empty_at: Optional[str] = None
    predicted_maintenance_at: Optional[str] = None
    next_geofence_eta_seconds: int = -1

    # History
    last_update: str = ""
    route_points: List[Dict] = field(default_factory=list)

    def update_from_telemetry(self, t: Dict[str, Any]):
        """Update twin state from a telemetry record."""
        self.lat = t.get("lat", self.lat)
        self.lng = t.get("lng", self.lng)
        self.speed = t.get("speed", self.speed) or 0
        self.fuel_level = t.get("fuel_level", self.fuel_level) or self.fuel_level
        self.rpm = t.get("rpm", self.rpm) or t.get("obd_rpm", self.rpm) or 0
        self.engine_load = t.get("engine_load", self.engine_load) or t.get("obd_engine_load", 0) or 0
        self.coolant_temp = t.get("coolant_temp", self.coolant_temp) or t.get("obd_coolant_temp", self.coolant_temp) or 90
        self.throttle = t.get("throttle", self.throttle) or 0
        self.mil = t.get("mil", self.mil)

        self.engine_running = self.rpm > 400
        self.in_motion = self.speed > 1

        # Derived
        if self.speed > 0 and self.fuel_level > 0:
            self.fuel_rate_l_per_100km = round(8.0 * (1 + self.engine_load / 100), 1)
            self.estimated_range_km = round(self.fuel_level / 100 * 60 / self.fuel_rate_l_per_100km * 100, 0)

        if self.lat and self.lng:
            self.route_points.append({
                "lat": self.lat, "lng": self.lng,
                "speed": self.speed, "ts": datetime.utcnow().isoformat(),
            })
            if len(self.route_points) > 1000:
                self.route_points = self.route_points[-500:]

        self.last_update = datetime.utcnow().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "vehicle_id": self.vehicle_id, "device_id": self.device_id,
            "driver_id": self.driver_id,
            "position": {"lat": self.lat, "lng": self.lng},
            "speed": self.speed, "heading": self.heading,
            "fuel": {"level_pct": self.fuel_level, "range_km": self.estimated_range_km},
            "engine": {"running": self.engine_running, "rpm": self.rpm,
                        "load_pct": self.engine_load, "temp_c": self.coolant_temp,
                        "mil": self.mil},
            "motion": self.in_motion,
            "derived": {"fuel_rate": self.fuel_rate_l_per_100km,
                         "estimated_range_km": self.estimated_range_km},
            "last_update": self.last_update,
            "route_point_count": len(self.route_points),
        }


class DigitalTwinEngine:
    """
    Manages a fleet of digital vehicle twins.
    Each twin is a live virtual representation fed by telemetry.
    """

    def __init__(self):
        self._twins: Dict[str, VehicleTwin] = {}
        self._history_buffer: List[Dict] = []

    def get_or_create(self, device_id: str, vehicle_id: str = None) -> VehicleTwin:
        """Get existing twin or create a new one."""
        if device_id in self._twins:
            return self._twins[device_id]
        twin = VehicleTwin(
            vehicle_id=vehicle_id or device_id,
            device_id=device_id,
        )
        self._twins[device_id] = twin
        return twin

    def update(self, telemetry: Dict[str, Any]):
        """Update the twin for a device with fresh telemetry."""
        device_id = telemetry.get("device_id", "unknown")
        twin = self.get_or_create(device_id)
        twin.update_from_telemetry(telemetry)
        self._history_buffer.append(twin.to_dict())
        if len(self._history_buffer) > 5000:
            self._history_buffer = self._history_buffer[-2000:]

    def update_batch(self, records: List[Dict[str, Any]]):
        for r in records:
            self.update(r)

    def get_twin(self, device_id: str) -> Optional[Dict]:
        twin = self._twins.get(device_id)
        return twin.to_dict() if twin else None

    def get_all_twins(self) -> List[Dict]:
        return [t.to_dict() for t in self._twins.values()]

    def get_fleet_summary(self) -> Dict[str, Any]:
        """Aggregate fleet-wide view from all twins."""
        twins = list(self._twins.values())
        if not twins:
            return {"total": 0}
        active = [t for t in twins if t.engine_running]
        avg_fuel = sum(t.fuel_level for t in twins) / len(twins)
        mil_on = [t.device_id for t in twins if t.mil]
        return {
            "total_vehicles": len(twins),
            "active_engines": len(active),
            "in_motion": sum(1 for t in twins if t.in_motion),
            "avg_fuel_level_pct": round(avg_fuel, 1),
            "mil_active_count": len(mil_on),
            "mil_devices": mil_on,
        }

    def simulate(self, device_id: str, scenario: str, params: Dict = None) -> Dict[str, Any]:
        """
        Run a what-if simulation on a twin.
        Scenarios: "fuel_exhaustion", "overheat", "collision_risk", "route_deviation"
        """
        twin = self._twins.get(device_id)
        if not twin:
            return {"error": "Twin not found"}

        scenarios = {
            "fuel_exhaustion": lambda: {
                "current_fuel_pct": twin.fuel_level,
                "current_range_km": twin.estimated_range_km,
                "time_to_empty_hours": round(twin.estimated_range_km / max(twin.speed, 1), 1) if twin.speed > 0 else "stationary",
                "nearest_refuel_km": 15.0,  # Placeholder — would query real POI data
            },
            "overheat_risk": lambda: {
                "current_temp": twin.coolant_temp,
                "trend": "rising" if twin.coolant_temp > 100 else "stable",
                "risk_level": "HIGH" if twin.coolant_temp > 110 else ("MEDIUM" if twin.coolant_temp > 100 else "LOW"),
            },
        }

        handler = scenarios.get(scenario)
        return handler() if handler else {"error": f"Unknown scenario: {scenario}"}
