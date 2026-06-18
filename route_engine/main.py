"""
Route Engine — FastAPI application.
Backend microservice for AI-driven route optimization, behaviour-aware
driver-route pairing, and comprehensive fleet reporting.

Endpoints:
  POST /api/route/analyze       — Score multiple route candidates for a driver
  POST /api/route/compare       — Compare routes & pick best without driver context
  POST /api/route/driver-match  — Find best driver(s) for a fixed route
  POST /api/route/report        — Run full 4-stage report pipeline
  GET  /api/route/driver/{id}   — Get behaviour profile for one driver
  GET  /api/route/drivers       — List all drivers with behaviour summaries
  GET  /health                  — Service health check
"""

import os
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any, List

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import db
from route_analyzer import RouteAnalyzer
from report_pipeline import ReportPipeline

load_dotenv()

_analyzer: Optional[RouteAnalyzer] = None
_pipeline: Optional[ReportPipeline] = None


# ── Lifespan ────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _analyzer, _pipeline

    await db.init_pool()

    api_key = os.getenv("GEMINI_API_KEY")
    if api_key:
        _analyzer = RouteAnalyzer(api_key)
        _pipeline = ReportPipeline(api_key)
        print("[RouteEngine] AI analyzers ready (Gemini)")
    else:
        print("[RouteEngine] WARNING: GEMINI_API_KEY not set — AI scoring disabled")

    yield

    await db.close_pool()


app = FastAPI(title="SGU Route Optimization Engine", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)


# ── Request / Response models ───────────────────────────────────────────────

class LatLng(BaseModel):
    lat: float
    lng: float


class RouteAnalysisRequest(BaseModel):
    driver_id: Optional[str] = None
    origin: LatLng
    destination: LatLng
    include_fleet_context: bool = True


class RouteCompareRequest(BaseModel):
    origin: LatLng
    destination: LatLng
    driver_ids: Optional[List[str]] = None


class DriverMatchRequest(BaseModel):
    origin: LatLng
    destination: LatLng
    driver_ids: Optional[List[str]] = None  # if None, uses all active drivers


class ReportRequest(BaseModel):
    origin: LatLng
    destination: LatLng
    driver_ids: Optional[List[str]] = None


# ── Helpers ─────────────────────────────────────────────────────────────────

def _require_analyzer():
    if _analyzer is None:
        raise HTTPException(status_code=503, detail="GEMINI_API_KEY not configured")
    return _analyzer


def _require_pipeline():
    if _pipeline is None:
        raise HTTPException(status_code=503, detail="GEMINI_API_KEY not configured")
    return _pipeline


async def _build_driver_profiles(driver_ids: Optional[List[str]]) -> List[Dict[str, Any]]:
    """Fetch behaviour profiles for requested drivers, or all active drivers."""
    if driver_ids:
        profiles = []
        for did in driver_ids:
            p = await db.get_driver_behavior_profile(did)
            if p:
                profiles.append(p)
        return profiles

    # Default: all drivers with any behaviour data
    # We query the drivers table directly via the pool
    if not db._pool_available():
        return []

    async with db._pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id FROM drivers WHERE status = 'active' ORDER BY name"
        )

    profiles = []
    for r in rows:
        p = await db.get_driver_behavior_profile(r["id"])
        if p:
            profiles.append(p)
    return profiles


# ── Endpoints ───────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "route-engine",
        "gemini_configured": _analyzer is not None,
        "db_connected": db._pool is not None,
    }


@app.post("/api/route/analyze")
async def analyze_route(req: RouteAnalysisRequest):
    """
    Analyze route candidates between origin and destination for a specific driver.
    Returns AI-ranked routes with full scoring breakdown.
    """
    analyzer = _require_analyzer()

    # Fetch driver profile if specified
    driver_profile = None
    if req.driver_id:
        driver_profile = await db.get_driver_behavior_profile(req.driver_id)
        if not driver_profile:
            raise HTTPException(status_code=404, detail=f"Driver {req.driver_id} not found")

    # Fetch fleet context for benchmarking
    fleet_context = None
    if req.include_fleet_context:
        fleet_context = await db.get_fleet_behavior_summary()

    # Generate + score route candidates
    origin = (req.origin.lat, req.origin.lng)
    destination = (req.destination.lat, req.destination.lng)

    candidates = analyzer.generate_candidates(origin, destination, driver_profile)
    result = await analyzer.score_routes(candidates, driver_profile, fleet_context)

    return result


@app.post("/api/route/compare")
async def compare_routes(req: RouteCompareRequest):
    """
    Compare route candidates without driver context (pure route comparison).
    Optionally rank drivers for each route.
    """
    analyzer = _require_analyzer()

    origin = (req.origin.lat, req.origin.lng)
    destination = (req.destination.lat, req.destination.lng)

    # Generate candidates (no driver → no behaviour pre-scoring)
    candidates = analyzer.generate_candidates(origin, destination, driver_profile=None)
    result = await analyzer.score_routes(candidates)

    # If driver IDs provided, rank them for the winning route
    driver_rankings = None
    if req.driver_ids and result.get("winning_route"):
        profiles = await _build_driver_profiles(req.driver_ids)
        if profiles:
            driver_rankings = analyzer.rank_drivers_for_route(
                profiles, result["winning_route"]
            )

    result["driver_rankings"] = driver_rankings
    return result


@app.post("/api/route/driver-match")
async def driver_match(req: DriverMatchRequest):
    """
    Given a fixed route, find the best driver(s) for it.
    Returns all drivers ranked by suitability.
    """
    analyzer = _require_analyzer()

    profiles = await _build_driver_profiles(req.driver_ids)
    if not profiles:
        raise HTTPException(status_code=404, detail="No driver profiles found")

    origin = (req.origin.lat, req.origin.lng)
    destination = (req.destination.lat, req.destination.lng)

    # Pick the best route candidate as the target route
    candidates = analyzer.generate_candidates(origin, destination, driver_profile=None)
    scored = await analyzer.score_routes(candidates)
    target_route = scored.get("winning_route", candidates[0] if candidates else None)

    if not target_route:
        raise HTTPException(status_code=500, detail="Failed to determine target route")

    rankings = analyzer.rank_drivers_for_route(profiles, target_route)

    return {
        "target_route": {
            "route_id": target_route.get("route_id"),
            "route_name": target_route.get("route_name"),
            "distance_km": target_route.get("distance_km"),
            "estimated_duration_min": target_route.get("estimated_duration_min"),
            "hazard_profile": target_route.get("hazard_profile"),
        },
        "driver_rankings": rankings,
        "best_driver": rankings[0] if rankings else None,
        "total_drivers_evaluated": len(rankings),
    }


@app.post("/api/route/report")
async def generate_report(req: ReportRequest):
    """
    Run the full 4-stage report pipeline:
    Collect → Analyze → Synthesize → Finalize

    Returns a comprehensive route optimization report with:
    - All driver-route pairings scored
    - Top recommendations
    - High-risk alerts
    - Optimization suggestions
    """
    pipeline = _require_pipeline()
    analyzer = _require_analyzer()

    # Fetch driver profiles
    profiles = await _build_driver_profiles(req.driver_ids)
    if not profiles:
        raise HTTPException(status_code=404, detail="No driver profiles found")

    # Fetch fleet context
    fleet_context = await db.get_fleet_behavior_summary()

    # Generate route candidates
    origin = (req.origin.lat, req.origin.lng)
    destination = (req.destination.lat, req.destination.lng)
    candidates = analyzer.generate_candidates(origin, destination, driver_profile=None)

    # Build route_candidates list for the pipeline
    route_candidates = [
        (c["route_key"], c) for c in candidates
    ]

    # Run the full pipeline
    result = await pipeline.run_full_pipeline(profiles, route_candidates, fleet_context)

    return result


@app.get("/api/route/driver/{driver_id}")
async def get_driver_profile(driver_id: str):
    """
    Get a single driver's behaviour profile with all event counts,
    trip stats, and raw event history.
    """
    profile = await db.get_driver_behavior_profile(driver_id)
    if not profile:
        raise HTTPException(status_code=404, detail=f"Driver {driver_id} not found")

    return {"driver": profile}


@app.get("/api/route/drivers")
async def list_drivers():
    """
    List all drivers with behaviour summaries (lightweight).
    """
    if not db._pool_available():
        return {"drivers": [], "note": "Database not connected"}

    async with db._pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, name, safety_score, status, vehicle_plate,
                      total_distance_km, total_trips, fuel_efficiency,
                      behavior_events_json
               FROM drivers
               LEFT JOIN (
                 SELECT driver_id,
                        SUM(total_distance_km) AS total_distance_km,
                        COUNT(*) AS total_trips,
                        AVG(fuel_efficiency) AS fuel_efficiency
                 FROM trips WHERE status = 'completed'
                 GROUP BY driver_id
               ) t ON drivers.id = t.driver_id
               ORDER BY safety_score DESC NULLS LAST"""
        )

    drivers = []
    for r in rows:
        d = dict(r)
        # Convert Decimal to float for JSON
        for k in ("total_distance_km", "fuel_efficiency"):
            if d.get(k) is not None:
                d[k] = round(float(d[k]), 2)
        d["total_trips"] = d.get("total_trips") or 0
        drivers.append(d)

    return {"drivers": drivers, "count": len(drivers)}


# ── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8001))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
