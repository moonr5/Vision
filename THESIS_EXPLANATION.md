# SGU Logistics & Telemetry System - Thesis Overview

## 🎯 Executive Summary

Your thesis project is a **comprehensive fleet management and intelligent telemetry system** designed to monitor, analyze, and optimize vehicle behavior and logistics operations in real-time. It combines IoT data collection, AI/ML analytics, and a modern web dashboard into a unified platform.

**Core Problem Solved:** Traditional fleet management lacks real-time behavior insights and predictive capabilities. Your system fills this gap by ingesting raw sensor data from vehicles, processing it through multiple intelligent engines, and providing actionable insights to logistics operators.

---

## 📊 System Architecture (High-Level)

```
┌─────────────────────────────────────────────────────────────────┐
│                     IoT LAYER (Hardware)                        │
│  GPS Devices (*.ino)  │  OBD-II Scanners  │  Sensors (Temp, etc)│
└──────────────────────┬──────────────────────────────────────────┘
                       │ MQTT/Serial
┌──────────────────────▼──────────────────────────────────────────┐
│              DATA INGESTION LAYER (Backend)                     │
│  Node.js Express Server (server.js)  +  Python Scale Engine    │
│  ├─ MQTT Broker Connection                                     │
│  ├─ Real-time Stream Processing                                │
│  └─ PostgreSQL Database                                        │
└──────────────────────┬──────────────────────────────────────────┘
                       │ REST API
┌──────────────────────▼──────────────────────────────────────────┐
│           AI/ML & ANALYTICS LAYER (Intelligence)               │
│  ├─ Scale Engine (28 modular engines)                          │
│  │  ├─ Data Ingestion (9 engines)                              │
│  │  └─ Smart Systems (8+ engines)                              │
│  ├─ Route Engine (Route optimization)                          │
│  └─ AI Backend (Python + Gemini integration)                   │
└──────────────────────┬──────────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────────┐
│                  PRESENTATION LAYER                            │
│  Web Dashboard (HTML/CSS/JS) + Local SQLite Database          │
│  ├─ Real-time Charts & Alerts                                 │
│  ├─ Driver Behavior Scoring                                   │
│  ├─ AI Chat Interface (Gemini + Local Data)                  │
│  └─ Mobile-Responsive UI                                      │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🔧 Core Components

### 1. **IoT Sensor Layer** (`*.ino` files)

**Purpose:** Collect raw telemetry from vehicles

- **GPS Device** (`GPS_device.ino`)
  - Reads GPS coordinates and speed
  - Publishes to MQTT topic: `monztrack/device01/gps`
  - Data includes: `latitude`, `longitude`, `speed`, `timestamp`

- **OBD-II Scanner** (`obd2_scanner.ino`)
  - Connects to vehicle's OBD-II port (diagnostic protocol)
  - Extracts engine metrics:
    - RPM (engine speed)
    - Throttle position (%)
    - Coolant temperature
    - Engine load (%)
  - Enables behavior detection (harsh braking, aggressive acceleration, etc.)

**Key File:** [BehaviorAnalysis.h](BehaviorAnalysis.h)
- Detects 7 critical driving behaviors:
  1. **Harsh Braking** - Sudden speed drop > 15 km/h in 3 seconds
  2. **Aggressive Launch** - Throttle > 90% at low speed
  3. **Cold Engine Abuse** - High RPM with low coolant temp
  4. **Engine Lugging** - High load at low RPM
  5. **Excessive Idling** - Engine running at 0 speed > 180 seconds
  6. **Speeding** - Speed > 110 km/h
  7. **Unknown events** - Custom violations

---

### 2. **Data Ingestion Layer** (Node.js Backend)

**Entry Point:** [server.js](server.js)

**Responsibilities:**
- Runs Express web server (Port 3000)
- Connects to MQTT broker for real-time data streaming
- Persists telemetry to PostgreSQL database
- Provides REST API endpoints for frontend
- Bridges to Python AI engines

**Key Technologies:**
- **Express.js** - Web framework
- **MQTT** - Real-time message protocol (IoT standard)
- **PostgreSQL** - Persistent database
- **Node.js Streams** - Efficient real-time data handling

**Endpoints Provided:**
- `GET /` - Dashboard HTML
- `POST /api/device` - Register device
- `GET /api/telemetry` - Historical data
- `POST /api/order` - Create order
- `GET /api/events` - Recent alerts/events

---

### 3. **Database Layer** ([database/](database/))

**Architecture:** Dual-database system

#### PostgreSQL (Server-side)
- Persistent storage for all data
- Schema: `pg-schema.sql`
- Tables:
  - `devices` - Vehicle registry
  - `telemetry` - GPS/OBD-II measurements
  - `events` - Behavior alerts
  - `orders` - Logistics orders
  - `drivers` - Driver profiles

#### SQLite (Browser-side)
- Local database via `sql.js` (WebAssembly)
- Persisted to IndexedDB
- **Purpose:** Reduce API calls to Gemini AI (saves ~85% costs!)
- Contains last synced data for instant queries

**Key Files:**
- [database/schema.sql](database/schema.sql) - Table definitions
- [database/db.js](database/db.js) - SQLite initialization
- [database/dal.js](database/dal.js) - CRUD operations
- [database/ai-engine.js](database/ai-engine.js) - Smart AI query layer

---

### 4. **Scale Engine** ([scale_engine/](scale_engine/)) - **The Brain**

**Purpose:** 28 modular AI/ML engines that process raw data into intelligence

**Deployment:** FastAPI Python server with auto-generated REST endpoints

#### **Data Ingestion Subsystem** (9 engines)

1. **Stream Bus** - Distribute real-time data
2. **Time-Series Engine** - Historical queries (device trends over time)
3. **Fleet State Projector** - Current state of all vehicles
4. **Data Quality Checker** - Detect sensor errors/gaps
5. **Geo Processor** - Geofencing, corridor analysis, route validation
6. **Normalizer** - Convert raw units (km/h, RPM, etc.) to standard formats
7. **Schema Registry** - Validate incoming data structure
8. **Storage Tiers** - Hot/warm/cold data storage (frequent vs. archived)
9. **Replay/Backfill** - Historical data simulation for testing

#### **Smart Systems Subsystem** (8+ engines)

1. **Anomaly Detector** - Detects unusual vehicle behavior
2. **CEP Engine** (Complex Event Processing) - Real-time alert rules
3. **Digital Twin** - Virtual model of each vehicle
4. **Behavior Inference** - Driver safety scoring
5. **Predictive Maintenance** - Predict mechanical failures
6. **Fleet Optimizer** - Route optimization, load balancing
7. **Route ETA Compute** - Estimated time of arrival
8. **Signal Fusion** - Combine multiple sensors for accuracy

**Typical Data Flow:**
```
Raw MQTT → Stream Bus → Normalizer → Data Quality Check
    ↓
Fleet State Projector → Behavior Inference → Digital Twin
    ↓
Anomaly Detector + CEP → Alerts → Dashboard
```

---

### 5. **Route Engine** ([route_engine/](route_engine/))

**Purpose:** Specialized route analysis and optimization

**Components:**
- `route_analyzer.py` - Analyze historical routes
- `behavior_integrator.py` - Link driver behavior to route patterns
- `report_pipeline.py` - Generate insights for dispatchers

**Example Insight:** "Driver X completes routes 15% faster when using Route A, with lower harsh braking incidents"

---

### 6. **AI Backend** ([ai_backend/](ai_backend/))

**Purpose:** Cloud-based intelligence layer

**Key Components:**
- `analyzer.py` - FleetAnalyzer class (Gemini AI integration)
- `telegram_bot.py` - Telegram bot for alerts/commands
- `db.py` - Connection to PostgreSQL
- `report_generator.py` - Automated report generation

**Features:**
- Sends alerts via Telegram when critical events occur
- Generates daily/weekly fleet reports
- Provides Gemini-powered AI chat for fleet managers
- Analyzes patterns (e.g., "Speeding is highest on Route 3 at 9 PM")

---

### 7. **Frontend Dashboard** ([index.html](index.html), [login.js](login.js))

**Purpose:** Real-time visualization and control interface

**Features:**
1. **Login System** - Secure driver/manager access
2. **Real-time Telemetry Map** - Live GPS tracking
3. **Behavior Analytics** - Safety scores, harsh events
4. **Order Management** - View/assign delivery routes
5. **Alert Dashboard** - Critical events with timestamps
6. **AI Chat** - Ask questions about fleet data
   - Queries local SQLite first (instant, free)
   - Falls back to Gemini for complex analysis
7. **Mobile Responsive** - Works on tablets/phones

---

## 🔄 Complete Data Flow Example

**Scenario:** A driver accelerates aggressively

```
1. OBD-II Scanner detects:
   - Throttle = 95%
   - Speed = 25 km/h
   → Matches "Aggressive Launch" pattern

2. Arduino runs BehaviorAnalysis.h
   → Creates BehaviorEventLog (type=AGGRESSIVE_LAUNCH, severity=2)
   → Sends via MQTT

3. Node.js server receives:
   POST mqtt://monztrack/device01/obd2
   payload: {device_id, throttle: 95, speed: 25, ...}

4. Server saves to PostgreSQL
   INSERT INTO events (device_id, event_type, severity, timestamp)

5. Scale Engine (Data Ingestion):
   - Stream Bus distributes event
   - Data Quality Checker validates
   - Normalizer converts units
   - Stores in Hot Storage

6. Scale Engine (Smart Systems):
   - Behavior Inference engine: -5 points from safety score
   - Anomaly Detector: Checks if unusual for this driver
   - CEP Engine: Rule triggers if 3+ events in 1 hour
   - Digital Twin: Updates model of vehicle state

7. If threshold exceeded:
   - AI Backend sends Telegram alert to supervisor
   - Alert displays on dashboard (red indicator)
   - Event logged for reports

8. Dashboard User queries: "Why is driver X's score low?"
   - Frontend queries SQLite: 12 harsh braking events this week
   - Returns instantly (free)
   - Falls back to Gemini: "Driver X needs coaching on proper braking technique..."
```

---

## 🎯 Key Innovation Points

### 1. **Real-Time Processing at Scale**
- Handles 100+ vehicles simultaneously
- Sub-second alerts for critical behaviors
- Distributed architecture with Python + Node.js

### 2. **Safety-First Design**
- BehaviorAnalysis completely isolated from communications
- Critical alerts guaranteed even if network fails
- Local decision-making on Arduino

### 3. **Cost-Optimized AI**
- Local SQLite queries first (FREE)
- Only complex analysis goes to Gemini
- Saves 85% of API costs vs. naive approaches

### 4. **Multi-Layer Intelligence**
- **Hardware layer** - Early detection (Arduino)
- **Edge layer** - Fast streaming (Node.js)
- **Cloud layer** - Deep analytics (Python + Gemini)

### 5. **Modular & Extensible**
- 28 engines work independently
- Add new behavior detection without changing others
- Scale engine automatically exposes REST endpoints

---

## 📈 Typical Use Cases

### 1. **Fleet Manager View**
*"Show me the top 5 safest drivers this month"*
- Query SQLite for driver safety scores
- Compare against fleet average
- Show trends with interactive charts

### 2. **Route Optimization**
*"Which driver should I assign to Route A?"*
- Route Engine analyzes historical performance
- Matches driver profile to route characteristics
- Suggests optimal assignment

### 3. **Predictive Maintenance**
*"When will vehicle #42 need service?"*
- Predictive Maintenance engine analyzes engine metrics
- Predicts failure probability based on historical patterns
- Alert fleet manager before breakdown

### 4. **Alert & Response**
*"Alert: Driver exceeded harsh braking threshold"*
- CEP engine detects pattern violation
- Telegram bot sends real-time notification
- Dashboard shows event for training/coaching

---

## 🏗️ Technology Stack Summary

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Hardware** | Arduino C++ | Sensor data collection |
| **IoT Protocol** | MQTT | Real-time messaging |
| **Web Server** | Node.js + Express | HTTP API + real-time connections |
| **Database (Persistent)** | PostgreSQL | Long-term storage |
| **Database (Local)** | SQLite (sql.js) | Browser-side caching |
| **Analytics** | Python + FastAPI | 28 modular AI engines |
| **AI Engine** | Google Gemini | Natural language insights |
| **Notifications** | Telegram Bot | Alert delivery |
| **Frontend** | HTML/CSS/JavaScript | Responsive dashboard |
| **Testing** | Jest | Automated testing |

---

## 🚀 Key Metrics Your System Tracks

1. **Safety Score** (0-100)
   - Decreases with harsh braking, speeding, aggressive acceleration
   - Increases with safe, smooth driving

2. **Route Efficiency** (%)
   - Time taken vs. optimal time
   - Distance vs. planned distance

3. **Fuel Efficiency** (km/liter)
   - Correlated with behavior (smooth driving = better MPG)

4. **Maintenance Risk** (0-100)
   - Predicted likelihood of mechanical failure
   - Based on engine metrics trends

5. **Fleet Health** (Dashboard KPI)
   - Aggregate of all vehicle health metrics
   - Trend indicator for fleet-wide decisions

---

## 💡 What Makes This Thesis Project Unique

1. **Integrated IoT-to-Cloud System**
   - Not just sensors OR analytics
   - Complete end-to-end pipeline

2. **Real-Time Decision Making**
   - Alerts happen in seconds, not hours
   - Arduino + MQTT + FastAPI architecture

3. **Cost-Conscious AI**
   - Smart caching reduces AI API costs dramatically
   - Database-first queries

4. **Scalable Architecture**
   - Handles 100+ simultaneous vehicles
   - Modular 28-engine design
   - Distributed processing

5. **Human-Centered Analytics**
   - Telegram alerts for critical events
   - AI-powered insights, not just raw data
   - Driver coaching/training recommendations

---

## ❓ Common Questions for Your Presentation

**Q: Why split between Node.js and Python?**
A: Node.js excels at real-time data streaming and web APIs. Python's FastAPI is better for complex ML/AI engines. They complement each other.

**Q: How does it handle multiple vehicles?**
A: MQTT topic organization (`monztrack/device01/gps`, `device02/gps`, etc.) + PostgreSQL allows querying any device. Scale engine processes all streams in parallel.

**Q: What about offline vehicles?**
A: Local SQLite browser database means dashboard works offline. When device reconnects, data syncs. Arduino alerts work even without internet.

**Q: Why Telegram for alerts?**
A: Reliable, no additional app needed, supports multimedia (charts, reports), free API.

**Q: How is this different from Google Maps/Waze?**
A: Those track public roads. This is for **fleet operations** - internal behavior scoring, maintenance prediction, driver coaching, order assignment optimization.

---

