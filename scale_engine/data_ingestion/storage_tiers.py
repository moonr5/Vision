"""
Scale Engine — Hot/Warm/Cold Storage Tiers.
Manages data lifecycle: real-time PG → compressed archive → S3 object storage.
Automates partition management, compression, and archival.
"""

import json
import os
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
import gzip
import io

from scale_engine import db


class StorageTierManager:
    """
    Manages 3 storage tiers:

    HOT  (0-7 days)   — Live PostgreSQL. Fast queries, recent data.
    WARM (7-90 days)  — Compressed TimescaleDB chunks. Slower, cheaper.
    COLD (90+ days)   — S3/MinIO object storage as gzipped JSONL.
                          Parallelized export + indexed for replay.
    """

    def __init__(self, s3_bucket: str = None, s3_prefix: str = "telemetry_archive/"):
        self.s3_bucket = s3_bucket or os.getenv("S3_ARCHIVE_BUCKET", "")
        self.s3_prefix = s3_prefix
        self._s3_client = None
        self._setup_s3()

    def _setup_s3(self):
        try:
            import boto3
            endpoint = os.getenv("S3_ENDPOINT", "")
            if endpoint:
                self._s3_client = boto3.client(
                    "s3",
                    endpoint_url=endpoint,
                    aws_access_key_id=os.getenv("S3_ACCESS_KEY", ""),
                    aws_secret_access_key=os.getenv("S3_SECRET_KEY", ""),
                )
            elif self.s3_bucket:
                self._s3_client = boto3.client("s3")
        except ImportError:
            print("[StorageTiers] boto3 not available — S3 cold storage disabled")
        except Exception as e:
            print(f"[StorageTiers] S3 setup failed: {e}")

    # ── Compression ──────────────────────────────────────────────────────

    async def compress_warm_chunks(self, older_than_days: int = 7):
        """Compress TimescaleDB chunks older than N days."""
        if not db.available():
            return {"compressed_chunks": 0}

        async with db._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT count(*) AS cnt FROM timescaledb_information.chunks "
                "WHERE is_compressed = false AND range_end < NOW() - ($1 || ' days')::INTERVAL",
                str(older_than_days),
            )
            if row and row["cnt"] > 0:
                try:
                    await conn.execute(
                        "SELECT compress_chunk(i) FROM "
                        "timescaledb_information.chunks i "
                        "WHERE is_compressed = false "
                        "AND range_end < NOW() - ($1 || ' days')::INTERVAL",
                        str(older_than_days),
                    )
                except Exception:
                    pass
            return {"compressed_chunks": row["cnt"] if row else 0}

    # ── Cold archival to S3 ──────────────────────────────────────────────

    async def archive_to_cold_storage(self, older_than_days: int = 90) -> Dict[str, Any]:
        """
        Export telemetry older than N days to S3 as compressed JSONL.
        Each file = one day, partitioned by device_id.
        """
        if not db.available():
            return {"archived": 0, "error": "DB unavailable"}
        if not self._s3_client:
            return {"archived": 0, "error": "S3 not configured"}

        archived = 0
        async with db._pool.acquire() as conn:
            # Fetch records in batches by day
            rows = await conn.fetch(
                """SELECT device_id, timestamp::date AS day, COUNT(*) AS cnt
                   FROM telemetry
                   WHERE timestamp < NOW() - ($1 || ' days')::INTERVAL
                   GROUP BY device_id, timestamp::date
                   ORDER BY day LIMIT 10""",
                str(older_than_days),
            )

        for r in rows:
            day_str = str(r["day"])
            device = r["device_id"]
            key = f"{self.s3_prefix}{device}/{day_str}.jsonl.gz"

            # Fetch actual data for this partition
            async with db._pool.acquire() as conn:
                data_rows = await conn.fetch(
                    """SELECT * FROM telemetry
                       WHERE device_id = $1
                         AND timestamp::date = $2::date
                       ORDER BY timestamp""",
                    device, day_str,
                )

            if not data_rows:
                continue

            # Compress to gzipped JSONL
            buf = io.BytesIO()
            with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
                for dr in data_rows:
                    record = dict(dr)
                    # Convert non-serializable types
                    for k, v in record.items():
                        if isinstance(v, datetime):
                            record[k] = v.isoformat()
                    gz.write((json.dumps(record, default=str) + "\n").encode())

            # Upload to S3
            try:
                self._s3_client.put_object(
                    Bucket=self.s3_bucket, Key=key,
                    Body=buf.getvalue(), ContentEncoding="gzip",
                )
                archived += r["cnt"]
            except Exception as e:
                print(f"[StorageTiers] S3 upload failed for {key}: {e}")

        return {"archived_records": archived, "files": len([r for r in rows])}

    # ── Warm/cold query router ───────────────────────────────────────────

    async def query_across_tiers(
        self, device_id: str, start: datetime, end: datetime,
    ) -> List[Dict[str, Any]]:
        """Query telemetry across all three tiers transparently."""
        results = []
        now = datetime.utcnow()

        # Hot
        hot = await db.query_tiered(
            "telemetry", "*", "device_id = $1", [device_id], tier="hot",
        )
        hot_filtered = [r for r in hot if start <= (r.get("timestamp") or now) <= end]
        results.extend(hot_filtered)

        # Warm
        if end < now - timedelta(days=7) or start < now - timedelta(days=7):
            warm = await db.query_tiered(
                "telemetry", "*", "device_id = $1", [device_id], tier="warm",
            )
            warm_filtered = [r for r in warm if start <= (r.get("timestamp") or now) <= end]
            results.extend(warm_filtered)

        # Cold (S3)
        if end < now - timedelta(days=90) or start < now - timedelta(days=90):
            if self._s3_client:
                cold = await self._scan_cold_storage(device_id, start, end)
                results.extend(cold)

        return sorted(results, key=lambda r: r.get("timestamp", now))

    async def _scan_cold_storage(
        self, device_id: str, start: datetime, end: datetime,
    ) -> List[Dict[str, Any]]:
        """Scan S3 cold storage for telemetry in the given date range."""
        results = []
        try:
            prefix = f"{self.s3_prefix}{device_id}/"
            paginator = self._s3_client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.s3_bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    key_date = obj["Key"].split("/")[-1].replace(".jsonl.gz", "")
                    try:
                        file_date = datetime.strptime(key_date, "%Y-%m-%d")
                        if start <= file_date <= end:
                            resp = self._s3_client.get_object(
                                Bucket=self.s3_bucket, Key=obj["Key"],
                            )
                            with gzip.GzipFile(fileobj=io.BytesIO(resp["Body"].read())) as gz:
                                for line in gz:
                                    results.append(json.loads(line))
                    except (ValueError, KeyError):
                        continue
        except Exception as e:
            print(f"[StorageTiers] Cold scan error: {e}")
        return results

    async def get_storage_stats(self) -> Dict[str, Any]:
        """Report storage usage across tiers."""
        stats = {"hot": {"estimated_rows": 0}, "warm": {"estimated_rows": 0}, "cold": {"files": 0}}
        if db.available():
            async with db._pool.acquire() as conn:
                for period, tier, field in [
                    ("7 days", "hot", "hot_rows"),
                    ("90 days", "warm", "warm_rows"),
                ]:
                    row = await conn.fetchrow(
                        f"SELECT COUNT(*) AS cnt FROM telemetry "
                        f"WHERE timestamp > NOW() - INTERVAL '{period}'"
                    )
                    if row:
                        stats[tier]["estimated_rows"] = row["cnt"]
        if self._s3_client and self.s3_bucket:
            try:
                resp = self._s3_client.list_objects_v2(
                    Bucket=self.s3_bucket, Prefix=self.s3_prefix, MaxKeys=1000,
                )
                stats["cold"]["files"] = resp.get("KeyCount", 0)
            except Exception:
                pass
        return stats
