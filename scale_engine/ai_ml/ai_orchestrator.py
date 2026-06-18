"""
Scale Engine — Multi-Agent AI Orchestrator.
Separate specialized AI agents for safety, security, maintenance, and logistics.
Orchestrates them and synthesizes their outputs into unified decisions.
"""

import os
import json
from typing import Dict, Any, List, Optional
from datetime import datetime
from dataclasses import dataclass, field

import google.generativeai as genai


@dataclass
class AgentSpec:
    name: str
    role: str
    system_prompt: str
    priority: int = 5  # 1=highest


class MultiAgentOrchestrator:
    """
    Orchestrates multiple specialized AI agents.

    Agents:
      1. SAFETY agent — driver behavior, risk, coaching
      2. SECURITY agent — fuel theft, cargo tampering, breaches
      3. MAINTENANCE agent — vehicle health, predictive failures
      4. LOGISTICS agent — routing, dispatch, load optimization
      5. FLEET_MANAGER agent — synthesizes all others into decisions
    """

    AGENTS = [
        AgentSpec("safety", "Driver safety & behavior analyst",
            "You are a fleet safety AI. Analyze driver behavior data and identify safety risks. "
            "Flag drivers needing coaching. Recommend specific interventions. "
            "Output JSON: {findings: [], risk_drivers: [], recommendations: []}", priority=2),
        AgentSpec("security", "Cargo & fuel security analyst",
            "You are a fleet security AI. Analyze sensor data for fuel theft, cargo tampering, "
            "and security breaches. Correlate patterns across time and location. "
            "Output JSON: {threats: [], risk_zones: [], recommendations: []}", priority=1),
        AgentSpec("maintenance", "Predictive maintenance analyst",
            "You are a vehicle maintenance AI. Analyze OBD-II trends to predict failures. "
            "Prioritize vehicles needing immediate service. Estimate repair urgency. "
            "Output JSON: {vehicles_at_risk: [], predictions: [], service_schedule: []}", priority=3),
        AgentSpec("logistics", "Route & dispatch optimizer",
            "You are a logistics AI. Optimize routes, driver assignments, and delivery schedules. "
            "Balance efficiency, safety, and cost. "
            "Output JSON: {optimized_routes: [], driver_assignments: [], efficiency_gains: []}", priority=4),
        AgentSpec("fleet_manager", "Fleet-wide decision synthesizer",
            "You are the fleet manager AI. Synthesize reports from safety, security, maintenance, "
            "and logistics agents into an executive decision brief. Prioritize actions. "
            "Output JSON: {executive_summary: str, top_actions: [], risk_matrix: {}, weekly_outlook: str}", priority=5),
    ]

    def __init__(self, gemini_api_key: str = None):
        self._api_key = gemini_api_key or os.getenv("GEMINI_API_KEY", "")
        self._model = None
        self._agent_results: Dict[str, List[Dict]] = {}
        if self._api_key:
            genai.configure(api_key=self._api_key)
            self._model = genai.GenerativeModel(model_name="gemini-2.0-flash")

    def _build_agent_model(self, agent: AgentSpec):
        """Create a model with the agent's system prompt."""
        if not self._api_key:
            return None
        return genai.GenerativeModel(
            model_name="gemini-2.0-flash",
            system_instruction=agent.system_prompt,
        )

    async def run_agent(self, agent: AgentSpec, context: str) -> Dict[str, Any]:
        """Run a single agent on the given context."""
        if not self._model:
            return {"agent": agent.name, "error": "GEMINI_API_KEY not configured"}

        prompt = f"CONTEXT:\n{context}\n\nAnalyze this data and respond in JSON."
        try:
            model = self._build_agent_model(agent)
            if not model:
                return {"agent": agent.name, "error": "Model init failed"}
            response = model.generate_content(
                prompt,
                generation_config={"temperature": 0.3, "max_output_tokens": 1000},
            )
            # Try to parse JSON from response
            text = response.text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1]
                if text.endswith("```"):
                    text = text[:-3]
            result = json.loads(text)
        except Exception as e:
            result = {"raw_response": response.text if 'response' in dir() else str(e),
                       "parse_error": str(e)}

        self._agent_results[agent.name] = self._agent_results.get(agent.name, []) + [{
            "result": result, "timestamp": datetime.utcnow().isoformat(),
        }]
        return result

    async def orchestrate(self, fleet_context: str,
                          agents_to_run: List[str] = None) -> Dict[str, Any]:
        """
        Run all (or selected) agents and have the fleet_manager synthesize.

        Returns a unified decision brief.
        """
        if agents_to_run is None:
            agents_to_run = ["safety", "security", "maintenance", "logistics"]

        specialist_agents = [a for a in self.AGENTS if a.name in agents_to_run]
        manager_agent = next(a for a in self.AGENTS if a.name == "fleet_manager")

        # Step 1: Run all specialist agents
        specialist_results = {}
        for agent in specialist_agents:
            result = await self.run_agent(agent, fleet_context)
            specialist_results[agent.name] = result

        # Step 2: Have fleet_manager synthesize
        synthesis_context = (
            f"FLEET CONTEXT:\n{fleet_context}\n\n"
            f"SPECIALIST REPORTS:\n{json.dumps(specialist_results, indent=2, default=str)}"
        )
        synthesis = await self.run_agent(manager_agent, synthesis_context)

        return {
            "specialist_reports": specialist_results,
            "executive_brief": synthesis,
            "orchestrated_at": datetime.utcnow().isoformat(),
            "agents_run": agents_to_run,
        }

    def get_agent_history(self, agent_name: str, limit: int = 10) -> List[Dict]:
        return self._agent_results.get(agent_name, [])[-limit:]
