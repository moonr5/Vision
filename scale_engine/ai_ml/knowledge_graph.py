"""
Scale Engine — Knowledge Graph.
Links drivers, vehicles, routes, incidents, and sensor patterns
into a queryable graph for reasoning and pattern discovery.
"""

from typing import Dict, Any, List, Optional, Set, Tuple
from datetime import datetime
from collections import defaultdict
import json


class KnowledgeGraph:
    """
    Fleet knowledge graph for cross-entity reasoning.

    Node types: Driver, Vehicle, Route, Incident, Sensor, Location, Order
    Edge types: DRIVES, OWNS, ASSIGNED_TO, OCCURRED_AT, INVOLVES,
                ON_ROUTE, NEAR, CORRELATED_WITH, CAUSED_BY
    """

    def __init__(self):
        self._nodes: Dict[str, Dict[str, Any]] = defaultdict(dict)  # type → {id → attrs}
        self._edges: List[Dict[str, Any]] = []
        self._adjacency: Dict[str, Dict[str, List[str]]] = defaultdict(lambda: defaultdict(list))  # node_id → {edge_type → [target_ids]}

    # ── Node management ──────────────────────────────────────────────────

    def add_node(self, node_type: str, node_id: str, attributes: Dict[str, Any] = None):
        """Add or update a node."""
        self._nodes[node_type][node_id] = {
            **(attributes or {}),
            "_added_at": datetime.utcnow().isoformat(),
        }

    def add_edge(self, from_type: str, from_id: str, edge_type: str,
                 to_type: str, to_id: str, attributes: Dict[str, Any] = None):
        """Add a directed edge between two nodes."""
        from_key = f"{from_type}:{from_id}"
        to_key = f"{to_type}:{to_id}"

        edge = {
            "from": from_key, "to": to_key,
            "type": edge_type,
            "from_type": from_type, "to_type": to_type,
            "attributes": attributes or {},
            "created_at": datetime.utcnow().isoformat(),
        }
        self._edges.append(edge)
        self._adjacency[from_key][edge_type].append(to_key)

    # ── Graph queries ────────────────────────────────────────────────────

    def query_related(self, node_type: str, node_id: str,
                      edge_types: List[str] = None, depth: int = 1) -> List[Dict]:
        """Find all nodes related to a given node up to N hops."""
        start_key = f"{node_type}:{node_id}"
        visited = {start_key}
        results = []

        queue = [(start_key, 0)]
        while queue:
            current, d = queue.pop(0)
            if d >= depth:
                continue

            adj = self._adjacency.get(current, {})
            for etype, targets in adj.items():
                if edge_types and etype not in edge_types:
                    continue
                for target in targets:
                    if target not in visited:
                        visited.add(target)
                        t_type, t_id = target.split(":", 1)
                        node_data = self._nodes.get(t_type, {}).get(t_id, {})
                        results.append({
                            "node_type": t_type, "node_id": t_id,
                            "edge_type": etype, "depth": d + 1,
                            "attributes": node_data,
                        })
                        queue.append((target, d + 1))

        return results

    def find_pattern(self, pattern: List[Tuple[str, str, str]]) -> List[List[Dict]]:
        """
        Find subgraph matches for a pattern.
        Pattern: [(node_type, edge_type, node_type), ...]
        Example: [("Driver", "DRIVES", "Vehicle"), ("Vehicle", "INVOLVED_IN", "Incident")]
        """
        matches = []
        # For each starting node of the first type
        for start_id in self._nodes.get(pattern[0][0], {}):
            path = self._follow_pattern(start_id, pattern)
            if path:
                matches.append(path)
        return matches

    def _follow_pattern(self, start_id: str, pattern: List[Tuple], step: int = 0) -> Optional[List[Dict]]:
        if step >= len(pattern):
            return []
        node_type, edge_type, next_type = pattern[step]
        key = f"{node_type}:{start_id}"
        adj = self._adjacency.get(key, {}).get(edge_type, [])
        for target in adj:
            t_type, t_id = target.split(":", 1)
            if t_type == next_type:
                node_data = self._nodes.get(t_type, {}).get(t_id, {})
                rest = self._follow_pattern(t_id, pattern, step + 1)
                if rest is not None:
                    return [{"type": t_type, "id": t_id, "attrs": node_data}] + rest
        return None if step < len(pattern) else []

    # ── Graph analytics ──────────────────────────────────────────────────

    def get_graph_stats(self) -> Dict[str, Any]:
        """Return graph statistics."""
        node_counts = {t: len(ids) for t, ids in self._nodes.items()}
        edge_counts = defaultdict(int)
        for e in self._edges:
            edge_counts[e["type"]] += 1

        return {
            "total_nodes": sum(node_counts.values()),
            "nodes_by_type": node_counts,
            "total_edges": len(self._edges),
            "edges_by_type": dict(edge_counts),
        }

    def get_central_nodes(self, top_n: int = 10) -> List[Dict]:
        """Find most-connected nodes (by degree centrality)."""
        degrees = defaultdict(int)
        for e in self._edges:
            degrees[e["from"]] += 1
            degrees[e["to"]] += 1

        sorted_nodes = sorted(degrees.items(), key=lambda x: x[1], reverse=True)[:top_n]
        return [{"node": n, "degree": d, "type": n.split(":")[0]} for n, d in sorted_nodes]

    def find_correlations(self) -> List[Dict]:
        """Find frequently co-occurring incident types on the same route."""
        route_incidents = defaultdict(list)
        for e in self._edges:
            if e["type"] == "OCCURRED_AT" and e["from_type"] == "Incident":
                route_incidents[e["to"]].append(e["from"])

        correlations = []
        for route, incidents in route_incidents.items():
            if len(incidents) >= 2:
                correlations.append({
                    "route": route,
                    "incident_count": len(incidents),
                    "incident_types": list(set(i.split(":")[0] for i in incidents)),
                })

        return sorted(correlations, key=lambda c: c["incident_count"], reverse=True)

    # ── Seed graph ───────────────────────────────────────────────────────

    def seed_fleet_graph(self, drivers: List[Dict] = None, devices: List[Dict] = None):
        """Seed the knowledge graph from fleet data."""
        if drivers:
            for d in drivers:
                did = d.get("driver_id") or d.get("id")
                self.add_node("Driver", did, {"name": d.get("driver_name") or d.get("name"),
                                "safety_score": d.get("safety_score")})

        if devices:
            for dev in devices:
                devid = dev.get("device_id") or dev.get("id")
                self.add_node("Vehicle", devid, {"name": dev.get("name", devid),
                                 "status": dev.get("status")})
