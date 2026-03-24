"""
testcase_validator.py — Validate and clean generated test cases.

Removes:
  1. Hallucinated tests  — reference URLs / form fields not found in the graph
  2. Duplicate tests     — identical action sequences (same category + same steps)
  3. Form tests on form-less sites — any form/boundary/security test when the
                                     graph has zero form fields

Usage:
    python testcase_validator.py                        # uses defaults
    python testcase_validator.py --graph  results/transition_graph.json \
                                 --input  results/test_cases.json \
                                 --output results/test_cases_validated.json

Outputs:
    • A cleaned JSON file (same schema as the input)
    • A validation report printed to stdout
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

# ── Defaults ──────────────────────────────────────────────────────────────────
GRAPH_FILE  = "results/transition_graph.json"
INPUT_FILE  = "results/test_cases.json"
OUTPUT_FILE = "results/test_cases_validated.json"

# Categories that require forms to exist on the site
FORM_DEPENDENT_CATEGORIES = {
    # old testgen keys
    "form_valid", "form_invalid", "form_empty", "boundary", "security",
    # new canonical names
    "Functional", "Negative", "Validation", "Boundary", "Security",
}

# Categories that are always valid regardless of site structure
ALWAYS_VALID_CATEGORIES = {
    # deterministic categories
    "navigation", "link_coverage", "page_load", "content_validation",
    "negative_url", "negative", "click_navigation", "back_navigation",
    "element_presence", "logo_click", "multi_step_nav", "link_validity",
    "external_link", "multi_nav", "utility_link",
    # LLM creative categories (old keys)
    "user_scenario", "edge_case", "accessibility", "cross_page",
    "error_recovery", "seo_structure",
    # New canonical category names
    "Navigation",
}

# Categories that intentionally navigate to URLs NOT in the graph — skip URL check
NEGATIVE_CATEGORIES = {
    "negative_url", "negative", "external_link", "error_recovery", "error recovery"
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _norm_url(url) -> str:
    """Normalise a URL: lowercase scheme+host, strip trailing slash.
    Accepts None or non-string values gracefully."""
    if url is None:
        return ""
    url = str(url)
    try:
        p = urlparse(url.strip())
        path = p.path.rstrip("/") or "/"
        return f"{p.scheme.lower()}://{p.netloc.lower()}{path}"
    except Exception:
        return url.strip().rstrip("/")


def _step_fingerprint(step: dict) -> tuple:
    """Canonical key for a single step — used for duplicate detection.
    Guards against None values the LLM may emit for any field."""
    action = step.get("action") or ""
    target = step.get("target")   # may be None
    value  = step.get("value")    # may be None
    return (
        str(action).lower(),
        _norm_url(target),
        str(value if value is not None else "").strip().lower(),
    )


def _get_actions(tc: dict) -> list[dict]:
    """
    Return the executable action list for a test case.
    Handles both the new unified schema (actions[] key) and the old
    testgen schema (steps[] as list-of-dicts with seq/action/target).
    """
    actions = tc.get("actions")
    if actions and isinstance(actions, list) and actions and isinstance(actions[0], dict):
        return actions
    steps = tc.get("steps", [])
    if steps and isinstance(steps[0], dict):
        return steps
    return []


def _tc_fingerprint(tc: dict) -> tuple:
    """Canonical key for a whole test case — used for duplicate detection."""
    actions = sorted(_get_actions(tc), key=lambda s: s.get("seq", 0))
    # Include the title so two tests with the same steps but different
    # descriptions (different intent) are not flagged as duplicates.
    title = (tc.get("title") or tc.get("description") or "").lower().strip()
    return (
        tc.get("category", "").lower(),
        title,
        tuple(_step_fingerprint(s) for s in actions),
    )


# ── Graph loader ──────────────────────────────────────────────────────────────

def load_graph_facts(graph_path: str) -> dict:
    """
    Extract facts from transition_graph.json that validators need:
      - known_urls     : set of normalised URLs present as states
      - base_domain    : e.g. 'gectcr.ac.in'
      - all_form_fields: set of all field name/id strings across all states
      - has_forms      : True if any state has form fields
    """
    with open(graph_path, encoding="utf-8") as f:
        graph = json.load(f)

    states = graph.get("states", [])
    meta   = graph.get("meta", {})

    known_urls: set[str] = set()
    all_form_fields: set[str] = set()

    for state in states:
        url = state.get("url", "")
        if url:
            known_urls.add(_norm_url(url))
            # Also add a version without query string (for assert_url checks)
            known_urls.add(_norm_url(url.split("?")[0]))
        for field in state.get("form_fields", []):
            for key in ("name", "id"):
                val = field.get(key)
                if val:
                    bare = val.lower().lstrip("#.").strip()
                    all_form_fields.add(bare)           # e.g. 'firstname'
                    all_form_fields.add("#" + bare)     # e.g. '#firstname'
                    all_form_fields.add(val.lower())    # original case-folded

    # Also extract base_domain from seed_url
    seed = meta.get("seed_url", "")
    base_domain = urlparse(seed).netloc if seed else ""

    has_forms = (
        meta.get("states_with_forms", 0) > 0
        or meta.get("total_form_fields", 0) > 0
        or bool(all_form_fields)
    )

    return {
        "known_urls":      known_urls,
        "base_domain":     base_domain,
        "all_form_fields": all_form_fields,
        "has_forms":       has_forms,
        "raw_meta":        meta,
    }


# ── Individual check functions ────────────────────────────────────────────────

def check_url_hallucination(tc: dict, facts: dict) -> list[str]:
    """
    Return a list of problem strings if any `navigate` step targets
    a URL that is NOT in the graph's known states.

    Skipped for negative_url / external_link categories — those tests
    intentionally use URLs that are NOT in the graph.
    """
    # Negative / external tests intentionally use bad URLs — don't flag them
    if tc.get("category", "").lower() in NEGATIVE_CATEGORIES:
        return []
    # Tests flagged by the generator as intentionally negative
    if tc.get("_is_negative"):
        return []

    problems = []
    known    = facts["known_urls"]
    domain   = facts["base_domain"]
    for step in _get_actions(tc):
        if step.get("action") not in ("navigate", "click_link"):
            continue
        target = step.get("target", "")
        if not target:
            continue
        # Only check same-domain URLs (external ones are intentional)
        parsed = urlparse(target)
        if domain and parsed.netloc and domain not in parsed.netloc:
            continue  # external domain — skip
        # assert_url targets may be partial domain strings — skip those
        if not parsed.scheme:
            continue
        normed = _norm_url(target)
        # Also try without query string (common when LLM adds ?ref=... etc.)
        normed_noq = _norm_url(target.split("?")[0])
        if normed not in known and normed_noq not in known:
            problems.append(f"URL not in graph: {target}")
    return problems


def check_field_hallucination(tc: dict, facts: dict) -> list[str]:
    """
    Return a list of problem strings if any `fill_field` step targets
    a field name that is NOT in the graph's collected form fields.
    Only checked when the site actually has forms.
    """
    if not facts["has_forms"]:
        return []  # handled separately by check_form_category_on_formless_site
    known_fields = facts["all_form_fields"]
    if not known_fields:
        return []
    problems = []
    for step in _get_actions(tc):
        if step.get("action") != "fill_field":
            continue
        raw_field = step.get("target", "")
        # Normalise: strip leading # or . (CSS selector prefix the LLM may add)
        bare = raw_field.lower().lstrip("#.").strip()
        if not bare:
            continue
        # Accept if the bare name OR the #-prefixed name is in the known set
        if bare not in known_fields and raw_field.lower() not in known_fields:
            # One more pass: camelCase vs kebab-case tolerance
            # e.g. 'date-of-birth' might match 'dateOfBirth'
            # Strip all hyphens and compare
            bare_nohyphen = bare.replace("-", "")
            match = any(f.replace("-", "") == bare_nohyphen for f in known_fields)
            if not match:
                problems.append(f"form field not in graph: '{raw_field}'")
    return problems


def check_form_category_on_formless_site(tc: dict, facts: dict) -> list[str]:
    """
    Return a problem string if the test category requires forms but
    the site has no forms at all.
    """
    if facts["has_forms"]:
        return []
    cat = tc.get("category", "").lower()
    if cat in FORM_DEPENDENT_CATEGORIES:
        return [f"category '{cat}' requires forms but site has 0 form fields"]
    return []


# ── Main validator ────────────────────────────────────────────────────────────

CHECKS = [
    ("url_hallucination",           check_url_hallucination),
    ("field_hallucination",         check_field_hallucination),
    ("form_category_on_formless",   check_form_category_on_formless_site),
]


def validate(test_cases: list[dict], facts: dict) -> tuple[list[dict], list[dict]]:
    """
    Run all checks on each test case.

    Returns:
        (valid_cases, rejected_cases)
    where rejected_cases have an extra `_rejection_reasons` key.
    """
    valid    = []
    rejected = []
    seen_fingerprints: set = set()

    for tc in test_cases:
        reasons = []

        # Run structural checks
        for check_name, check_fn in CHECKS:
            problems = check_fn(tc, facts)
            for p in problems:
                reasons.append(f"[{check_name}] {p}")

        # Duplicate check (only if structurally OK so far)
        if not reasons:
            fp = _tc_fingerprint(tc)
            if fp in seen_fingerprints:
                reasons.append("[duplicate] identical action sequence already exists")
            else:
                seen_fingerprints.add(fp)

        if reasons:
            tc_out = dict(tc)
            tc_out["_rejection_reasons"] = reasons
            rejected.append(tc_out)
            tc_id = tc.get('test_id') or tc.get('id', '?')
            print(f"  [REJECT] {tc_id} -- {'; '.join(reasons)}")
        else:
            valid.append(tc)
            tc_id = tc.get('test_id') or tc.get('id', '?')
            print(f"  [OK]     {tc_id}")

    return valid, rejected


# ── Output writer ─────────────────────────────────────────────────────────────

def write_output(valid: list[dict], rejected: list[dict],
                 original_meta: dict, output_path: str):
    by_cat: dict = {}
    for tc in valid:
        cat = tc.get("category", "other")
        by_cat[cat] = by_cat.get(cat, 0) + 1

    out = {
        "meta": {
            **original_meta,
            "total_tests":         len(valid),
            "rejected_tests":      len(rejected),
            "by_category":         by_cat,
            "validation_applied":  True,
        },
        "test_cases": valid,
        "rejected":   rejected,
    }
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(json.dumps(out, indent=2, ensure_ascii=False),
                                  encoding="utf-8")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Validate and de-duplicate generated test cases."
    )
    parser.add_argument("--graph",  default=GRAPH_FILE,
                        help=f"Transition graph JSON (default: {GRAPH_FILE})")
    parser.add_argument("--input",  default=INPUT_FILE,
                        help=f"Test cases JSON to validate (default: {INPUT_FILE})")
    parser.add_argument("--output", default=OUTPUT_FILE,
                        help=f"Cleaned output file (default: {OUTPUT_FILE})")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"  Test Case Validator")
    print(f"  Graph : {args.graph}")
    print(f"  Input : {args.input}")
    print(f"  Output: {args.output}")
    print(f"{'='*60}\n")

    # Load graph facts
    try:
        facts = load_graph_facts(args.graph)
    except FileNotFoundError:
        print(f"ERROR: Graph file not found: {args.graph}", file=sys.stderr)
        sys.exit(1)

    print(f"Graph facts:")
    print(f"  Known URLs      : {len(facts['known_urls'])}")
    print(f"  Known fields    : {len(facts['all_form_fields'])} "
          f"{list(facts['all_form_fields'])[:5]}")
    print(f"  Has forms       : {facts['has_forms']}")
    print(f"  Base domain     : {facts['base_domain']}\n")

    # Load test cases
    try:
        with open(args.input, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"ERROR: Input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    test_cases    = data.get("test_cases", [])
    original_meta = data.get("meta", {})

    print(f"Validating {len(test_cases)} test cases...\n")

    valid, rejected = validate(test_cases, facts)

    write_output(valid, rejected, original_meta, args.output)

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*60}  SUMMARY")
    print(f"  Input total : {len(test_cases)}")
    print(f"  [OK]  Kept     : {len(valid)}")
    print(f"  [!!]  Rejected : {len(rejected)}")
    if rejected:
        print("\n  Rejection breakdown:")
        reason_counts: dict = {}
        for tc in rejected:
            for r in tc.get("_rejection_reasons", []):
                key = r.split("]")[0].strip("[") if "]" in r else r
                reason_counts[key] = reason_counts.get(key, 0) + 1
        for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1]):
            print(f"    {reason:<35} : {count}")
    print(f"\n  -> {args.output}\n")


if __name__ == "__main__":
    main()