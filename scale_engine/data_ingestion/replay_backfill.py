"""
Scale Engine — Replay & Backfill System.
Reprocesses historical telemetry when rules, models, or scoring algorithms change.
Supports time-range replay, selective backfill, and dry-run mode.
"""

import json
import asyncio
from typing import Dict, Any, List, Optional, Callable, Awaitable
from datetime import datetime, timedelta
from dataclasses import dataclass

from scale_engine import db


@dataclass
class ReplayConfig:
    start: datetime
    end: datetime
    device_ids: Optional[List[str]] = None
    batch_size: int = 500
    parallel_batches: int = 2
    dry_run: bool = False
    reprocess_rules: List[str] = None   # e.g., ["behavior_scoring", "fuel_theft", "geofence"]


class ReplayBackfillEngine:
    """
    Replay engine for historical telemetry reprocessing.

    Use cases:
      - New behaviour scoring rules → replay last 30 days
      - Updated geofence boundaries → recheck all crossings
      - Fixed fuel theft detection → backfill missed alerts
      - Schema migration → validate historical data
    """

    def __init__(self):
        self._processors: Dict[str, Callable[[List[Dict]], Awaitable[List[Dict]]]] = {}
        self._replay_state: Dict[str, Any] = {"running": False, "progress": 0}

    def register_processor(self, name: str, fn: Callable[[List[Dict]], Awaitable[List[Dict]]]):
        """Register a processing function for a rule type."""
        self._processors[name] = fn

    async def replay(
        self, config: ReplayConfig,
    ) -> Dict[str, Any]:
        """
        Replay historical telemetry through registered processors.
        Returns a summary of what was reprocessed and any new findings.
        """
        if not db.available():
            return {"error": "Database unavailable"}

        self._replay_state = {"running": True, "progress": 0, "config": config.__dict__}

        # Count total records to replay
        async with db._pool.acquire() as conn:
            count_row = await conn.fetchrow(
                """SELECT COUNT(*) AS cnt FROM telemetry
                   WHERE timestamp BETWEEN $1 AND $2"""
                + (" AND device_id = ANY($3)" if config.device_ids else ""),
                config.start, config.end,
                *(config.device_ids or []),
            )
        total = count_row["cnt"] if count_row else 0

        results = {
            "total_records": total,
            "processed": 0,
            "new_alerts": 0,
            "updated_records": 0,
            "errors": 0,
            "details": [],
            "duration_seconds": 0,
        }

        if total == 0:
            self._replay_state["running"] = False
            return results

        start_time = datetime.utcnow()

        # Process in batches
        offset = 0
        while offset < total and self._replay_state["running"]:
            async with db._pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT * FROM telemetry
                       WHERE timestamp BETWEEN $1 AND $2"""
                    + (" AND device_id = ANY($3)" if config.device_ids else "")
                    + " ORDER BY timestamp LIMIT $4 OFFSET $5",
                    config.start, config.end,
                    *(config.device_ids or []), config.batch_size, offset,
                )

            if not rows:
                break

            batch = [dict(r) for r in rows]

            # Run each registered processor
            for rule_name in (config.reprocess_rules or self._processors.keys()):
                processor = self._processors.get(rule_name)
                if not processor:
                    continue
                try:
                    if not config.dry_run:
                        processor_results = await processor(batch)
                        results["new_alerts"] += processor_results.get("new_alerts", 0) if isinstance(processor_results, dict) else 0
                        results["updated_records"] += processor_results.get("updated", 0) if isinstance(processor_results, dict) else 0
                    results["details"].append({"offset": offset, "rule": rule_name, "batch_size": len(batch)})
                except Exception as e:
                    results["errors"] += 1
                    results["details"].append({"offset": offset, "rule": rule_name, "error": str(e)})

            results["processed"] += len(batch)
            offset += config.batch_size
            self._replay_state["progress"] = min(offset / total * 100, 100)

        results["duration_seconds"] = round((datetime.utcnow() - start_time).total_seconds(), 1)
        self._replay_state["running"] = False
        self._replay_state["last_result"] = results

        return results

    async def backfill_missing(
        self, device_id: str, start: datetime, end: datetime,
    ) -> Dict[str, Any]:
        """
        Detect and backfill gaps in telemetry data.
        Finds time ranges with no data and flags them.
        """
        if not db.available():
            return {"error": "Database unavailable"}

        async with db._pool.acquire() as conn:
            # Find gaps > 5 minutes
            gaps = await conn.fetch(
                """SELECT timestamp, LEAD(timestamp) OVER (ORDER BY timestamp) AS next_ts
                   FROM telemetry
                   WHERE device_id = $1 AND timestamp BETWEEN $2 AND $3
                   ORDER BY timestamp""",
                device_id, start, end,
            )

        gap_report = []
        for i, row in enumerate(gaps):
            if row["next_ts"] and row["timestamp"]:
                gap_seconds = (row["next_ts"] - row["timestamp"]).total_seconds()
                if gap_seconds > 300:  # 5 minutes
                    gap_report.append({
                        "from": row["timestamp"].isoformat(),
                        "to": row["next_ts"].isoformat(),
                        "gap_minutes": round(gap_seconds / 60, 1),
                    })

        return {
            "device_id": device_id,
            "period": f"{start.isoformat()} → {end.isoformat()}",
            "gaps_found": len(gap_report),
            "gaps": gap_report,
            "total_gap_minutes": round(sum(g["gap_minutes"] for g in gap_report), 1),
        }

    def cancel_replay(self):
        """Cancel a running replay."""
        self._replay_state["running"] = False

    def get_status(self) -> Dict[str, Any]:
        """Get current replay status."""
        return dict(self._replay_state)
