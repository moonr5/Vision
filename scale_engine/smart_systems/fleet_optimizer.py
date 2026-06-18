"""
Scale Engine — Fleet Optimization Engine.
Dispatch, load balancing, fuel-efficiency, and cost-minimization
recommendations across the entire fleet.
"""

from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
from collections import defaultdict
import math


class FleetOptimizer:
    """
    Multi-objective fleet optimization:

    1. Driver-to-order matching (minimize risk, maximize efficiency)
    2. Load balancing (distribute work evenly)
    3. Fuel cost minimization (route + driver + vehicle optimization)
    4. Idle reduction (identify excessive idling patterns)
    5. Maintenance scheduling optimization
    """

    def __init__(self):
        pass

    # ── Driver-to-Order Matching ─────────────────────────────────────────

    def recommend_driver_for_order(
        self,
        order: Dict[str, Any],
        available_drivers: List[Dict[str, Any]],
        strategy: str = "balanced",
    ) -> List[Dict[str, Any]]:
        """
        Rank drivers for an order.

        Strategies:
          - "safety_first": prioritize lowest risk
          - "fastest": prioritize fastest completion
          - "fuel_efficient": prioritize best fuel economy
          - "balanced": weighted multi-objective (default)
        """
        results = []
        distance = order.get("distance_km", 50)

        for driver in available_drivers:
            safety = driver.get("safety_score", 80) / 100
            fuel_eff = driver.get("fuel_efficiency", 7.0)  # km/L
            pace = 45.0 / max(driver.get("avg_speed", 45), 10)  # normalize

            # Strategy weights
            weights = {
                "safety_first":    {"safety": 0.60, "efficiency": 0.20, "speed": 0.20},
                "fastest":         {"safety": 0.20, "efficiency": 0.10, "speed": 0.70},
                "fuel_efficient":  {"safety": 0.20, "efficiency": 0.65, "speed": 0.15},
                "balanced":        {"safety": 0.40, "efficiency": 0.30, "speed": 0.30},
            }
            w = weights.get(strategy, weights["balanced"])

            safety_score = safety * w["safety"]
            eff_score = (fuel_eff / 10) * w["efficiency"]  # normalized to ~0-1
            speed_score = (1 / max(pace, 0.5)) * w["speed"]

            total_score = round((safety_score + eff_score + speed_score) * 100, 1)

            # Fuel cost estimate
            fuel_cost = round((distance / max(fuel_eff, 1)) * 1.2, 2)  # $1.2/L estimate

            results.append({
                "driver_id": driver.get("driver_id") or driver.get("id"),
                "driver_name": driver.get("driver_name") or driver.get("name"),
                "score": total_score,
                "safety_score": driver.get("safety_score", 80),
                "estimated_fuel_cost_usd": fuel_cost,
                "estimated_duration_hours": round(distance / max(driver.get("avg_speed", 45), 10), 1),
                "strategy": strategy,
            })

        results.sort(key=lambda r: r["score"], reverse=True)
        return results

    # ── Load Balancing ───────────────────────────────────────────────────

    def balance_load(
        self, orders: List[Dict], drivers: List[Dict],
    ) -> Dict[str, Any]:
        """
        Distribute orders across drivers to balance workload.
        Minimizes max-min disparity in assigned distance/duration.
        """
        if not drivers or not orders:
            return {"assignments": [], "unassigned_orders": len(orders)}

        # Greedy assignment: most constrained driver gets easiest order
        driver_loads = {d.get("id") or d.get("driver_id"): {"driver": d, "assigned": [], "total_km": 0, "total_min": 0} for d in drivers}

        remaining = list(orders)
        while remaining:
            # Find driver with least load
            least_loaded = min(driver_loads.values(), key=lambda x: x["total_km"])
            # Find closest order to that driver's last assignment
            order = remaining.pop(0)
            dist = order.get("distance_km", 50)
            dur = order.get("estimated_duration_min", 60)
            least_loaded["assigned"].append(order)
            least_loaded["total_km"] += dist
            least_loaded["total_min"] += dur

        assignments = [
            {"driver_id": did, "driver_name": v["driver"].get("name") or v["driver"].get("driver_name", did),
             "order_count": len(v["assigned"]), "total_km": round(v["total_km"], 1),
             "total_hours": round(v["total_min"] / 60, 1)}
            for did, v in driver_loads.items()
        ]

        # Fairness metric
        kms = [a["total_km"] for a in assignments if a["order_count"] > 0]
        fairness = round(1 - (max(kms) - min(kms)) / max(max(kms), 1), 2) if kms else 1.0

        return {"assignments": assignments, "fairness_score": fairness, "unassigned": 0}

    # ── Fuel Optimization ────────────────────────────────────────────────

    def fuel_optimization_report(self, drivers: List[Dict]) -> Dict[str, Any]:
        """Identify fuel savings opportunities across the fleet."""
        report = {
            "total_estimated_monthly_savings_usd": 0,
            "recommendations": [],
            "worst_performers": [],
        }

        for d in drivers:
            eff = d.get("fuel_efficiency", 7.0)
            if eff < 5.0:
                report["worst_performers"].append({
                    "driver": d.get("name") or d.get("driver_name"),
                    "fuel_efficiency_kmpl": eff,
                    "potential_improvement": "15-20% with eco-driving coaching",
                })

        # Idling cost estimate
        for d in drivers:
            idle_count = d.get("excessive_idling_count", 0)
            if idle_count > 5:
                cost = round(idle_count * 0.5, 2)  # ~$0.50 fuel per idling event
                report["recommendations"].append(
                    f"{d.get('name') or d.get('driver_name')}: reduce {idle_count} idling "
                    f"events to save ~${cost}/month"
                )
                report["total_estimated_monthly_savings_usd"] += cost

        report["total_estimated_monthly_savings_usd"] = round(report["total_estimated_monthly_savings_usd"], 2)
        return report

    # ── Fleet-wide KPIs ──────────────────────────────────────────────────

    def compute_fleet_kpis(self, drivers: List[Dict], orders: List[Dict] = None) -> Dict[str, Any]:
        """Compute fleet-wide key performance indicators."""
        if not drivers:
            return {"error": "No driver data"}

        scores = [d.get("safety_score", 80) for d in drivers if d.get("safety_score") is not None]
        fuel_effs = [d.get("fuel_efficiency", 7) for d in drivers if d.get("fuel_efficiency", 0) > 0]
        trips = [d.get("total_trips", 0) for d in drivers]

        return {
            "total_drivers": len(drivers),
            "active_drivers": sum(1 for d in drivers if d.get("status") == "active"),
            "avg_safety_score": round(sum(scores) / max(len(scores), 1), 1) if scores else 0,
            "best_safety_score": max(scores) if scores else 0,
            "worst_safety_score": min(scores) if scores else 0,
            "avg_fuel_efficiency_kmpl": round(sum(fuel_effs) / max(len(fuel_effs), 1), 2) if fuel_effs else 0,
            "total_trips_completed": sum(trips),
            "orders": len(orders) if orders else 0,
            "computed_at": datetime.utcnow().isoformat(),
        }
