import google.generativeai as genai
from typing import Optional, Dict, Any


SYSTEM_PROMPT = (
    "You are an expert fleet management AI for SGU Logistics. "
    "Analyze real-time fleet data to provide precise, actionable insights. "
    "Rules: plain text only — no markdown, no dashes, no emojis. "
    "Maximum 3 sentences. Always reference specific numbers from the fleet data when available. "
    "Focus on what the fleet manager should DO. "
    "Never say 'As an AI', 'Great question', or any filler phrase. "
    "If data shows a risk or declining trend, flag it directly and briefly."
)


class FleetAnalyzer:
    def __init__(self, api_key: str):
        genai.configure(api_key=api_key)
        self._model = genai.GenerativeModel(
            model_name="gemini-2.0-flash",
            system_instruction=SYSTEM_PROMPT,
        )

    def _build_context_text(self, ctx: Optional[Dict[str, Any]]) -> str:
        if not ctx:
            return ""

        lines = []

        drivers = ctx.get("drivers", {})
        if drivers.get("total", 0) > 0:
            lines.append(
                f"Drivers: {drivers.get('active', 0)} active / {drivers['total']} total, "
                f"average safety score {drivers.get('avgScore', 'N/A')}"
            )

        top = ctx.get("topDrivers", [])
        if top:
            lines.append(
                "Top performers: "
                + ", ".join(f"{d['name']} score {d.get('safety_score', '?')}" for d in top[:3])
            )

        bottom = ctx.get("bottomDrivers", [])
        if bottom:
            lines.append(
                "Needs attention: "
                + ", ".join(f"{d['name']} score {d.get('safety_score', '?')}" for d in bottom[:3])
            )

        orders = ctx.get("orders", {})
        if orders.get("total", 0) > 0:
            lines.append(f"Orders: {orders.get('active', 0)} active / {orders['total']} total")

        devices = ctx.get("devices", {})
        if devices.get("total", 0) > 0:
            lines.append(
                f"Devices: {devices.get('online', 0)} online / {devices['total']} total"
            )

        alerts = ctx.get("alerts", [])
        if alerts:
            critical = sum(1 for a in alerts if a.get("type") == "CRITICAL")
            warnings = sum(1 for a in alerts if a.get("type") == "WARNING")
            lines.append(f"Active alerts: {critical} critical, {warnings} warnings")
            for a in alerts[:3]:
                lines.append(f"  - {a.get('event', '')} (device: {a.get('device_id', '?')})")

        events = ctx.get("events", {})
        if events.get("total", 0) > 0:
            lines.append(
                f"Events logged: {events['total']} total, {events.get('critical', 0)} critical"
            )

        return "\n".join(lines)

    def analyze(self, question: str, context: Optional[Dict[str, Any]]) -> str:
        ctx_text = self._build_context_text(context)

        prompt = (
            f"Fleet data:\n{ctx_text}\n\nQuestion: {question}"
            if ctx_text
            else question
        )

        response = self._model.generate_content(prompt)
        return response.text.strip()
