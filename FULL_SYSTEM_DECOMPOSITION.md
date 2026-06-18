# Full System Decomposition — SGU Logistics & Telemetry Platform

## 1. System Overview

This platform is a full end-to-end fleet monitoring and intelligence system. It combines:
- **Hardware data collection** from GPS and OBD-II devices
- **Real-time data ingestion** via MQTT
- **Persistent storage** in PostgreSQL
- **Browser-side local caching** using SQLite/sql.js
- **Cloud-side analytics and AI** via multiple Python services
- **Web dashboard UI** for operators
- **External notification and report delivery** via Telegram and PDF generation

The system is divided into these major layers:

1. **Hardware / Edge**
2. **Backend Core**
3. **Integration Connector**
4. **AI / Intelligence Services**
5. **Frontend / Dashboard**
6. **Data & Storage**


## 2. Hardware / Edge Layer

### 2.1 GPS and OBD-II Device Code

Location: `Hardware/` directory

- `GPS_device.ino`
  - Reads GPS data from a tracker module.
  - Publishes telemetry to MQTT topic like `monztrack/device01/gps`.
  - Captures latitude, longitude, speed, fix status, satellites, and timestamps.

- `obd2_scanner.ino`
  - Reads vehicle diagnostics via OBD-II.
  - Captures RPM, throttle percentage, coolant temperature, engine load, fuel-related signals.
  - Sends telemetry messages for behavior event detection and analytics.

### 2.2 Vehicle Behavior Detection Logic

Location: `BehaviorAnalysis.h`

This module is responsible for **real-time detection of unsafe driving behaviors** on the edge device.

Key behavior rules:
- **Harsh Braking**: speed drop > 15 km/h in 3 seconds
- **Aggressive Launch**: throttle > 90% while speed < 30 km/h
- **Cold Engine Abuse**: RPM > 3000 with coolant temperature < 70 °C
- **Engine Lugging**: engine load > 85% with RPM < 1500
- **Excessive Idling**: speed == 0 and RPM > 500 for > 180 seconds
- **Speeding**: speed > 110 km/h

Each detected event becomes a `BehaviorEventLog` entry with:
- event type
- timestamp
- severity
- values

Purpose: keep critical safety logic local so the system can flag urgent issues even if connectivity drops.


## 3. Backend Core

### 3.1 Node.js Express Server

Location: `root/server.js`

This is the main backend service for the web dashboard and real-time ingestion.

Responsibilities:
- Start Express server on port `3000`
- Serve static files and dashboard UI
- Provide REST endpoints for telemetry, devices, events, and health
- Connect to PostgreSQL via `pg`
- Subscribe to MQTT broker and ingest live telemetry
- Broadcast live telemetry to browser clients via Server-Sent Events (SSE)
- Proxy AI, route, and scale engine requests via `integration/connector.js`

Key behaviors:
- `connectToHiveMQ()` subscribes to MQTT topics and handles incoming messages
- `saveTelemetry(payload, topic)` inserts telemetry rows into PostgreSQL
- `broadcastSSE()` pushes live telemetry updates to connected dashboard clients
- `POST /api/ai/analyze` proxies AI requests to Python AI backend if configured
- `GET /health` returns health for database, MQTT, and upstream services

Notes:
- `POOL` is created only if `DATABASE_URL` is provided.
- The service uses a `connector` to gracefully integrate with Python-based engines.

### 3.2 Integration Connector

Location: `integration/connector.js`

Purpose:
- Proxy API requests from the Node.js web server to the Python-based intelligence services.
- Forward MQTT telemetry to the Scale Engine without blocking the main MQTT pipeline.
- Keep the user-facing backend working even if downstream services are unavailable.

Key functions:
- `upstreamFetch(baseUrl, path, options)` - call upstream service with timeout and graceful error handling
- `proxyToRouteEngine(req, res, path)` - forward route optimization requests
- `proxyToScaleEngine(req, res, path)` - forward intelligence requests
- `proxyGetToRouteEngine()` and `proxyGetToScaleEngine()` - support GET proxying with query params
- `forwardTelemetryToScaleEngine(payload, topic)` - fire-and-forget telemetry forwarding

This connector is the bridge that allows the older Node.js dashboard to connect to newer Python AI services.


## 4. AI / Intelligence Services

The intelligent services are implemented in Python, separated into three main bundles:

- `ai_backend/` — Gemini AI and Telegram integration
- `route_engine/` — route optimization and report generation
- `scale_engine/` — core analytics and smart systems

### 4.1 AI Backend

Location: `ai_backend/`

Primary purpose: provide natural-language AI analysis and messaging.

Files:
- `main.py` — FastAPI app for AI analysis API
- `analyzer.py` — `FleetAnalyzer` wraps Gemini API and builds AI context
- `db.py` — PostgreSQL helper for fleet snapshots and report data
- `telegram_bot.py` — Telegram bot that responds to commands and natural language
- `report_generator.py` — builds PDF fleet reports

How it works:
- `main.py` starts FastAPI with lifecycle event handlers.
- It loads `GEMINI_API_KEY` and initializes `FleetAnalyzer`.
- It loads Telegram bot if `TELEGRAM_BOT_TOKEN` is configured.
- `POST /api/ai/analyze` returns Gemini analysis of a user question plus fleet context.

`FleetAnalyzer` details:
- Builds a compact context string from fleet stats, top/bottom drivers, orders, devices, and alerts.
- Sends that context and the user question to Gemini.
- The AI prompt is designed for short, actionable answers without extra filler.

Telegram integration:
- Users can send `/report`, `/score`, `/events`, `/metrics`, or ask text questions.
- Based on message content, it may generate a PDF or reply with AI insights.
- The bot uses snapshot data from `db.get_fleet_snapshot()` before calling `analyze()`.

Report generation:
- `report_generator.py` uses Jinja2 and WeasyPrint to convert fleet data into a well-styled PDF.
- The bot sends the generated PDF back to the user.

### 4.2 Route Engine

Location: `route_engine/`

Purpose: perform route analysis, driver-route matching, and report pipeline execution.

Service: `route_engine/main.py`
- FastAPI app exposing endpoints:
  - `/health`
  - `/api/route/analyze`
  - `/api/route/compare`
  - `/api/route/driver-match`
  - `/api/route/report`
  - `/api/route/driver/{id}`
  - `/api/route/drivers`

Components:
- `route_analyzer.py` — core route candidate generation and AI scoring
- `report_pipeline.py` — multi-stage route report generation
- `behavior_integrator.py` — shared driver-route suitability logic
- `db.py` — database access for driver profiles, trips, and telemetry

#### Route Analyzer

How route candidates are built:
- Generates 3 synthetic candidate routes: Direct, Arterial, Urban
- Computes distance, duration, fuel estimate, hazards, and segments
- If driver profile exists, computes driver suitability metrics

AI scoring:
- Builds a detailed scoring context with all candidate attributes
- Sends prompt to Gemini with route scoring schema
- Falls back to deterministic scoring if Gemini is unavailable

Strengths:
- Combines algorithmic metrics and AI reasoning
- Produces ranking, scores, strengths, weaknesses, and recommendation

#### Report Pipeline

A 4-stage pipeline:
1. **Collect** — normalize driver and route data
2. **Analyze** — compute pairing-level suitability and risk
3. **Synthesize** — ask Gemini to build a narrative JSON report
4. **Output** — return structured report ready for UI or download

The generated output includes:
- executive summary
- fleet overview
- top recommendations
- risk alerts
- optimization suggestions
- metadata

#### Route Engine DB Access

`route_engine/db.py` reads from PostgreSQL driver and telemetry tables. It provides:
- `get_driver_behavior_profile(driver_id)`
- `get_telemetry_segments(device_id, hours)`
- `get_route_events(driver_id, route_lat, route_lng)`
- `get_historical_trips(...)`

Its driver profile returns enriched fields like:
- total events per warning type
- average speed
- fuel efficiency
- raw event history

### 4.3 Scale Engine

Location: `scale_engine/`

Purpose: the main intelligence platform of the project. It contains multiple engines across four groups.

Key structure:
- `scale_engine/__init__.py` — package entry point
- `scale_engine/main.py` — FastAPI app and lifecycle management
- `scale_engine/db.py` — shared async database pool
- `scale_engine/data_ingestion/` — ingestion engines
- `scale_engine/smart_systems/` — analytics engines
- `scale_engine/ai_ml/` — AI/ML support modules
- `scale_engine/edge_cloud/` — edge-cloud sync helpers

#### Engine groups

1. **Data Ingestion**
   - `stream_bus.py` — distributed stream ingestion using Redis/NATS/memory queue
   - `timeseries_engine.py` — time-series queries and aggregates
   - `storage_tiers.py` — hot/warm/cold storage logic
   - `schema_registry.py` — payload validation
   - `normalizer.py` — standardize raw telemetry units
   - `geo_processor.py` — geofencing and route corridor analysis
   - `fleet_state.py` — projection of current fleet state
   - `data_quality.py` — sensor/data health checks
   - `replay_backfill.py` — historical replay and backfill processing

2. **Smart Systems**
   - `behavior_inference.py` — long-term driver scoring, trends, coaching recommendations
   - `anomaly_detector.py` — detect unusual fleet behavior
   - `cep_engine.py` — complex event processing and rule-based alerts
   - `digital_twin.py` — maintain virtual vehicle state
   - `predictive_maintenance.py` — failure prediction from trends
   - `fleet_optimizer.py` — scheduling and optimization logic
   - `route_eta.py` — ETA prediction
   - `signal_fusion.py` — fuse multiple sensor streams for better accuracy

3. **AI / ML**
   - `ai_ml/feature_store.py`
   - `ai_ml/model_trainer.py`
   - `ai_ml/model_server.py`
   - `ai_ml/vector_rag.py`
   - `ai_ml/forecaster.py`
   - `ai_ml/knowledge_graph.py`
   - `ai_ml/mlops.py`
   - `ai_ml/ai_orchestrator.py`

4. **Edge / Cloud Bridge**
   - `edge_cloud/edge_model_mgr.py`
   - `edge_cloud/federated_learning.py`
   - `edge_cloud/sync_engine.py`

#### Scale Engine details

- `stream_bus.py` supports Redis Streams, NATS, or in-memory queue.
- Telemetry is published into the stream bus and also broadcast to subscribers.
- `TimeseriesEngine` constructs continuous aggregates for fleet metrics.
- `BehaviorInferenceEngine` computes longitudinal trend scores and percentiles using the last 500 events per driver.
- `PredictiveMaintenanceEngine` predicts failures using cooling trends, RPM stability, voltage, and MIL status.
- `SystemAnalyzer` can introspect engine health and produce architecture-level recommendations.


## 5. Frontend / Dashboard

### 5.1 Main Dashboard

Location: `index.html`

This is the web UI for fleet operators. It includes:
- Live map visualization using Leaflet
- Telemetry and event panels
- Driver rankings and behavior analytics
- Order tracking and route assignment
- AI chat interface
- PDF report generation support

It also uses external libraries for charts, mapping, and PDF creation:
- Leaflet
- Chart.js
- jsPDF
- Turf.js
- Leaflet-Geoman

### 5.2 Local Database and AI Caching

Location: `database/` directory

Purpose: enable fast, offline-capable analytics and reduce external AI calls.

Components:
- `db.js` — initializes SQLite via sql.js in browser
- `dal.js` — data access layer for CRUD operations
- `service.js` — high-level database service API
- `ai-engine.js` — smart AI engine that answers questions from local DB
- `loader.js` — loads the local database modules in correct order
- `schema.sql` — schema definition for browser database

Key behavior:
- Local SQLite database is persisted to IndexedDB.
- `database/ai-engine.js` answers many fleet questions directly from SQL queries.
- If the question cannot be answered locally, it delegates to Gemini.
- This strategy saves AI cost by answering ~85% of queries locally.

### 5.3 Login UI

Location: `login.html` and `login.js` under `root/`

The UI protects dashboard access and stores auth info in browser storage.

### 5.4 Real-Time Updates

The frontend connects to the backend SSE stream at `/api/stream`.
Incoming telemetry is displayed live on the map and alert panels.


## 6. Data and Storage Architecture

### 6.1 PostgreSQL Schema

Location: `root/database/pg-schema.sql`

The main backend database stores:
- `devices`
- `telemetry`
- `events`
- `drivers`
- `orders`
- `customers`
- `trips`
- `driver_behavior_history`

The schema supports event history, driver-route associations, fleet state, and order management.

### 6.2 Browser-side SQLite Schema

Location: `database/db.js` and `database/schema.sql`

This browser database mirrors key fleet entities for local querying:
- `drivers`
- `devices`
- `orders`
- `telemetry`
- `events`
- `customers`

It is designed for offline analytics and AI prompt caching.

### 6.3 Telemetry Flow

1. Vehicle edge publishes MQTT message
2. Node.js backend receives it
3. `saveTelemetry()` stores into PostgreSQL
4. Broadcasts live updates via SSE to frontend
5. Forwards payload to Scale Engine via `integration/connector.js`
6. Scale Engine ingests telemetry into Stream Bus and smart engines


## 7. AI Architecture

### 7.1 Local AI vs Cloud AI

**Local AI**
- Implemented in `database/ai-engine.js`
- Answers straightforward fleet questions using SQL queries
- Fast and free
- Example questions: driver count, top drivers, active orders, current alerts

**Cloud AI**
- Implemented in `ai_backend/` and route_engine/ via Gemini
- Used for narrative analysis, recommendations, rankings, and reports
- Example questions: "Why is driver score low?", "Which route is best?"

### 7.2 Gemini Prompting Strategy

The system uses carefully designed prompts to enforce structured output, avoid filler, and constrain responses.

Examples:
- `FleetAnalyzer` prompt focuses on concise action recommendations and data-driven output.
- `RouteAnalyzer` prompt requires valid JSON with route rankings, scores, strengths, and weaknesses.
- `ReportPipeline` prompt requires a structured JSON report with executive summary and recommendations.
- `SystemAnalyzer` prompt asks for architecture-level health and scaling advice.

### 7.3 AI Failover Strategy

If Gemini fails or returns invalid output:
- `route_analyzer.py` falls back to deterministic scoring
- `report_pipeline.py` can produce a fallback report
- The system logs errors and continues operating


## 8. External Integrations

### 8.1 MQTT

- Message broker configured through `MQTT_BROKER_URL`
- Default example: `mqtt://broker.hivemq.com:1883`
- Main telemetry topic: `monztrack/device01/gps`
- The backend subscribes and ingests data in real-time

### 8.2 Telegram

- `ai_backend/telegram_bot.py` uses `python-telegram-bot`
- Supports commands and free text
- Sends PDF reports and AI insights to supervisors

### 8.3 Gemini

- Used by `ai_backend`, `route_engine`, and `scale_engine` components
- Requires `GEMINI_API_KEY`
- The system uses Gemini for complex analysis, route ranking, report synthesis, and system introspection

### 8.4 Redis / NATS / Memory Stream Bus

- `scale_engine/data_ingestion/stream_bus.py` supports multiple backends
- Default fallback is in-memory queue for development
- Redis Streams is the intended high-volume ingestion backend


## 9. Deployment and Environment

### 9.1 Environment Variables

Important variables:
- `DATABASE_URL` — PostgreSQL connection string
- `MQTT_BROKER_URL` — MQTT broker address
- `MQTT_TOPIC` — MQTT topic subscription
- `PYTHON_AI_URL` — AI backend URL for proxy
- `ROUTE_ENGINE_URL` — Route engine URL for proxy
- `SCALE_ENGINE_URL` — Scale engine URL for proxy
- `GEMINI_API_KEY` — API key for Gemini AI
- `TELEGRAM_BOT_TOKEN` — Telegram bot token
- `TELEGRAM_CHAT_ID` — optional chat ID

### 9.2 Service Boundaries

- `root/server.js` — dashboard backend + MQTT ingestion
- `ai_backend/main.py` — Gemini analysis + Telegram bot
- `route_engine/main.py` — route optimization microservice
- `scale_engine/main.py` — core analytics / intelligence microservice

Each service uses FastAPI or Express and can be deployed as an independent container.

### 9.3 Deployment Files

Each Python service includes `requirements.txt`, `nixpacks.toml`, and `railway.json` for predictable deployment.


## 10. Key Concepts by Component

### 10.1 Node.js Backend

Responsibilities:
- Accept telemetry
- Persist to PostgreSQL
- Serve frontend
- Health checks
- Proxy advanced intelligence requests
- Keep live SSE stream

Important design pattern: graceful degradation — fallback when advanced AI services are offline.

### 10.2 Route Engine

Responsibilities:
- Generate route candidates
- Score routes with AI and deterministic logic
- Match drivers to routes
- Produce narrative route reports

Important design pattern: AI + deterministic hybrid scoring.

### 10.3 Scale Engine

Responsibilities:
- Ingest telemetry at volume
- Normalize data
- Track fleet state and data quality
- Detect anomalies and manage digital twins
- Predict maintenance risk
- Provide AI-ready summaries

Important design pattern: modular engine groups and stream-based ingestion.

### 10.4 AI Backend

Responsibilities:
- Answer natural language fleet questions
- Generate fleet reports
- Send alerts/reports through Telegram

Important design pattern: local context building for Gemini and cost-aware prompting.

### 10.5 Frontend

Responsibilities:
- Present live fleet telemetry
- Display alerts and driver analytics
- Provide an AI chat interface
- Offer offline/local aggregate querying

Important design pattern: local browser storage and database-first AI queries.


## 11. System Flow Example: Unsafe Driving Event

1. Arduino detects a behavior event in `BehaviorAnalysis.h`.
2. The edge device sends telemetry via MQTT.
3. `root/server.js` receives the message.
4. It saves telemetry to PostgreSQL.
5. It broadcasts the event through SSE to the browser.
6. It forwards telemetry to `scale_engine` via the connector.
7. `scale_engine` ingests it into the StreamBus and smart engines.
8. `behavior_inference.py` updates driver trends.
9. `cep_engine.py` may raise a critical alert.
10. `ai_backend` can report the current fleet status and send Telegram alerts.


## 12. Extension Points

The architecture is intentionally modular and extensible:
- Add new edge behavior checks in `BehaviorAnalysis.h`
- Add new route scoring dimensions in `route_analyzer.py`
- Add new smart systems engines in `scale_engine/smart_systems/`
- Add new AI prompts in `ai_backend/` and `scale_engine/system_analyzer.py`
- Add new browser queries in `database/ai-engine.js`


## 13. Recommended Presentation Focus

When explaining the system, emphasize:
- **End-to-end scope**: from sensors to reports
- **Real-time safety alerts**: hardware edge + backend streaming
- **Modular intelligence**: separate engines for ingestion, analytics, AI
- **Cost-aware AI**: use local DB first, Gemini only when needed
- **Resilience**: fallbacks and graceful degradation
- **Actionability**: route and driver recommendations, Telegram alerts, PDF reports


## 14. Important Files and What They Mean

- `root/server.js` — main web backend and MQTT ingestion
- `integration/connector.js` — API bridge to Python services
- `hardware/GPS_device.ino` — GPS data source
- `hardware/obd2_scanner.ino` — vehicle OBD-II source
- `BehaviorAnalysis.h` — edge behavior detection rules
- `database/db.js` — browser SQLite initialization
- `database/ai-engine.js` — local AI question answering
- `ai_backend/main.py` — Gemini AI service entrypoint
- `ai_backend/telegram_bot.py` — notification bot
- `route_engine/main.py` — route optimization service
- `route_engine/route_analyzer.py` — AI route ranking logic
- `route_engine/report_pipeline.py` — report generation pipeline
- `scale_engine/main.py` — core intelligence service entrypoint
- `scale_engine/data_ingestion/stream_bus.py` — high-volume telemetry bus
- `scale_engine/smart_systems/behavior_inference.py` — driver scoring
- `scale_engine/smart_systems/predictive_maintenance.py` — failure prediction


## 15. Final Notes

This system is not a single monolithic application.
It is a **distributed fleet intelligence platform** built from:
- edge detection and IoT messaging
- backend persistence and realtime streaming
- modular Python analytics services
- natural language AI integration
- browser-side local analytics and dashboard UI

The architecture is strong because it keeps each layer focused and allows advances in one layer without breaking the others.

---

Generated by the project analysis toolchain for full system decomposition.
