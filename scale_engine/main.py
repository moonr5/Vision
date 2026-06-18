"""
Scale Engine — Unified FastAPI Application.
Single entry point for all 28 backend engines. Zero frontend changes required.
Made by Monzer · github.com/moonr5/Vision

Engine groups and their endpoints:

  DATA INGESTION (9 engines)
    POST /api/stream/publish           — Publish to distributed stream bus
    GET  /api/stream/stats             — Stream bus statistics
    GET  /api/timeseries/device/{id}   — Time-series queries for a device
    GET  /api/timeseries/fleet         — Fleet-wide aggregates
    GET  /api/storage/stats            — Hot/warm/cold storage stats
    POST /api/schema/validate          — Validate payload against schema
    GET  /api/schema/violations        — Recent schema violations
    POST /api/normalize                — Normalize raw telemetry
    POST /api/geo/point-in-fence       — Check point against geofences
    POST /api/geo/corridor             — Analyze route corridor
    GET  /api/fleet/state              — Current fleet state projection
    GET  /api/fleet/state/device/{id}  — Single device state
    GET  /api/quality/check            — Run all data quality checks
    GET  /api/quality/device/{id}      — Single device quality
    POST /api/replay/start             — Start historical replay
    GET  /api/replay/status            — Replay job status

  SMART SYSTEMS (8 engines)
    POST /api/cep/ingest               — Ingest telemetry for CEP
    GET  /api/cep/alerts               — Recent CEP alerts
    POST /api/anomaly/detect           — Detect anomalies in telemetry
    GET  /api/anomaly/baseline/{id}    — Get anomaly baseline for device
    POST /api/twin/update              — Update digital twin
    GET  /api/twin/{device_id}         — Get digital twin state
    GET  /api/twin/fleet               — Fleet-wide twin summary
    POST /api/maintenance/predict      — Predict maintenance needs
    GET  /api/maintenance/fleet        — Fleet health report
    POST /api/behavior/score           — Compute server-side behavior score
    GET  /api/behavior/compare/{id}    — Compare driver to fleet
    POST /api/eta/compute              — Compute route ETA
    POST /api/optimize/driver-match    — Match driver to order
    POST /api/optimize/load-balance    — Load balance orders
    GET  /api/optimize/kpis            — Fleet KPIs
    POST /api/fusion/ingest            — Ingest signal for fusion
    GET  /api/fusion/decisions         — Get fused decisions

  AI/ML (8 engines)
    POST /api/features/compute         — Compute feature vectors
    GET  /api/features/{type}/{id}     — Get features
    POST /api/rag/index                — Index document into RAG
    POST /api/rag/search               — Semantic search
    POST /api/models/train             — Train a model
    POST /api/models/predict           — Run inference
    GET  /api/models/list              — List trained models
    GET  /api/mlops/drift              — Check model drift
    POST /api/mlops/rollback           — Rollback model
    POST /api/orchestrator/run         — Run multi-agent orchestration
    POST /api/forecast/fuel            — Forecast fuel consumption
    POST /api/forecast/delay           — Forecast delivery delay
    POST /api/graph/add-node           — Add knowledge graph node
    GET  /api/graph/related            — Query related nodes

  EDGE-CLOUD (3 engines)
    POST /api/edge/models/create       — Create edge model
    POST /api/edge/models/rollout      — Stage model rollout
    POST /api/edge/models/confirm      — Confirm device update
    POST /api/sync/push-to-edge        — Push cloud data to edge
    POST /api/sync/ingest-from-edge    — Ingest edge data to cloud
    POST /api/fl/start-round           — Start federated learning round
    POST /api/fl/submit-update         — Submit local model update
    POST /api/fl/aggregate             — Aggregate updates

  SYSTEM ANALYZER
    POST /api/system/analyze           — AI-powered full system analysis
"""

import os
import json
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any, List

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ── Internal imports ──────────────────────────────────────────────────────
from scale_engine import db

# Data ingestion
from scale_engine.data_ingestion import (
    StreamBus, StreamConfig, TimeseriesEngine, StorageTierManager,
    SchemaRegistry, TelemetryNormalizer, GeoProcessor,
    FleetStateEngine, DataQualityPipeline, ReplayBackfillEngine, ReplayConfig,
)

# Smart systems
from scale_engine.smart_systems import (
    ComplexEventProcessor, AnomalyDetector, DigitalTwinEngine,
    PredictiveMaintenanceEngine, BehaviorInferenceEngine,
    RouteETAEngine, FleetOptimizer,
    SignalFusionEngine, SignalSource, SignalPriority,
)

# AI/ML
from scale_engine.ai_ml import (
    FeatureStore, VectorRAGEngine, ModelTrainer, ModelServer,
    MLOpsManager, MultiAgentOrchestrator,
    ForecastingService, KnowledgeGraph,
)

# Edge-cloud
from scale_engine.edge_cloud import (
    EdgeModelManager, SyncEngine, FederatedLearningCoordinator,
)

# System analyzer
from scale_engine.system_analyzer import SystemAnalyzer

load_dotenv()

# ── Global engine instances (lazy init) ───────────────────────────────────
_stream_bus: Optional[StreamBus] = None
_timeseries: Optional[TimeseriesEngine] = None
_storage_tiers: Optional[StorageTierManager] = None
_schema_registry: Optional[SchemaRegistry] = None
_normalizer: Optional[TelemetryNormalizer] = None
_geo_processor: Optional[GeoProcessor] = None
_fleet_state: Optional[FleetStateEngine] = None
_data_quality: Optional[DataQualityPipeline] = None
_replay_engine: Optional[ReplayBackfillEngine] = None

_cep: Optional[ComplexEventProcessor] = None
_anomaly: Optional[AnomalyDetector] = None
_digital_twin: Optional[DigitalTwinEngine] = None
_predictive_maint: Optional[PredictiveMaintenanceEngine] = None
_behavior_inference: Optional[BehaviorInferenceEngine] = None
_route_eta: Optional[RouteETAEngine] = None
_fleet_optimizer: Optional[FleetOptimizer] = None
_signal_fusion: Optional[SignalFusionEngine] = None

_feature_store: Optional[FeatureStore] = None
_vector_rag: Optional[VectorRAGEngine] = None
_model_trainer: Optional[ModelTrainer] = None
_model_server: Optional[ModelServer] = None
_mlops: Optional[MLOpsManager] = None
_ai_orchestrator: Optional[MultiAgentOrchestrator] = None
_forecaster: Optional[ForecastingService] = None
_knowledge_graph: Optional[KnowledgeGraph] = None

_edge_model_mgr: Optional[EdgeModelManager] = None
_sync_engine: Optional[SyncEngine] = None
_federated_learning: Optional[FederatedLearningCoordinator] = None

_system_analyzer: Optional[SystemAnalyzer] = None


# ── Lifespan ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _stream_bus, _timeseries, _storage_tiers, _schema_registry
    global _normalizer, _geo_processor, _fleet_state, _data_quality, _replay_engine
    global _cep, _anomaly, _digital_twin, _predictive_maint, _behavior_inference
    global _route_eta, _fleet_optimizer, _signal_fusion
    global _feature_store, _vector_rag, _model_trainer, _model_server, _mlops
    global _ai_orchestrator, _forecaster, _knowledge_graph
    global _edge_model_mgr, _sync_engine, _federated_learning, _system_analyzer

    await db.init_pool()
    await db.setup_hypertables()
    await db.setup_schema_registry()
    await db.refresh_analytics_views()

    api_key = os.getenv("GEMINI_API_KEY", "")

    # Data ingestion engines
    _stream_bus = StreamBus(StreamConfig(backend=os.getenv("STREAM_BACKEND", "memory")))
    await _stream_bus.connect()
    _timeseries = TimeseriesEngine()
    _storage_tiers = StorageTierManager()
    _schema_registry = SchemaRegistry()
    _normalizer = TelemetryNormalizer()
    _geo_processor = GeoProcessor()
    _fleet_state = FleetStateEngine()
    _data_quality = DataQualityPipeline()
    _replay_engine = ReplayBackfillEngine()

    # Smart systems engines
    _cep = ComplexEventProcessor()
    _anomaly = AnomalyDetector()
    _digital_twin = DigitalTwinEngine()
    _predictive_maint = PredictiveMaintenanceEngine()
    _behavior_inference = BehaviorInferenceEngine()
    _route_eta = RouteETAEngine()
    _fleet_optimizer = FleetOptimizer()
    _signal_fusion = SignalFusionEngine()

    # AI/ML engines
    _feature_store = FeatureStore()
    _vector_rag = VectorRAGEngine()
    _vector_rag.seed_fleet_knowledge()
    _model_trainer = ModelTrainer()
    _model_server = ModelServer()
    _mlops = MLOpsManager()
    _ai_orchestrator = MultiAgentOrchestrator(api_key)
    _forecaster = ForecastingService()
    _knowledge_graph = KnowledgeGraph()

    # Edge-cloud engines
    _edge_model_mgr = EdgeModelManager()
    _sync_engine = SyncEngine()
    _federated_learning = FederatedLearningCoordinator()

    # System analyzer
    _system_analyzer = SystemAnalyzer(api_key)

    engine_count = 28
    print(f"[ScaleEngine] All {engine_count} engines initialized — {datetime.utcnow().isoformat()}")

    yield

    await _stream_bus.close()
    await db.close_pool()


# Needed here for logging in lifespan
from datetime import datetime

app = FastAPI(title="SGU Scale Engine — Fleet Intelligence Platform — Made by Monzer", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["POST", "GET"], allow_headers=["*"])


# ── Request models ────────────────────────────────────────────────────────

class LatLng(BaseModel):
    lat: float; lng: float

class TelemetryPayload(BaseModel):
    device_id: str
    lat: Optional[float] = None; lng: Optional[float] = None
    speed: Optional[float] = None; obd_speed: Optional[float] = None
    rpm: Optional[float] = None; fuel_level: Optional[float] = None
    coolant_temp: Optional[float] = None; engine_load: Optional[float] = None
    throttle: Optional[float] = None; mil: Optional[bool] = False

class RouteRequest(BaseModel):
    origin: LatLng; destination: LatLng
    device_id: Optional[str] = None; driver_id: Optional[str] = None

class DocumentIndex(BaseModel):
    collection: str; content: str; doc_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

class SearchQuery(BaseModel):
    query: str; collection: Optional[str] = None; top_k: int = 5

class DriverOrderMatch(BaseModel):
    order: Dict[str, Any]; driver_ids: Optional[List[str]] = None
    strategy: str = "balanced"

class ForecastRequest(BaseModel):
    driver_id: str; distance_km: float; route: Optional[Dict[str, Any]] = None

class AnalyzeRequest(BaseModel):
    include_fleet_context: bool = True
    include_quality_report: bool = True


# ── Helper ────────────────────────────────────────────────────────────────

def _engine_status_map() -> Dict[str, str]:
    """Build a health status map of all 28 engines."""
    engines = {
        "StreamBus": "healthy" if _stream_bus else "degraded",
        "TimeseriesEngine": "healthy" if _timeseries else "degraded",
        "StorageTierManager": "healthy" if _storage_tiers else "degraded",
        "SchemaRegistry": "healthy", "TelemetryNormalizer": "healthy",
        "GeoProcessor": "healthy" if db.available() else "degraded",
        "FleetStateEngine": "healthy", "DataQualityPipeline": "healthy",
        "ReplayBackfillEngine": "healthy",
        "ComplexEventProcessor": "healthy", "AnomalyDetector": "healthy",
        "DigitalTwinEngine": "healthy", "PredictiveMaintenanceEngine": "healthy",
        "BehaviorInferenceEngine": "healthy", "RouteETAEngine": "healthy",
        "FleetOptimizer": "healthy", "SignalFusionEngine": "healthy",
        "FeatureStore": "healthy", "VectorRAGEngine": "healthy",
        "ModelTrainer": "healthy", "ModelServer": "healthy",
        "MLOpsManager": "healthy", "MultiAgentOrchestrator": "healthy",
        "ForecastingService": "healthy", "KnowledgeGraph": "healthy",
        "EdgeModelManager": "healthy", "SyncEngine": "healthy",
        "FederatedLearningCoordinator": "healthy",
    }
    return engines


# ── Health ────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "scale-engine", "db_connected": db.available(),
            "engines": len(_engine_status_map()), "version": "1.0.0"}


# ══════════════════════════════════════════════════════════════════════════
# DATA INGESTION ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════

@app.post("/api/stream/publish")
async def stream_publish(payload: Dict[str, Any]):
    await _stream_bus.publish(payload)
    return {"published": True}

@app.get("/api/stream/stats")
async def stream_stats():
    return await _stream_bus.stats()

@app.get("/api/timeseries/device/{device_id}")
async def timeseries_device(device_id: str, hours: int = 24, interval: str = "5 minutes"):
    from datetime import timedelta
    end = datetime.utcnow(); start = end - timedelta(hours=hours)
    return {"data": await _timeseries.get_telemetry_timeseries(device_id, start, end, interval)}

@app.get("/api/timeseries/fleet")
async def timeseries_fleet(window: str = "1 hour"):
    return await _timeseries.get_fleet_aggregates(window)

@app.get("/api/storage/stats")
async def storage_stats():
    return await _storage_tiers.get_storage_stats()

@app.post("/api/schema/validate")
async def schema_validate(payload: Dict[str, Any]):
    is_valid, violations = _schema_registry.validate(payload)
    return {"valid": is_valid, "violations": violations}

@app.get("/api/schema/violations")
async def schema_violations(hours: int = 24):
    return await _schema_registry.get_violation_stats(hours)

@app.post("/api/normalize")
async def normalize(payload: Dict[str, Any], source: str = "mqtt"):
    return {"normalized": _normalizer.normalize(payload, source)}

@app.post("/api/geo/point-in-fence")
async def geo_point_in_fence(point: LatLng, geofence_id: str = None):
    return await _geo_processor.point_in_geofence(point.lat, point.lng, geofence_id)

@app.post("/api/geo/corridor")
async def geo_corridor(req: RouteRequest):
    return await _geo_processor.analyze_route_corridor(
        (req.origin.lat, req.origin.lng), (req.destination.lat, req.destination.lng))

@app.get("/api/fleet/state")
async def fleet_state_full():
    return _fleet_state.get_full_state()

@app.get("/api/fleet/state/device/{device_id}")
async def fleet_state_device(device_id: str):
    return _fleet_state.get_device_state(device_id)

@app.get("/api/quality/check")
async def quality_check():
    return await _data_quality.run_all_checks()

@app.get("/api/quality/device/{device_id}")
async def quality_device(device_id: str):
    return await _data_quality.score_device(device_id)

@app.get("/api/quality/fleet")
async def quality_fleet():
    return await _data_quality.get_fleet_quality()

@app.post("/api/replay/start")
async def replay_start(start: str, end: str, device_ids: str = None, dry_run: bool = False):
    cfg = ReplayConfig(
        start=datetime.fromisoformat(start), end=datetime.fromisoformat(end),
        device_ids=device_ids.split(",") if device_ids else None, dry_run=dry_run,
    )
    return await _replay_engine.replay(cfg)

@app.get("/api/replay/status")
async def replay_status():
    return _replay_engine.get_status()


# ══════════════════════════════════════════════════════════════════════════
# SMART SYSTEMS ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════

@app.post("/api/cep/ingest")
async def cep_ingest(payload: Dict[str, Any]):
    alerts = _cep.ingest(payload)
    return {"alerts_triggered": len(alerts), "alerts": alerts}

@app.get("/api/cep/alerts")
async def cep_alerts(limit: int = 50):
    return {"alerts": _cep.get_recent_alerts(limit)}

@app.post("/api/anomaly/detect")
async def anomaly_detect(payload: Dict[str, Any]):
    return {"anomalies": _anomaly.ingest(payload)}

@app.get("/api/anomaly/baseline/{device_id}")
async def anomaly_baseline(device_id: str, metric: str = "speed"):
    return _anomaly.get_baseline(device_id, metric)

@app.post("/api/twin/update")
async def twin_update(payload: Dict[str, Any]):
    _digital_twin.update(payload)
    return {"updated": True, "device_id": payload.get("device_id")}

@app.get("/api/twin/{device_id}")
async def twin_get(device_id: str):
    twin = _digital_twin.get_twin(device_id)
    if not twin: raise HTTPException(404, "Twin not found")
    return twin

@app.get("/api/twin/fleet")
async def twin_fleet():
    return _digital_twin.get_fleet_summary()

@app.post("/api/maintenance/predict")
async def maintenance_predict(payload: Dict[str, Any]):
    _predictive_maint.ingest(payload)
    return _predictive_maint.predict(payload.get("device_id", "unknown"))

@app.get("/api/maintenance/fleet")
async def maintenance_fleet():
    return _predictive_maint.get_fleet_health()

@app.post("/api/behavior/score")
async def behavior_score(payload: Dict[str, Any]):
    driver_id = payload.get("driver_id", "unknown")
    _behavior_inference.ingest_telemetry(driver_id, payload)
    return _behavior_inference.compute_longitudinal_score(driver_id)

@app.get("/api/behavior/compare/{driver_id}")
async def behavior_compare(driver_id: str):
    return _behavior_inference.compare_driver_to_fleet(driver_id)

@app.post("/api/eta/compute")
async def eta_compute(req: RouteRequest):
    eta = _route_eta.compute_eta(
        req.origin.lat, req.origin.lng, req.destination.lat, req.destination.lng)
    if req.driver_id:
        eta = _route_eta.compute_eta_with_history(
            req.driver_id, (req.origin.lat, req.origin.lng),
            (req.destination.lat, req.destination.lng))
    return eta

@app.post("/api/optimize/driver-match")
async def optimize_driver_match(req: DriverOrderMatch):
    from scale_engine import db as sdb
    profiles = []
    if req.driver_ids:
        for did in req.driver_ids:
            p = await sdb.get_driver_behavior_profile(did)
            if p: profiles.append(p)
    return {"rankings": _fleet_optimizer.recommend_driver_for_order(req.order, profiles, req.strategy)}

@app.post("/api/optimize/load-balance")
async def optimize_load_balance(orders: List[Dict], drivers: List[Dict]):
    return _fleet_optimizer.balance_load(orders, drivers)

@app.get("/api/optimize/kpis")
async def optimize_kpis():
    return _fleet_optimizer.compute_fleet_kpis([])

@app.post("/api/fusion/ingest")
async def fusion_ingest(source: str = "edge", signal_type: str = "telemetry",
                         payload: Dict[str, Any] = None, priority: int = 3):
    src = SignalSource(source) if source in [s.value for s in SignalSource] else SignalSource.EDGE
    pri = SignalPriority(priority)
    _signal_fusion.ingest(src, signal_type, payload or {}, pri)
    return {"ingested": True}

@app.get("/api/fusion/decisions")
async def fusion_decisions(limit: int = 50, min_confidence: float = 0.0):
    return {"decisions": _signal_fusion.get_fused_decisions(limit, min_confidence)}


# ══════════════════════════════════════════════════════════════════════════
# AI/ML ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════

@app.post("/api/features/compute")
async def features_compute(entity_type: str, entity: Dict[str, Any]):
    if entity_type == "driver": f = _feature_store.compute_driver_features(entity)
    elif entity_type == "vehicle": f = _feature_store.compute_vehicle_features(entity)
    elif entity_type == "route": f = _feature_store.compute_route_features(entity)
    else: raise HTTPException(400, f"Unknown entity type: {entity_type}")
    return {"entity_type": entity_type, "features": f}

@app.get("/api/features/{entity_type}/{entity_id}")
async def features_get(entity_type: str, entity_id: str):
    f = _feature_store.get_features(entity_type, entity_id)
    if not f: raise HTTPException(404)
    return {"entity_type": entity_type, "entity_id": entity_id, "features": f}

@app.post("/api/rag/index")
async def rag_index(doc: DocumentIndex):
    _vector_rag.index_document(doc.collection, doc.doc_id or f"doc_{_vector_rag._doc_count}", doc.content, doc.metadata)
    return {"indexed": True, "collection": doc.collection}

@app.post("/api/rag/search")
async def rag_search(req: SearchQuery):
    results = _vector_rag.search(req.collection, req.query, req.top_k) if req.collection else _vector_rag.search_all(req.query, req.top_k)
    return {"results": results, "context": _vector_rag.build_rag_context(req.query, [req.collection] if req.collection else None, req.top_k)}

@app.post("/api/models/train")
async def models_train(model_type: str, data: List[Dict[str, Any]] = None):
    if model_type == "driver_risk": m = _model_trainer.train_driver_risk_classifier(data or [])
    elif model_type == "fuel": m = _model_trainer.train_fuel_consumption_regressor(data or [])
    elif model_type == "maintenance": m = _model_trainer.train_maintenance_risk_scorer(data or [])
    else: raise HTTPException(400, f"Unknown model type: {model_type}")
    _model_server.register_model(m["name"], m); _mlops.register(m)
    return {"model": m}

@app.post("/api/models/predict")
async def models_predict(model_name: str, features: List[float]):
    return {"prediction": _model_server.predict(model_name, features)}

@app.get("/api/models/list")
async def models_list():
    return {"models": _model_server.stats()}

@app.get("/api/mlops/drift")
async def mlops_drift(model_name: str):
    return {"drift": _mlops.get_drift_alerts()}

@app.post("/api/mlops/rollback")
async def mlops_rollback(model_name: str):
    return _mlops.rollback(model_name)

@app.post("/api/orchestrator/run")
async def orchestrator_run(context: str = "", agents: List[str] = None):
    return await _ai_orchestrator.orchestrate(context, agents)

@app.post("/api/forecast/fuel")
async def forecast_fuel(req: ForecastRequest):
    return _forecaster.forecast_fuel_consumption(req.driver_id, req.distance_km)

@app.post("/api/forecast/delay")
async def forecast_delay(route: Dict[str, Any], driver: Dict[str, Any]):
    d = _forecaster.forecast_delivery_delay(route, driver)
    _forecaster.ingest("delays", d)
    return d

@app.post("/api/graph/add-node")
async def graph_add_node(node_type: str, node_id: str, attributes: Dict[str, Any] = None):
    _knowledge_graph.add_node(node_type, node_id, attributes)
    return {"added": True}

@app.get("/api/graph/related")
async def graph_related(node_type: str, node_id: str, depth: int = 1):
    return {"related": _knowledge_graph.query_related(node_type, node_id, depth=depth)}


# ══════════════════════════════════════════════════════════════════════════
# EDGE-CLOUD ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════

@app.post("/api/edge/models/create")
async def edge_model_create(model_type: str, version: str, params: Dict[str, Any]):
    if model_type == "behavior":
        m = _edge_model_mgr.create_behavior_model(version, params.get("thresholds", {}))
    elif model_type == "sensor":
        m = _edge_model_mgr.create_sensor_threshold_model(version, params)
    else:
        raise HTTPException(400, f"Unknown model type: {model_type}")
    return {"model_id": m.model_id, "version": m.version, "checksum": m.checksum}

@app.post("/api/edge/models/rollout")
async def edge_model_rollout(model_name: str, version: str, devices: List[str], pct: int = 10):
    return _edge_model_mgr.stage_rollout(model_name, version, devices, pct)

@app.post("/api/edge/models/confirm")
async def edge_model_confirm(rollout_id: str, device_id: str, checksum: str, success: bool = True):
    return _edge_model_mgr.confirm_device_update(rollout_id, device_id, checksum, success)

@app.post("/api/sync/push-to-edge")
async def sync_push_to_edge(device_id: str, since: str = None):
    s = datetime.fromisoformat(since) if since else None
    return await _sync_engine.push_to_edge(device_id, s)

@app.post("/api/sync/ingest-from-edge")
async def sync_ingest_from_edge(device_id: str, telemetry: List[Dict], events: List[Dict] = None):
    return await _sync_engine.ingest_from_edge(device_id, telemetry, events)

@app.post("/api/fl/start-round")
async def fl_start_round():
    return {"round": _federated_learning.start_round()}

@app.post("/api/fl/submit-update")
async def fl_submit_update(device_id: str, round_num: int, deltas: Dict[str, float], samples: int = 100):
    return _federated_learning.submit_local_update(device_id, round_num, deltas, samples)

@app.post("/api/fl/aggregate")
async def fl_aggregate(round_num: int, min_devices: int = 2):
    return _federated_learning.aggregate(round_num, min_devices)


# ══════════════════════════════════════════════════════════════════════════
# SYSTEM ANALYZER
# ══════════════════════════════════════════════════════════════════════════

@app.post("/api/system/analyze")
async def system_analyze(req: AnalyzeRequest = AnalyzeRequest()):
    fleet_ctx = None; quality = None
    if req.include_fleet_context and db.available():
        fleet_ctx = await db.get_fleet_behavior_summary()
    if req.include_quality_report and _data_quality:
        quality = await _data_quality.run_all_checks()
    result = await _system_analyzer.analyze(_engine_status_map(), fleet_ctx, quality)
    result["engine_count"] = len(_engine_status_map())
    return result


# ── Entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8002))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
