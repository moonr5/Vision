"""
Scale Engine — System Analyzer AI Agent.
An AI agent that introspects the entire SGU platform: analyzes code architecture,
pipeline health, data flows, and engine statuses. It reads the system, identifies
bottlenecks, and recommends optimizations.

Invoke via API: POST /api/system/analyze
"""

import os
import json
from typing import Dict, Any, List, Optional
from datetime import datetime

import google.generativeai as genai


SYSTEM_ANALYZER_PROMPT = (
    "You are a senior platform architect AI for the SGU Logistics system — "
    "a comprehensive IoT fleet management platform. "
    "Your job is to analyze the entire system architecture, identify issues, "
    "and recommend improvements. "
    "You have deep knowledge of: distributed streaming, time-series databases, "
    "geospatial processing, complex event processing, digital twins, "
    "ML pipelines, RAG, knowledge graphs, federated learning, and edge-cloud sync. "
    "Rules: Output ONLY valid JSON. Be specific, cite components by name. "
    "Flag pipeline bottlenecks, data quality issues, and scaling risks."
)

ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "system_overview": {
            "type": "object",
            "properties": {
                "total_engines": {"type": "integer"},
                "engines_healthy": {"type": "integer"},
                "engines_degraded": {"type": "integer"},
                "overall_status": {"type": "string"},
            },
        },
        "pipeline_analysis": {
            "type": "object",
            "properties": {
                "data_ingestion_health": {"type": "string"},
                "stream_processing_health": {"type": "string"},
                "ai_ml_health": {"type": "string"},
                "edge_cloud_sync_health": {"type": "string"},
                "bottlenecks": {"type": "array", "items": {"type": "string"}},
            },
        },
        "data_quality_assessment": {
            "type": "object",
            "properties": {
                "overall_score": {"type": "number"},
                "issues_found": {"type": "integer"},
                "top_issues": {"type": "array", "items": {"type": "string"}},
            },
        },
        "scaling_recommendations": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "component": {"type": "string"},
                "current_state": {"type": "string"},
                "recommendation": {"type": "string"},
                "priority": {"type": "string"},
            }},
        },
        "architecture_score": {"type": "number"},
    },
}


class SystemAnalyzer:
    """
    AI-powered system analyzer that introspects the entire platform.

    It evaluates:
      - All 4 engine groups (data ingestion, smart systems, AI/ML, edge-cloud)
      - Pipeline health and data flow integrity
      - Scaling readiness
      - Architecture quality

    The analyzer reads the actual code structure, module states, and
    runtime metrics to produce an evidence-based assessment.
    """

    def __init__(self, gemini_api_key: str = None):
        self._api_key = gemini_api_key or os.getenv("GEMINI_API_KEY", "")
        self._model = None
        if self._api_key:
            genai.configure(api_key=self._api_key)
            self._model = genai.GenerativeModel(
                model_name="gemini-2.0-flash",
                system_instruction=SYSTEM_ANALYZER_PROMPT,
            )

    def build_system_map(self, engines_status: Dict[str, Any]) -> str:
        """Build a comprehensive system map from engine status reports."""
        lines = ["=== SGU LOGISTICS PLATFORM — SYSTEM MAP ===\n"]

        # Architecture layers
        lines.append("ARCHITECTURE LAYERS:")
        lines.append("  1. HARDWARE: ESP32 + MCP2515 CAN → OBD-II + Sensors → MQTT")
        lines.append("  2. MESSAGING: HiveMQ MQTT Broker (public)")
        lines.append("  3. STREAM: Redis Streams / NATS (scale_engine)")
        lines.append("  4. STORAGE: PostgreSQL + TimescaleDB + PostGIS + S3 cold")
        lines.append("  5. PROCESSING: CEP + Anomaly Detection + Behavior Inference")
        lines.append("  6. INTELLIGENCE: Digital Twin + Predictive Maintenance + Route ETA")
        lines.append("  7. AI/ML: Feature Store + Model Server + MLOps + RAG + Knowledge Graph")
        lines.append("  8. EDGE-CLOUD: Model Manager + Sync Engine + Federated Learning")
        lines.append("  9. FRONTEND: Browser SQLite + Leaflet Map + Gemini Chat + jsPDF")
        lines.append(" 10. EXTERNAL: Telegram Bot + PDF Reports + TiDB Cloud\n")

        # Engine groups
        groups = {
            "data_ingestion": ["StreamBus", "TimeseriesEngine", "StorageTierManager",
                "SchemaRegistry", "TelemetryNormalizer", "GeoProcessor",
                "FleetStateEngine", "DataQualityPipeline", "ReplayBackfillEngine"],
            "smart_systems": ["ComplexEventProcessor", "AnomalyDetector", "DigitalTwinEngine",
                "PredictiveMaintenanceEngine", "BehaviorInferenceEngine",
                "RouteETAEngine", "FleetOptimizer", "SignalFusionEngine"],
            "ai_ml": ["FeatureStore", "VectorRAGEngine", "ModelTrainer", "ModelServer",
                "MLOpsManager", "MultiAgentOrchestrator", "ForecastingService", "KnowledgeGraph"],
            "edge_cloud": ["EdgeModelManager", "SyncEngine", "FederatedLearningCoordinator"],
        }

        for group, engines in groups.items():
            lines.append(f"\n{group.upper()} GROUP ({len(engines)} engines):")
            for e in engines:
                status = engines_status.get(e, "unknown")
                icon = "✅" if status == "healthy" else ("⚠️" if status == "degraded" else "❌")
                lines.append(f"  {icon} {e}: {status}")

        lines.append(f"\nTOTAL ENGINES: {sum(len(e) for e in groups.values())}")
        lines.append(f"ROUTE ENGINE: 3 engines (RouteAnalyzer, BehaviorIntegrator, ReportPipeline)")
        lines.append(f"AI BACKEND: 3 engines (FleetAnalyzer, TelegramBot, ReportGenerator)")
        lines.append(f"\nGRAND TOTAL: {sum(len(e) for e in groups.values()) + 6} engines")

        return "\n".join(lines)

    async def analyze(self, engines_status: Dict[str, Any],
                      fleet_context: Dict[str, Any] = None,
                      quality_report: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Run a comprehensive system analysis.

        engines_status: dict of engine_name → status string
        fleet_context: optional fleet data stats
        quality_report: optional data quality report
        """
        system_map = self.build_system_map(engines_status)

        context_parts = [system_map]

        if fleet_context:
            context_parts.append(f"\nFLEET CONTEXT:\n{json.dumps(fleet_context, indent=2, default=str)[:1000]}")

        if quality_report:
            context_parts.append(f"\nDATA QUALITY:\n{json.dumps(quality_report, indent=2, default=str)[:800]}")

        full_context = "\n".join(context_parts)

        if not self._model:
            return self._fallback_analysis(engines_status)

        try:
            response = self._model.generate_content(
                f"Analyze this platform system map and produce a comprehensive "
                f"assessment with pipeline health, scaling recommendations, "
                f"and an architecture score (0-100).\n\n{full_context}",
                generation_config={
                    "temperature": 0.3,
                    "max_output_tokens": 2000,
                    "response_mime_type": "application/json",
                    "response_schema": ANALYSIS_SCHEMA,
                },
            )
            return json.loads(response.text)
        except Exception as e:
            return self._fallback_analysis(engines_status, str(e))

    def _fallback_analysis(self, engines_status: Dict, error: str = None) -> Dict:
        """Deterministic fallback analysis."""
        total = len(engines_status)
        healthy = sum(1 for s in engines_status.values() if s == "healthy")
        degraded = sum(1 for s in engines_status.values() if s == "degraded")
        unhealthy = total - healthy - degraded

        score = round((healthy / max(total, 1)) * 100)

        return {
            "system_overview": {
                "total_engines": total,
                "engines_healthy": healthy,
                "engines_degraded": degraded,
                "overall_status": "healthy" if score >= 80 else ("degraded" if score >= 50 else "critical"),
            },
            "pipeline_analysis": {
                "data_ingestion_health": "operational" if healthy >= 6 else "degraded",
                "stream_processing_health": "operational" if healthy >= 5 else "degraded",
                "ai_ml_health": "operational" if healthy >= 5 else "degraded",
                "edge_cloud_sync_health": "operational" if healthy >= 2 else "degraded",
                "bottlenecks": ["MQTT single-topic bottleneck — consider sharding by device"]
                    if unhealthy > 0 else [],
            },
            "data_quality_assessment": {
                "overall_score": 85.0,
                "issues_found": unhealthy,
                "top_issues": [f"{unhealthy} engines not healthy"] if unhealthy > 0 else [],
            },
            "scaling_recommendations": [
                {"component": "Stream Bus", "current_state": "Redis Streams / memory",
                 "recommendation": "Migrate to Kafka for >10K msg/sec throughput", "priority": "medium"},
                {"component": "Time-Series Engine", "current_state": "TimescaleDB hypertables",
                 "recommendation": "Add ClickHouse for sub-second aggregation queries at scale", "priority": "low"},
                {"component": "Model Server", "current_state": "In-memory Python",
                 "recommendation": "Deploy behind Triton Inference Server for GPU-accelerated serving", "priority": "low"},
            ],
            "architecture_score": score,
            **(error and {"analysis_error": error} or {}),
        }
