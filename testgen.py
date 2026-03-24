
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from graph_analyzer import GraphAnalyzer
from llm_generator  import generate_knowledge_base, generate_test_cases, DEFAULT_MODEL
from code_emitter   import emit_pytest_file, CONFTEST
from testcase_validator import load_graph_facts, validate, write_output as _write_validated

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write(path: str, content: str):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    log.info(f"Saved → {path}")


def _write_json(path: str, data):
    _write(path, json.dumps(data, indent=2, ensure_ascii=False))


def _kb_is_stale(kb: dict, graph_meta: dict) -> bool:
    """
    Return True if the cached knowledge base looks like it was generated
    from a different site than the current transition graph.

    Heuristic: compare the graph's site URL (stored as base_url or seed_url
    in the graph metadata) against both `base_url` and `site_name` in the KB.
    If neither matches, warn and treat the KB as stale.
    """
    def _normalise(url: str) -> str:
        """Lowercase, strip trailing slash, strip www. prefix."""
        u = url.lower().rstrip("/")
        # Strip scheme
        if "://" in u:
            u = u.split("://", 1)[1]
        # Strip www.
        if u.startswith("www."):
            u = u[4:]
        return u

    # Different graph generators write different key names
    raw_graph = graph_meta.get("base_url") or graph_meta.get("seed_url") or ""
    if not raw_graph:
        return False   # can't tell; assume it's fine

    graph_domain = _normalise(raw_graph)
    kb_base      = _normalise(kb.get("base_url") or "")
    kb_name      = (kb.get("site_name") or "").lower()

    if kb_base and graph_domain not in kb_base and kb_base not in graph_domain:
        log.warning(
            f"Stale KB detected — KB base_url='{kb_base}' does not match "
            f"graph base_url='{graph_domain}'. Re-running Phase 1."
        )
        return True

    # If there's no base_url in the KB but the site_name looks unrelated
    if not kb_base and kb_name:
        # Extract first meaningful label from domain, e.g. "todolistme" or "calculator"
        domain_hint = graph_domain.split(".")[0]
        if domain_hint and domain_hint not in kb_name.replace(" ", "").lower():
            log.warning(
                f"Stale KB detected — KB site_name='{kb_name}' does not seem "
                f"to match graph domain hint='{domain_hint}'. Re-running Phase 1."
            )
            return True

    return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Generate Selenium test cases from a transition graph using Groq LLM."
    )
    parser.add_argument(
        "--graph", default="results/transition_graph.json",
        help="Path to transition_graph.json (default: results/transition_graph.json)"
    )
    parser.add_argument(
        "--kb", default=None,
        help="Path to an existing knowledge_base.json. "
             "If omitted, Phase 1 will generate it via LLM."
    )
    parser.add_argument(
        "--force-kb", action="store_true",
        help="Force re-generation of the knowledge base even if one already exists on disk."
    )
    parser.add_argument(
        "--out", default="results",
        help="Output directory (default: results/)"
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL,
        help=f"Groq model name (default: {DEFAULT_MODEL})"
    )
    parser.add_argument(
        "--skip-validate", action="store_true",
        help="Skip automatic test-case validation after generation."
    )
    args = parser.parse_args()

    t0 = time.time()

    # ── Load graph ─────────────────────────────────────────────────────────────
    log.info(f"Loading transition graph: {args.graph}")
    if not os.path.exists(args.graph):
        log.error(f"Graph file not found: {args.graph}")
        sys.exit(1)

    analyzer      = GraphAnalyzer(args.graph)
    graph_context = analyzer.to_prompt_context()
    graph_meta    = analyzer.meta

    log.info(
        f"Graph loaded: {len(analyzer.states)} states, "
        f"{len(analyzer.transitions)} transitions"
    )

    # ── Phase 1 — Knowledge Base ───────────────────────────────────────────────
    kb_path = os.path.join(args.out, "knowledge_base.json")

    if args.kb:
        # Explicit path supplied — always use it
        log.info(f"Using provided knowledge base: {args.kb}")
        with open(args.kb, encoding="utf-8") as f:
            knowledge_base = json.load(f)

    elif not args.force_kb and os.path.exists(kb_path):
        # Cached KB exists — check if it's stale before reusing
        with open(kb_path, encoding="utf-8") as f:
            cached_kb = json.load(f)

        if _kb_is_stale(cached_kb, graph_meta):
            log.info("Phase 1 — Regenerating stale knowledge base …")
            knowledge_base = generate_knowledge_base(graph_context, model=args.model)
            _write_json(kb_path, knowledge_base)
            log.info("Phase 1 complete.")
        else:
            log.info(f"Reusing existing knowledge base: {kb_path}")
            knowledge_base = cached_kb

    else:
        if args.force_kb:
            log.info("Phase 1 — Forced knowledge base re-generation …")
        else:
            log.info("Phase 1 — LLM knowledge base generation starting …")
        knowledge_base = generate_knowledge_base(graph_context, model=args.model)
        _write_json(kb_path, knowledge_base)
        log.info("Phase 1 complete.")

    # ── Phase 2a — Deterministic test generation (disabled) ──────────────────
    # Uncomment the block below to re-enable deterministic test generation.
    # from deterministic_generator import generate_all as _det_generate
    # log.info("Phase 2a — Deterministic test generation starting ...")
    # base_url   = knowledge_base.get("base_url", "")
    # det_tests  = _det_generate(args.graph, base_url=base_url, max_multi_step=30)
    # log.info(f"Phase 2a complete — {len(det_tests)} deterministic tests generated.")
    det_tests = []  # deterministic generation disabled

    # ── Phase 2b — LLM creative test generation (chunked, 5 per call) ──────────
    log.info("Phase 2b — LLM creative test generation starting ...")

    # Build graph_facts for the LLM generator
    graph_data  = json.loads(Path(args.graph).read_text(encoding="utf-8"))
    raw_states  = graph_data.get("states", [])
    raw_trans   = graph_data.get("transitions", [])
    graph_facts = {
        "has_forms":   (graph_meta.get("states_with_forms", 0) > 0
                        or graph_meta.get("total_form_fields", 0) > 0),
        "states":      raw_states,
        "transitions": raw_trans,
    }

    llm_tests = generate_test_cases(
        graph_context, knowledge_base, model=args.model, graph_facts=graph_facts
    )
    log.info(f"Phase 2b complete — {len(llm_tests)} LLM creative tests generated.")

    # ── Merge and renumber ─────────────────────────────────────────────────────
    test_cases = llm_tests  # det_tests disabled; re-add det_tests here to re-enable
    log.info(f"Total tests: {len(test_cases)} test cases.")

    # ── Compute coverage metrics ───────────────────────────────────────────────
    coverage = analyzer.coverage_metrics(test_cases)

    # ── Assemble output JSON ───────────────────────────────────────────────────
    output = {
        "meta": {
            "generated_at":      datetime.now().isoformat(timespec="seconds"),
            "model":             args.model,
            "graph_file":        args.graph,
            "graph_states":      len(analyzer.states),
            "graph_transitions": len(analyzer.transitions),
            "total_tests":       len(test_cases),
            "by_category":       _count_by_category(test_cases),
            "coverage":          coverage,
        },
        "test_cases": test_cases,
    }

    tc_path     = os.path.join(args.out, "test_cases.json")
    tc_raw_path = os.path.join(args.out, "test_cases_raw.json")
    _write_json(tc_raw_path, output)   # always keep the raw LLM output

    # ── Phase 3 — Validation (optional) ───────────────────────────────────────
    validated_count = len(test_cases)
    rejected_count  = 0

    if not args.skip_validate:
        log.info("Phase 3 — Validating and de-duplicating test cases …")
        facts    = load_graph_facts(args.graph)
        valid, rejected = validate(test_cases, facts)
        validated_count = len(valid)
        rejected_count  = len(rejected)
        _write_validated(valid, rejected, output["meta"], tc_path)
        log.info(
            f"Phase 3 complete — {validated_count} kept, {rejected_count} rejected."
        )
    else:
        _write_json(tc_path, output)

    # ── Summary ────────────────────────────────────────────────────────────────
    elapsed = time.time() - t0
    m       = output["meta"]

    print("\n" + "=" * 60)
    print(f"  Done in {elapsed:.1f}s")
    print(f"  Tests generated : {m['total_tests']}")
    if not args.skip_validate:
        print(f"  After validation: {validated_count} kept, {rejected_count} rejected")
    print(f"  By category     : {m['by_category']}")
    print(f"  Coverage        : {coverage['states_pct']}% states | "
          f"{coverage['transitions_pct']}% transitions")
    print(f"\n  Outputs:")
    print(f"    {kb_path}")
    print(f"    {tc_path}  (validated)")
    print(f"    {tc_raw_path}  (raw LLM output)")
    print("=" * 60)
    print("\nTo run the generated tests:")
    print(f"  python execute_tests.py --input {tc_path}")


def _count_by_category(test_cases: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for tc in test_cases:
        cat = tc.get("category", "other")
        counts[cat] = counts.get(cat, 0) + 1
    return counts


if __name__ == "__main__":
    main()
