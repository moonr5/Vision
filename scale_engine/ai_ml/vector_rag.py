"""
Scale Engine — Vector Database + RAG Pipeline.
Semantic search over fleet history, driver manuals, incident logs,
and maintenance records using vector embeddings.
"""

import json
import math
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from collections import defaultdict


class VectorRAGEngine:
    """
    Retrieval-Augmented Generation for fleet intelligence.

    Stores:
      - Incident reports (vectorized descriptions)
      - Maintenance records
      - Driver coaching materials
      - Route safety notes

    Query → embed → retrieve top-K similar documents → augment AI prompt
    """

    def __init__(self):
        self._documents: Dict[str, List[Dict]] = defaultdict(list)
        # Simple TF-IDF-like term frequency index (no external deps needed)
        self._inverted_index: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self._doc_count = 0

    # ── Document ingestion ───────────────────────────────────────────────

    def index_document(self, collection: str, doc_id: str,
                       content: str, metadata: Dict[str, Any] = None):
        """
        Index a document into a collection.
        Collections: "incidents", "maintenance", "coaching", "routes", "manuals"
        """
        terms = self._tokenize(content)

        doc = {
            "id": doc_id,
            "collection": collection,
            "content": content,
            "metadata": metadata or {},
            "term_count": len(terms),
            "indexed_at": datetime.utcnow().isoformat(),
        }
        self._documents[collection].append(doc)
        self._doc_count += 1

        # Update inverted index with TF
        for term in set(terms):
            tf = terms.count(term) / max(len(terms), 1)
            self._inverted_index[collection][f"{term}:{doc_id}"] = tf

    def index_batch(self, collection: str, docs: List[Dict[str, Any]]):
        """Index multiple documents at once."""
        for d in docs:
            self.index_document(
                collection, d.get("id", f"doc_{self._doc_count}"),
                d.get("content", ""), d.get("metadata"),
            )

    # ── Semantic search ──────────────────────────────────────────────────

    def search(self, collection: str, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        Search for documents semantically similar to the query.
        Uses TF-IDF cosine similarity (production would use embeddings).
        """
        query_terms = self._tokenize(query)
        if not query_terms:
            return []

        scores = []
        for doc in self._documents.get(collection, []):
            score = self._cosine_similarity(query_terms, doc)
            scores.append((score, doc))

        scores.sort(key=lambda x: x[0], reverse=True)
        return [
            {"score": round(s, 4), "id": d["id"], "content": d["content"],
             "metadata": d.get("metadata", {}), "collection": collection}
            for s, d in scores[:top_k] if s > 0
        ]

    def search_all(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """Search across all collections."""
        results = []
        for collection in self._documents:
            results.extend(self.search(collection, query, top_k))
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    # ── RAG prompt builder ───────────────────────────────────────────────

    def build_rag_context(self, query: str, collections: List[str] = None,
                          top_k: int = 5) -> str:
        """
        Build a context string for RAG by retrieving relevant documents.
        This context is meant to be prepended to an AI prompt.
        """
        if collections is None:
            collections = list(self._documents.keys())

        all_results = []
        for coll in collections:
            all_results.extend(self.search(coll, query, top_k))

        all_results.sort(key=lambda x: x["score"], reverse=True)
        top = all_results[:top_k]

        if not top:
            return ""

        lines = ["RELEVANT CONTEXT FROM FLEET KNOWLEDGE BASE:"]
        for i, r in enumerate(top, 1):
            lines.append(f"\n[{i}] [{r['collection'].upper()}] {r['content'][:300]}")
            if r.get("metadata"):
                lines.append(f"    Meta: {json.dumps(r['metadata'], default=str)[:200]}")

        return "\n".join(lines)

    # ── Collection management ────────────────────────────────────────────

    def get_collections(self) -> Dict[str, int]:
        return {coll: len(docs) for coll, docs in self._documents.items()}

    def clear_collection(self, collection: str):
        self._documents[collection] = []
        # Clean inverted index for this collection
        keys_to_remove = [k for k in self._inverted_index[collection]]
        for k in keys_to_remove:
            del self._inverted_index[collection][k]

    # ── Seed fleet knowledge ─────────────────────────────────────────────

    def seed_fleet_knowledge(self):
        """Seed the knowledge base with initial fleet documents."""
        docs = [
            ("incidents", "Harsh braking events most common on Jakarta inner-city routes with high traffic density. Recommend driver coaching on progressive braking for urban routes."),
            ("incidents", "Fuel theft detected 3 times in Q1 — all cases involved S1 sensor trigger while vehicle stationary > 5 minutes in industrial zones at night."),
            ("maintenance", "Toyota HiAce coolant temp sensor prone to drift after 80,000 km. If MIL triggers with P0115, replace sensor before thermostat."),
            ("maintenance", "Isuzu Elf common issue: alternator voltage drops below 12.8V under load at idle. Check belt tension first before replacing alternator."),
            ("coaching", "Drivers with safety scores below 70 benefit most from 1-on-1 ride-along coaching sessions. Average improvement: +12 points in 30 days."),
            ("coaching", "Speeding events drop 40% after installing in-cab speed alerts. Combine with gamification — monthly safe driver awards."),
            ("routes", "Jakarta Inner Ring Road: avg speed 22 km/h 07:00-09:00, 28 km/h 16:00-18:00. Avoid for time-sensitive deliveries during these windows."),
            ("routes", "Tangerang-Merak toll road: high-speed corridor, increased accident risk in rainy season (Nov-Mar). Enforce 80 km/h max during wet conditions."),
        ]
        for i, (coll, content) in enumerate(docs):
            self.index_document(coll, f"seed_{i}", content)

    # ── Helpers ──────────────────────────────────────────────────────────

    def _tokenize(self, text: str) -> List[str]:
        """Simple tokenization: lowercase, split on non-alpha, remove stopwords."""
        stopwords = {"the", "a", "an", "is", "are", "was", "were", "in", "on", "at",
                      "to", "for", "of", "and", "or", "with", "this", "that", "it", "be"}
        text = text.lower()
        # Split on non-alphanumeric
        import re
        tokens = re.findall(r'[a-z0-9]+', text)
        return [t for t in tokens if t not in stopwords and len(t) > 1]

    def _cosine_similarity(self, query_terms: List[str], doc: Dict) -> float:
        """Compute TF-IDF cosine similarity between query and document."""
        doc_terms = self._tokenize(doc["content"])
        if not doc_terms:
            return 0.0

        # Query vector (TF only)
        query_vec = {}
        for t in query_terms:
            query_vec[t] = query_vec.get(t, 0) + 1
        query_norm = math.sqrt(sum(v**2 for v in query_vec.values()))

        # Doc vector (TF)
        doc_vec = {}
        for t in doc_terms:
            doc_vec[t] = doc_vec.get(t, 0) + 1
        doc_norm = math.sqrt(sum(v**2 for v in doc_vec.values()))

        if query_norm == 0 or doc_norm == 0:
            return 0.0

        # Dot product
        dot = sum(query_vec.get(t, 0) * doc_vec.get(t, 0) for t in set(query_vec) | set(doc_vec))
        return dot / (query_norm * doc_norm)
