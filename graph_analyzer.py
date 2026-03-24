"""
graph_analyzer.py — Load and analyse the transition graph.
Provides helper functions used by the LLM generator.

Supports the schema produced by webcrawl.py:
  {
    "states": { "<hash>": { "id": "...", "url": "...", "interactive_elements": [...], ... } },
    "transitions": [ { "from_state_id": "...", "to_state_id": "...", "action_type": "...", ... } ]
  }
"""
from __future__ import annotations
import json
from collections import defaultdict, deque
from typing import Optional


class GraphAnalyzer:
    def __init__(self, graph_path: str):
        with open(graph_path, encoding="utf-8") as f:
            self.graph = json.load(f)

        # ── Normalise states ───────────────────────────────────────────────────
        # The webcrawler stores states as a dict keyed by hash; the value has
        # an "id" field (the same hash).  Normalise to state_id → state dict.
        raw_states = self.graph.get("states", {})
        if isinstance(raw_states, dict):
            # webcrawl.py format: { "<hash>": { "id": "<hash>", "url": ..., ... } }
            self.states: dict[str, dict] = {}
            for key, state in raw_states.items():
                state_id = state.get("id") or key
                state["state_id"] = state_id          # expose uniform key
                self.states[state_id] = state
        else:
            # Legacy list format: [{ "state_id": "...", ... }]
            self.states = {s["state_id"]: s for s in raw_states}

        # ── Normalise transitions ──────────────────────────────────────────────
        # webcrawl.py uses "from_state_id" / "to_state_id"; legacy uses "from"/"to"
        raw_trans = self.graph.get("transitions", [])
        self.transitions: list[dict] = []
        for i, t in enumerate(raw_trans):
            from_id = t.get("from_state_id") or t.get("from", "")
            to_id   = t.get("to_state_id")   or t.get("to",   "")
            # Build a normalised copy so the rest of the code uses "from"/"to"
            normalised = dict(t)
            normalised["from"] = from_id
            normalised["to"]   = to_id
            normalised.setdefault("id", f"T{i+1:04d}")
            self.transitions.append(normalised)

        self.meta: dict = self.graph.get("meta", self.graph.get("metadata", {}))

        # Adjacency: state_id → list of outbound transitions
        self.adj: dict[str, list[dict]] = defaultdict(list)
        for t in self.transitions:
            self.adj[t["from"]].append(t)

    # ── Basic queries ──────────────────────────────────────────────────────────

    def states_of_type(self, page_type: str) -> list[dict]:
        return [s for s in self.states.values() if s.get("page_type") == page_type]

    def transitions_from(self, state_id: str) -> list[dict]:
        return self.adj.get(state_id, [])

    def internal_states(self) -> list[dict]:
        """States that are not external boundaries."""
        return [s for s in self.states.values()
                if not s.get("is_external_boundary", False)
                and s.get("page_type") != "external_boundary"]

    def form_states(self) -> list[dict]:
        """States that have at least one form field or input element."""
        result = []
        for s in self.states.values():
            if s.get("form_fields") or s.get("forms"):
                result.append(s)
                continue
            # webcrawl.py puts interactives in interactive_elements
            for el in s.get("interactive_elements", []):
                if el.get("type") == "input":
                    result.append(s)
                    break
        return result

    # ── BFS shortest path ──────────────────────────────────────────────────────

    def shortest_path(self, from_id: str, to_id: str) -> Optional[list[dict]]:
        """Return list of transitions representing the shortest path, or None."""
        if from_id == to_id:
            return []
        visited = {from_id}
        queue: deque[tuple[str, list[dict]]] = deque([(from_id, [])])
        while queue:
            current, path = queue.popleft()
            for t in self.adj.get(current, []):
                nxt = t["to"]
                new_path = path + [t]
                if nxt == to_id:
                    return new_path
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append((nxt, new_path))
        return None

    # ── Prompt context builder ─────────────────────────────────────────────────

    def to_prompt_context(self, max_states: int = 20, max_transitions: int = 30) -> str:
        """
        Produce a compact LLM-friendly text block summarising the graph.
        max_states / max_transitions cap output size to avoid 413 Payload Too Large.
        """
        lines: list[str] = []

        # ── States ────────────────────────────────────────────────────────────────────
        all_states = list(self.states.values())
        shown_states = all_states[:max_states]
        lines.append(f"=== STATES (showing {len(shown_states)}/{len(all_states)}) ===")

        for s in shown_states:
            sid       = s["state_id"]
            url       = s.get("url", "")[:80]
            title     = s.get("title", "")[:60]
            is_ext    = s.get("is_external_boundary", False)
            is_error  = s.get("is_error_page", False)
            page_type = ("external_boundary" if is_ext
                         else "error_page" if is_error
                         else s.get("page_type", "unknown"))

            lines.append(f"  [{sid[:16]}] {page_type} | {url} | {title}")

            # Form fields only (most useful for test generation)
            form_fields = s.get("form_fields", [])
            if form_fields:
                field_names = [f.get("name") or f.get("id", "?") for f in form_fields[:8]]
                lines.append(f"    fields: {field_names}")

        # ── Transitions ──────────────────────────────────────────────────────────
        shown_trans = self.transitions[:max_transitions]
        lines.append(f"\n=== TRANSITIONS (showing {len(shown_trans)}/{len(self.transitions)}) ===")

        for t in shown_trans:
            tid        = t.get("id", "?")
            from_short = t["from"][:16]
            to_short   = t["to"][:16]
            behavior   = t.get("action_type", t.get("behavior", "?"))
            text       = t.get("element_text", t.get("action", {}).get("text", ""))[:40]
            href       = t.get("action", {}).get("href", "")[:60]
            lines.append(f"  [{tid}] {from_short}… → {to_short}… | {behavior} | '{text}' | {href}")

        return "\n".join(lines)

    # ── Coverage metrics ───────────────────────────────────────────────────────

    def coverage_metrics(self, test_cases: list[dict]) -> dict:
        """
        Given generated test cases, compute what % of states
        and transitions are referenced.
        """
        covered_states: set[str] = set()
        covered_transitions: set[str] = set()

        for tc in test_cases:
            for tid in tc.get("source_transitions", []):
                covered_transitions.add(tid)
                for t in self.transitions:
                    if t.get("id") == tid:
                        covered_states.add(t["from"])
                        covered_states.add(t["to"])

        internal = self.internal_states()
        total_states = len(internal)
        total_transitions = len([
            t for t in self.transitions
            if not self.states.get(t["to"], {}).get("is_external_boundary", False)
        ])

        return {
            "states_covered":      len(covered_states),
            "states_total":        total_states,
            "states_pct":          round(len(covered_states) / max(total_states, 1) * 100, 1),
            "transitions_covered": len(covered_transitions),
            "transitions_total":   total_transitions,
            "transitions_pct":     round(len(covered_transitions) / max(total_transitions, 1) * 100, 1),
        }
