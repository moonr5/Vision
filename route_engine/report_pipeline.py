"""
Route Engine — Comprehensive Report Pipeline.

Multi-stage pipeline that produces actionable fleet route reports:

  Stage 1 — DATA COLLECTION
    Gather driver profiles, telemetry, trip history, behaviour events
    from PostgreSQL.

  Stage 2 — ANALYSIS
    Score each driver-route pairing through the Behaviour Integrator.
    Compute risk indices, projected safety scores, and suitability flags.

  Stage 3 — AI SYNTHESIS
    Feed the analysis into Gemini to produce a narrative fleet report
    with ranked recommendations, risk alerts, and optimization suggestions.

  Stage 4 — OUTPUT
    Return structured JSON (for API consumers) and/or generate a
    downloadable report document.

All stages run sequentially; each stage's output is the next stage's input.
"""

import io
import json
import os
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

import google.generativeai as genai

from behavior_integrator import BehaviorIntegrator

# ── Report generation system prompt ─────────────────────────────────────────

REPORT_SYSTEM_PROMPT = (
    "You are a senior fleet analyst for SGU Logistics. "
    "You produce clear, data-dense route optimization reports. "
    "Rules: "
    "  - Output ONLY valid JSON (no markdown, no code fences). "
    "  - Be direct. Cite specific numbers. "
    "  - Flag high-risk driver-route pairings explicitly. "
    "  - Recommend concrete actions, not vague suggestions. "
    "  - Include a 2-3 sentence executive summary at the top."
)

REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "executive_summary": {"type": "string"},
        "fleet_overview": {
            "type": "object",
            "properties": {
                "active_drivers": {"type": "integer"},
                "fleet_avg_safety_score": {"type": "number"},
                "total_routes_analyzed": {"type": "integer"},
                "high_risk_pairings": {"type": "integer"},
                "recommended_pairings": {"type": "integer"},
            },
        },
        "top_recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "driver_name": {"type": "string"},
                    "route_name": {"type": "string"},
                    "projected_safety_score": {"type": "number"},
                    "rationale": {"type": "string"},
                },
            },
        },
        "risk_alerts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "driver_name": {"type": "string"},
                    "route_name": {"type": "string"},
                    "risk_level": {"type": "string"},
                    "concern": {"type": "string"},
                    "recommended_action": {"type": "string"},
                },
            },
        },
        "optimization_suggestions": {
            "type": "array",
            "items": {"type": "string"},
        },
        "report_metadata": {
            "type": "object",
            "properties": {
                "generated_at": {"type": "string"},
                "data_period_days": {"type": "integer"},
            },
        },
    },
    "required": [
        "executive_summary", "fleet_overview", "top_recommendations",
        "risk_alerts", "optimization_suggestions", "report_metadata",
    ],
}


class ReportPipeline:
    """
    Orchestrates the multi-stage route analysis report pipeline.
    """

    def __init__(self, gemini_api_key: str):
        genai.configure(api_key=gemini_api_key)
        self._model = genai.GenerativeModel(
            model_name="gemini-2.0-flash",
            system_instruction=REPORT_SYSTEM_PROMPT,
        )
        self._behavior = BehaviorIntegrator()

    # ── Stage 1: Data Collection ─────────────────────────────────────────

    async def collect(
        self,
        driver_profiles: List[Dict[str, Any]],
        route_candidates: List[Tuple[str, Dict[str, Any]]],
        fleet_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Collect and normalise all inputs needed for the report.

        driver_profiles: list of driver profile dicts from db.get_driver_behavior_profile()
        route_candidates: list of (route_label, route_attributes_dict) tuples
        fleet_context: optional fleet benchmark data
        """
        stage1 = {
            "driver_count": len(driver_profiles),
            "route_count": len(route_candidates),
            "drivers": [],
            "routes": [],
            "fleet_context": fleet_context or {},
            "collected_at": datetime.utcnow().isoformat(),
        }

        # Normalise driver data
        for dp in driver_profiles:
            stage1["drivers"].append({
                "driver_id": dp.get("driver_id"),
                "driver_name": dp.get("driver_name"),
                "safety_score": dp.get("safety_score", 100),
                "total_events": dp.get("total_events", 0),
                "fuel_efficiency": dp.get("fuel_efficiency", 0),
                "total_trips": dp.get("total_trips", 0),
                "risk_index": self._behavior.compute_risk_index(dp),
            })

        # Normalise route data
        for label, attrs in route_candidates:
            stage1["routes"].append({
                "label": label,
                "distance_km": attrs.get("distance_km", 0),
                "estimated_duration_min": attrs.get("estimated_duration_min", 0),
                "hazard_count": len(attrs.get("hazard_zones", [])),
                "hazard_profile": attrs.get("hazard_profile", "unknown"),
            })

        return stage1

    # ── Stage 2: Analysis ────────────────────────────────────────────────

    def analyze(
        self,
        stage1_data: Dict[str, Any],
        driver_profiles: List[Dict[str, Any]],
        route_candidates: List[Tuple[str, Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """
        Run behaviour integration on every driver × route combination.
        Produces a cross-product scoring matrix.
        """
        pairings = []

        for dp in driver_profiles:
            for label, attrs in route_candidates:
                suitability = self._behavior.driver_suitability(dp, attrs)
                pairings.append({
                    "driver_id": dp.get("driver_id"),
                    "driver_name": dp.get("driver_name"),
                    "route_label": label,
                    "route_name": attrs.get("route_name", label),
                    "distance_km": attrs.get("distance_km", 0),
                    "risk_index": suitability["risk_index"],
                    "projected_safety_score": suitability["projected_safety_score"],
                    "suitable": suitability["suitable"],
                    "recommendation": suitability["recommendation"],
                    "rationale": suitability["rationale"],
                    "concerns": suitability["behavioural_concerns"],
                    "hazard_matches": suitability["route_hazard_matches"],
                })

        # Sort: best pairings first
        pairings.sort(
            key=lambda p: (p["suitable"], -p["projected_safety_score"]),
            reverse=True,
        )

        high_risk = [p for p in pairings if not p["suitable"]]
        recommended = [p for p in pairings if p["suitable"]]

        return {
            "pairings": pairings,
            "recommended_count": len(recommended),
            "high_risk_count": len(high_risk),
            "best_pairing": recommended[0] if recommended else None,
            "worst_pairing": high_risk[-1] if high_risk else (pairings[-1] if pairings else None),
            "high_risk_pairings": high_risk,
            "analyzed_at": datetime.utcnow().isoformat(),
        }

    # ── Stage 3: AI Synthesis ────────────────────────────────────────────

    async def synthesize(
        self,
        stage2_data: Dict[str, Any],
        fleet_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Feed the analyzed pairings into Gemini for narrative synthesis.
        Returns a structured JSON report.
        """
        pairings = stage2_data.get("pairings", [])

        if not pairings:
            return self._empty_report()

        # Build concise context for the AI
        top_n = pairings[:5]
        risk_n = stage2_data.get("high_risk_pairings", [])[:5]

        context_lines = ["FLEET ROUTE ANALYSIS DATA:"]
        context_lines.append(f"Total driver-route pairings analyzed: {len(pairings)}")
        context_lines.append(f"Recommended pairings: {stage2_data.get('recommended_count', 0)}")
        context_lines.append(f"High-risk pairings: {stage2_data.get('high_risk_count', 0)}")

        if fleet_context:
            context_lines.append(f"Fleet avg safety score: {fleet_context.get('fleet_avg_score', 'N/A')}")

        context_lines.append("\nTOP PAIRINGS:")
        for p in top_n:
            context_lines.append(
                f"  {p['driver_name']} → {p['route_name']} ({p['route_label']}): "
                f"safety {p['projected_safety_score']}/100, risk {p['risk_index']}, "
                f"suitable={p['suitable']}"
            )

        if risk_n:
            context_lines.append("\nHIGH-RISK PAIRINGS:")
            for p in risk_n:
                concerns = [c['behaviour'] for c in p.get('concerns', [])]
                context_lines.append(
                    f"  ⚠ {p['driver_name']} → {p['route_name']} ({p['route_label']}): "
                    f"safety {p['projected_safety_score']}/100, concerns: {', '.join(concerns)}"
                )

        prompt = (
            "Generate a comprehensive fleet route optimization report from this analysis. "
            "Include: executive summary, fleet overview stats, top 3 recommended driver-route "
            "pairings, risk alerts for any high-risk pairings, and 2-4 optimization suggestions.\n\n"
            + "\n".join(context_lines)
        )

        try:
            response = self._model.generate_content(
                prompt,
                generation_config={
                    "temperature": 0.4,
                    "max_output_tokens": 2000,
                    "response_mime_type": "application/json",
                    "response_schema": REPORT_SCHEMA,
                },
            )
            report = json.loads(response.text)
        except Exception:
            report = self._fallback_report(stage2_data, fleet_context)

        return report

    # ── Stage 4: Output ──────────────────────────────────────────────────

    def finalize(
        self,
        stage3_report: Dict[str, Any],
        stage2_data: Dict[str, Any],
        format: str = "json",
    ) -> Dict[str, Any]:
        """
        Produce the final output in the requested format.
        Currently supports 'json' with full pairings embedded.
        """
        result = {
            "report": stage3_report,
            "detailed_pairings": stage2_data.get("pairings", []),
            "best_pairing": stage2_data.get("best_pairing"),
            "high_risk_alerts": [
                {
                    "driver_name": p["driver_name"],
                    "route_label": p["route_label"],
                    "route_name": p["route_name"],
                    "risk_index": p["risk_index"],
                    "projected_safety_score": p["projected_safety_score"],
                    "concerns": [c["behaviour"] for c in p.get("concerns", [])],
                    "rationale": p["rationale"],
                }
                for p in stage2_data.get("high_risk_pairings", [])[:10]
            ],
            "pipeline_metadata": {
                "pipeline_version": "1.0.0",
                "stages_completed": ["collect", "analyze", "synthesize", "finalize"],
                "generated_at": datetime.utcnow().isoformat(),
            },
        }

        return result

    # ── Full pipeline runner ─────────────────────────────────────────────

    async def run_full_pipeline(
        self,
        driver_profiles: List[Dict[str, Any]],
        route_candidates: List[Tuple[str, Dict[str, Any]]],
        fleet_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Execute all 4 stages sequentially.
        This is the primary entry point for API consumers.
        """
        # Stage 1
        stage1 = await self.collect(driver_profiles, route_candidates, fleet_context)

        # Stage 2
        stage2 = self.analyze(stage1, driver_profiles, route_candidates)

        # Stage 3
        stage3 = await self.synthesize(stage2, fleet_context)

        # Stage 4
        return self.finalize(stage3, stage2)

    # ── Fallback / empty reports ─────────────────────────────────────────

    def _empty_report(self) -> Dict[str, Any]:
        return {
            "executive_summary": "No data available for report generation.",
            "fleet_overview": {
                "active_drivers": 0,
                "fleet_avg_safety_score": 0,
                "total_routes_analyzed": 0,
                "high_risk_pairings": 0,
                "recommended_pairings": 0,
            },
            "top_recommendations": [],
            "risk_alerts": [],
            "optimization_suggestions": [],
            "report_metadata": {
                "generated_at": datetime.utcnow().isoformat(),
                "data_period_days": 90,
            },
        }

    def _fallback_report(
        self,
        stage2_data: Dict[str, Any],
        fleet_context: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Deterministic report when AI is unavailable."""
        pairings = stage2_data.get("pairings", [])
        top_3 = pairings[:3]
        risk_alerts = stage2_data.get("high_risk_pairings", [])[:5]

        return {
            "executive_summary": (
                f"Analyzed {len(pairings)} driver-route combinations. "
                f"{stage2_data.get('recommended_count', 0)} recommended, "
                f"{stage2_data.get('high_risk_count', 0)} flagged as high risk."
            ),
            "fleet_overview": {
                "active_drivers": len(set(p["driver_id"] for p in pairings)),
                "fleet_avg_safety_score": fleet_context.get("fleet_avg_score", 0) if fleet_context else 0,
                "total_routes_analyzed": len(pairings),
                "high_risk_pairings": stage2_data.get("high_risk_count", 0),
                "recommended_pairings": stage2_data.get("recommended_count", 0),
            },
            "top_recommendations": [
                {
                    "driver_name": p["driver_name"],
                    "route_name": f"{p['route_name']} ({p['route_label']})",
                    "projected_safety_score": p["projected_safety_score"],
                    "rationale": p["rationale"],
                }
                for p in top_3
            ],
            "risk_alerts": [
                {
                    "driver_name": p["driver_name"],
                    "route_name": f"{p['route_name']} ({p['route_label']})",
                    "risk_level": "HIGH" if p["risk_index"] > 0.5 else "MEDIUM",
                    "concern": ", ".join(c["behaviour"] for c in p.get("concerns", [])[:2]),
                    "recommended_action": (
                        "Reassign driver to a lower-risk route"
                        if p["risk_index"] > 0.5
                        else "Monitor closely and consider coaching"
                    ),
                }
                for p in risk_alerts
            ],
            "optimization_suggestions": [
                "Assign drivers with high safety scores to longer arterial routes.",
                "Pair drivers with harsh braking history to routes with fewer sharp curves.",
                "Schedule urban deliveries during off-peak hours where possible.",
                "Track fuel efficiency per route to refine estimates over time.",
            ],
            "report_metadata": {
                "generated_at": datetime.utcnow().isoformat(),
                "data_period_days": 90,
            },
        }
