"""
llm_generator.py — Build prompts and call LLM via Groq
to generate knowledge bases and exploratory test cases.

Uses the Groq Python SDK (groq.Groq).
Key rotation: set GROQ_API_KEY_1, GROQ_API_KEY_2, GROQ_API_KEY_3 in .env
for rotation on 429 rate-limit errors, or just GROQ_API_KEY for a single key.

Fixes applied (generic — works with any site):
  1. KB_MAX_TOKENS raised 2000→4000 — prevents response truncation mid-JSON
  2. _repair_truncated_object() added — mirrors _repair_truncated_array for dicts
  3. _extract_first_json_structure() now uses a brace-counting scanner instead of
     greedy regex, correctly handles nested {...} in string values
  4. KB prompt schema slimmed — removed deeply-nested keys that ballooned the
     response beyond the token budget; all critical fields kept
  5. _extract_json() calls both array and object repair regardless of response type
  6. generate_knowledge_base() validates required KB keys after parsing and falls
     back to a minimal skeleton rather than crashing
"""

from __future__ import annotations
import json
import logging
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
from groq import Groq

# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────

load_dotenv()
log = logging.getLogger(__name__)

DEFAULT_MODEL  = os.getenv("GROQ_MODEL",    "llama-3.3-70b-versatile")
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL", "llama-3.1-8b-instant")

# FIX #1: KB_MAX_TOKENS was 2000 — far too low for a multi-key JSON object.
# The KB prompt produces ~800-1200 tokens of JSON; 2000 left almost no headroom
# and caused truncation mid-object, producing unparseable JSON.
KB_MAX_TOKENS       = 4000   # raised from 2000
TESTCASE_MAX_TOKENS = 3000
TESTCASE_CHUNK_SIZE = 15
TESTS_PER_CATEGORY  = 6
GRAPH_CONTEXT_LIMIT = 30

# ──────────────────────────────────────────────────────────────────────────────
# API Key Pool
# ──────────────────────────────────────────────────────────────────────────────

def _load_groq_keys() -> list[str]:
    """
    Discover all Groq API keys from the environment — no hardcoded count.
    Supports GROQ_API_KEY_1, _2, _3, ... up to any number,
    plus a bare GROQ_API_KEY fallback. Duplicate values are deduplicated.
    """
    seen: set[str] = set()
    keys: list[str] = []
    # Numbered sequence: GROQ_API_KEY_1, _2, _3, ... stop at first gap
    i = 0
    while True:
        k = os.getenv(f"GROQ_API_KEY_{i}")
        if k is None:
            break
        i += 1
        k = k.strip()   # strip accidental whitespace from .env
        if k and not k.startswith("your_") and k not in seen:
            seen.add(k)
            keys.append(k)
    # Any remaining GROQ_API_KEY_<suffix> entries beyond the numbered sequence
    for var, val in os.environ.items():
        if var.startswith("GROQ_API_KEY_") and val:
            val = val.strip()
            if not val.startswith("your_") and val not in seen:
                seen.add(val)
                keys.append(val)
    # Bare GROQ_API_KEY fallback
    k = os.getenv("GROQ_API_KEY")
    if k:
        k = k.strip()
        if k and not k.startswith("your_") and k not in seen:
            seen.add(k)
            keys.append(k)
    return keys

_ALL_KEYS: list[str] = _load_groq_keys()

if not _ALL_KEYS:
    raise EnvironmentError(
        "No Groq API key found in .env.\n"
        "Add one or more keys:\n"
        "  GROQ_API_KEY=gsk_...          # single key\n"
        "  GROQ_API_KEY_1=gsk_...        # rotation (add _2, _3, ... as needed)\n"
    )

log.info(f"Groq: {len(_ALL_KEYS)} key(s) loaded — round-robin rotation active.")

_key_index = 0


def _get_current_key() -> str:
    return _ALL_KEYS[_key_index]


def _rotate_key() -> bool:
    global _key_index
    nxt = (_key_index + 1) % len(_ALL_KEYS)
    if nxt == _key_index:
        return False
    _key_index = nxt
    log.info(f"  [KEY] Rotated to Groq key #{_key_index + 1}")
    return True


def _make_client() -> Groq:
    # max_retries=0 prevents the SDK from silently retrying 429s on the same key.
    return Groq(api_key=_get_current_key(), max_retries=0)


# ──────────────────────────────────────────────────────────────────────────────
# Prompt builders
# ──────────────────────────────────────────────────────────────────────────────

def _build_kb_prompt(graph_context: str) -> str:
    """
    FIX #4: Slimmed KB schema — removed deeply-nested sub-objects that caused
    the LLM to produce 3000+ token responses, blowing past KB_MAX_TOKENS=2000
    and truncating mid-object.

    The schema now uses flat arrays instead of nested objects where possible,
    and removes the verbose navigation/auth sub-keys that aren't used downstream.
    All fields that ARE used by the test generators are kept.
    """
    return f"""You are a senior QA analyst. Study this web application's transition graph
and produce a concise structured knowledge base for Selenium test generation.

{graph_context}

OUTPUT a single valid JSON object with EXACTLY the keys below.
No prose, no markdown fences, no comments — raw JSON only.

{{
  "site_name": "...",
  "base_url": "https://...",
  "site_description": "one sentence describing the site purpose",
  "pages": {{
    "<slug>": {{
      "url": "...",
      "title": "...",
      "page_type": "auth_page | form_page | nav_hub | generic_page | external_boundary | error_page",
      "description": "...",
      "requires_auth": false
    }}
  }},
  "form_data": {{
    "valid":         {{ "<field_name>": "<realistic valid value>" }},
    "invalid":       {{ "<field_name>": "<invalid value>" }},
    "boundary":      {{ "<field_name>": "<edge case value>" }},
    "special_chars": {{ "<field_name>": "<XSS payload>" }}
  }},
  "critical_flows": [
    {{
      "name": "...",
      "steps": ["<step1>", "<step2>"],
      "expected": "..."
    }}
  ],
  "negative_test_scenarios": [
    {{
      "name": "...",
      "description": "...",
      "expected": "..."
    }}
  ]
}}

Rules:
- pages: EACH KEY must be a UNIQUE URL slug — use the last path segment of the URL.
  e.g. "/" → "home", "/test-suites" → "test-suites", "/products/p1" → "products-p1".
  NEVER use "generic_page", "auth_page", or any page_type value as a key.
  One entry per URL. Duplicate keys will break JSON parsing.
- form_data fields MUST use real field names from the graph's form_fields list.
  If no forms exist, set all form_data values to {{}}.
- critical_flows: list 2-4 most important user journeys visible from the graph.
- Keep ALL string values concise (under 120 chars each).
- Output the JSON object only — nothing before or after it.
"""


def _format_already_generated(titles: list[str] | None) -> str:
    """
    Format the 'already generated' section injected into each chunk prompt.
    Returns an empty string if the list is empty or None.
    """
    if not titles:
        return ""
    items = "\n".join(f"  - {t}" for t in titles[:40])  # cap to keep prompt small
    return (
        "=== DO NOT REPEAT — these test titles were ALREADY generated. "
        "Generate DIFFERENT tests with different pages/fields/scenarios ===\n"
        + items + "\n\n"
    )


def _build_testcase_prompt(graph_context: str, knowledge_base: dict,
                            category: str, count: int = TESTCASE_CHUNK_SIZE,
                            extra_context: str = "",
                            known_fields_str: str = "",
                            known_urls_str: str = "",
                            already_generated: list[str] | None = None) -> str:
    kb_str = json.dumps(knowledge_base, separators=(",", ":"))
    if len(kb_str) > 2000:
        kb_str = kb_str[:2000] + "..."

    base_url = knowledge_base.get("base_url", "")

    category_instructions = {
        "user_scenario": (
            f"Generate realistic persona-based user journey tests. "
            f"Think about WHO would visit this site and WHY. For example: "
            f"'A first-time visitor exploring the site', 'A user navigating menus', "
            f"'A returning user going straight to a deep page'. "
            f"Each test navigates through 2-4 real pages in a logical order. "
            f"Assert the final URL and that the landing page has relevant text. "
            f"Use ONLY URLs from the graph provided. Every step must be meaningful."
        ),
        "edge_case": (
            f"Generate edge case / unusual user behaviour tests. Think about: "
            f"1) Navigating directly to a deep URL without visiting the homepage first. "
            f"2) Navigating back and forth rapidly between two pages. "
            f"3) Visiting the same page multiple times in a row. "
            f"4) Starting on a leaf page and navigating to the root. "
            f"5) Loading a page with a query string appended (e.g. ?ref=test) and verifying it still loads. "
            f"Use navigate + assert_url + assert_element_present. ONLY use URLs from the graph."
        ),
        "accessibility": (
            f"Generate accessibility-focused tests for each page. Test: "
            f"1) Page has a visible title (assert_title). "
            f"2) Page body contains actual text (assert_text_contains). "
            f"3) Page has at least one heading — assert_element_present with target 'h1' or 'h2'. "
            f"4) Page has a nav landmark — assert_element_present 'nav'. "
            f"5) Page footer exists — assert_element_present 'footer'. "
            f"Generate one test per page. ONLY use URLs from the graph."
        ),
        "cross_page": (
            f"Generate cross-page consistency tests verifying key UI elements are "
            f"consistent across pages: "
            f"1) Navigation menu appears on every page. "
            f"2) Header/logo appears on every page. "
            f"3) Footer appears on every page. "
            f"4) Page title format is consistent. "
            f"Each test checks the SAME element on 2-3 different pages. "
            f"Use assert_element_present and assert_title. ONLY use URLs from the graph."
        ),
        "error_recovery": (
            f"Generate error recovery tests. Simulate getting into a bad state then recovering: "
            f"1) Navigate to an invalid path (e.g. {base_url}/not-real), "
            f"   then navigate to a valid page and assert it loads. "
            f"2) Use browser_back after navigating to a nested page. "
            f"3) Navigate away mid-journey then return to a known good URL. "
            f"The recovery step MUST be a navigate to a REAL URL from the graph, "
            f"ending with assert_url. ONLY use real graph URLs for the recovery destination."
        ),
        "seo_structure": (
            f"Generate SEO and page structure validation tests. For each page check: "
            f"1) assert_title — page has a non-empty meaningful title. "
            f"2) assert_element_present 'h1' — page has a main heading. "
            f"3) assert_text_contains — page body contains the main keyword from its URL path. "
            f"Generate one test per page. ONLY use URLs from the graph."
        ),
        "form_valid": (
            f"Step 1 MUST be: navigate to the form page URL from the graph. "
            f"Fill fields with valid data from knowledge_base.form_data.valid. "
            f"Final step: submit_form. Assert success confirmation."
        ),
        "form_invalid": (
            f"Step 1 MUST be: navigate to the form page URL from the graph. "
            f"Fill with invalid data from knowledge_base.form_data.invalid. "
            f"Assert validation error message is visible."
        ),
        "form_empty": (
            f"Step 1 MUST be: navigate to the form page URL. "
            f"Submit form WITHOUT filling any fields. "
            f"Assert required-field error or form stays on page."
        ),
        "boundary": (
            f"Step 1 MUST be: navigate to the form page URL. "
            f"Fill boundary values from knowledge_base.form_data.boundary. "
            f"Submit and assert app handles gracefully."
        ),
        "security": (
            f"Step 1 MUST be: navigate to the form page URL. "
            f"Fill XSS/SQL payloads from knowledge_base.form_data.special_chars. "
            f"Submit and assert raw '<script>' is NOT reflected in page body."
        ),
    }

    instruction = category_instructions.get(
        category,
        f"Generate {count} exploratory test cases using only URLs from the graph."
    )

    _nav_only_cats = {"form_valid", "form_invalid", "form_empty", "boundary", "security"}
    graph_section  = (
        "" if category in _nav_only_cats
        else f"\n=== TRANSITION GRAPH CONTEXT ===\n{graph_context}\n"
    )

    return f"""You are a senior QA automation engineer. Generate exactly {count} test cases for category: **{category}**

Site under test: {base_url}

Category instruction:
{instruction}

{extra_context}

=== CRITICAL RULES — READ CAREFULLY ===
1. EVERY test MUST have a 'navigate' as the first action to a real URL
2. ONLY use URLs from this EXACT list (no others, no invented URLs):
{known_urls_str or '(see transition graph below)'}
3. ONLY use these EXACT field names as fill_field targets (do NOT invent names):
{known_fields_str or '(see knowledge base form_data)'}
4. priority must be exactly one of: high | medium | low
5. steps[] must be plain English strings (human-readable description of each step)
6. actions[] must be the machine-executable equivalent of those steps
7. source_transitions: list real transition IDs (T0001...) or leave as []

=== ALLOWED ACTIONS (for the actions[] array) ===
navigate, click_link, browser_back, fill_field, submit_form,
assert_url, assert_title, assert_element_present, assert_element_not_present, assert_text_contains

=== KNOWLEDGE BASE ===
{kb_str}

{graph_section}{_format_already_generated(already_generated)}
OUTPUT: raw JSON array of exactly {count} objects. No prose, no markdown fences.

Each object MUST follow this EXACT schema:
{{
  "test_id": "TC_{category.upper()}_<NNN>",
  "category": "{category}",
  "priority": "high | medium | low",
  "title": "<one-line human-readable test title>",
  "precondition": "<single string, e.g. 'User is on the home page'>",
  "steps": [
    "<Step 1: human-readable description of what to do>",
    "<Step 2: human-readable description>",
    "<Step N: verify the expected outcome>"
  ],
  "test_data": {{
    "locator": {{
      "href": "<destination URL or empty string>",
      "text": "<link or button label or empty string>",
      "id": "<element id if known, else omit this key>",
      "data_testid": "<data-testid value if known, else omit>"
    }},
    "input_values": {{
      "<field_name>": "<value to enter, or empty string to leave blank>"
    }}
  }},
  "expected_result": "<specific verifiable outcome>",
  "from_state": "<full starting page URL>",
  "to_state": "<full ending page URL (same as from_state if page does not change)>",
  "actions": [
    {{"seq": 1, "action": "navigate",  "target": "<real URL from the allowed list>", "value": "", "expected": "Page loaded"}},
    {{"seq": 2, "action": "<action>",  "target": "<CSS selector, URL, or element text>", "value": "<value if needed>", "expected": "<what to verify>"}}
  ],
  "source_transitions": ["T0001"]
}}
"""


# ──────────────────────────────────────────────────────────────────────────────
# JSON repair helpers
# ──────────────────────────────────────────────────────────────────────────────

def _strip_fences(raw: str) -> str:
    """Remove markdown code fences and leading/trailing whitespace."""
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw)
    return raw.strip()


def _strip_reasoning_tags(raw: str) -> str:
    """Strip <think>...</think> blocks emitted by some reasoning models."""
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    return raw.strip()


def _find_json_structure(raw: str) -> str:
    """
    FIX #3: Replace the original greedy regex approach with a brace/bracket
    counting scanner.

    The original code used:
        re.search(r"({.*})", raw, re.DOTALL)    # greedy — works for simple objects
                                                # but breaks on nested structures
        re.search(r"(\\[.*\\])", raw, re.DOTALL) # and fails when LLM emits
                                                # prose after the closing brace

    The scanner finds the FIRST '[' or '{', then walks forward counting
    open/close pairs (handling escaped quotes inside strings) until the
    matching closer is found.  This correctly handles:
      - nested objects and arrays
      - braces/brackets inside string values
      - prose text before and after the JSON
    """
    raw = raw.strip()

    # Find the first JSON starter character
    first_bracket = raw.find("[")
    first_brace   = raw.find("{")

    # Determine which comes first
    if first_bracket == -1 and first_brace == -1:
        return raw   # no JSON found — return as-is and let json.loads fail with a clear error

    if first_bracket == -1:
        start_pos = first_brace
        open_ch, close_ch = "{", "}"
    elif first_brace == -1:
        start_pos = first_bracket
        open_ch, close_ch = "[", "]"
    else:
        # Whichever comes first in the string
        if first_bracket < first_brace:
            start_pos = first_bracket
            open_ch, close_ch = "[", "]"
        else:
            start_pos = first_brace
            open_ch, close_ch = "{", "}"

    # Walk forward with a depth counter, honouring quoted strings
    depth  = 0
    in_str = False
    escape = False
    i      = start_pos

    while i < len(raw):
        ch = raw[i]
        if escape:
            escape = False
        elif ch == "\\" and in_str:
            escape = True
        elif ch == '"':
            in_str = not in_str
        elif not in_str:
            if ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    return raw[start_pos: i + 1]
        i += 1

    # Reached end of string without closing — return from start_pos to end
    # so that the truncation repair can attempt a fix
    return raw[start_pos:]


def _repair_truncated_array(raw: str) -> str:
    """
    If the JSON array was truncated mid-stream, close it gracefully so at
    least the completed items are recoverable.
    """
    raw = raw.rstrip()
    if raw.startswith("[") and not raw.endswith("]"):
        last_close = raw.rfind("}")
        if last_close != -1:
            raw = raw[: last_close + 1] + "\n]"
        else:
            raw = "[]"
    return raw


def _repair_truncated_object(raw: str) -> str:
    """
    FIX #2: Mirror of _repair_truncated_array for JSON objects.

    The knowledge base is a JSON object ({...}).  If the LLM response was
    cut off mid-object (finish_reason='length'), the original code only tried
    to repair arrays and then raised ValueError.

    Strategy:
      1. Find the last complete key-value pair (last '"key": <value>' ending
         with either '}', ']', '"', a number, true, false, or null).
      2. Close any open arrays/objects by counting unclosed openers.
      3. Append the necessary closers.
    """
    raw = raw.rstrip().rstrip(",")   # strip trailing comma left by truncation

    if not raw.startswith("{"):
        return raw

    if raw.endswith("}"):
        return raw   # already looks complete

    # Count unmatched openers to determine what closers to append
    depth_obj = 0
    depth_arr = 0
    in_str    = False
    escape    = False

    for ch in raw:
        if escape:
            escape = False
            continue
        if ch == "\\" and in_str:
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if   ch == "{": depth_obj += 1
        elif ch == "}": depth_obj -= 1
        elif ch == "[": depth_arr += 1
        elif ch == "]": depth_arr -= 1

    # Append closers in reverse nesting order
    closers = "]" * max(depth_arr, 0) + "}" * max(depth_obj, 0)
    if closers:
        repaired = raw + "\n" + closers
        log.warning(
            f"KB JSON was truncated — appended '{closers}' to repair. "
            f"Some fields may be missing."
        )
        return repaired
    return raw


def _extract_json(raw: str):
    """
    FIX #5: Multi-stage JSON extraction with repair fallbacks for BOTH
    arrays (test cases) and objects (knowledge base).

    Stage order:
      1. Strip reasoning tags + markdown fences
      2. Direct parse
      3. Extract first balanced JSON structure (brace-counter, not regex)
      4. Parse extracted structure
      5a. If it's an array — repair truncated array, parse again
      5b. If it's an object — repair truncated object, parse again
      6. Raise ValueError with a useful diagnostic message
    """
    if not raw or not raw.strip():
        raise ValueError("LLM returned an empty response.")

    # Stage 1: clean
    cleaned = _strip_reasoning_tags(raw)
    cleaned = _strip_fences(cleaned)

    # Stage 2: direct parse (works when LLM is well-behaved)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Stage 3: extract first balanced JSON structure
    extracted = _find_json_structure(cleaned)

    # Stage 4: parse extracted structure
    try:
        return json.loads(extracted)
    except json.JSONDecodeError:
        pass

    # Stage 5a: truncated array repair
    if extracted.lstrip().startswith("["):
        repaired = _repair_truncated_array(extracted)
        try:
            result = json.loads(repaired)
            log.warning("JSON array was truncated — recovered partial result.")
            return result
        except json.JSONDecodeError:
            pass

    # Stage 5b: truncated object repair (NEW — fixes the KB crash)
    if extracted.lstrip().startswith("{"):
        repaired = _repair_truncated_object(extracted)
        try:
            result = json.loads(repaired)
            log.warning("JSON object was truncated — recovered partial KB.")
            return result
        except json.JSONDecodeError:
            pass

    raise ValueError(
        f"Could not parse LLM response as JSON after all repair attempts.\n"
        f"Raw snippet (first 500 chars): {raw[:500]}"
    )


def _unwrap_test_cases(parsed) -> list[dict]:
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        for key in ("test_cases", "testCases", "tests", "items", "results"):
            if key in parsed and isinstance(parsed[key], list):
                log.info(f"Unwrapped LLM response from key '{key}'")
                return parsed[key]
        list_values = [(k, v) for k, v in parsed.items() if isinstance(v, list)]
        if len(list_values) == 1:
            key, lst = list_values[0]
            log.info(f"Unwrapped LLM response from sole list key '{key}'")
            return lst
    raise ValueError(
        f"LLM did not return a JSON array. "
        f"Got type={type(parsed).__name__}. Snippet: {str(parsed)[:300]}"
    )


# ──────────────────────────────────────────────────────────────────────────────
# Core LLM caller — Groq with key rotation on 429
# ──────────────────────────────────────────────────────────────────────────────

# ── Proactive token-bucket pacing ───────────────────────────────────────────
# The free Groq tier enforces 6,000 tokens/min org-wide (ALL keys share this).
# Instead of firing immediately → hitting 429 → waiting 48s (reactive),
# we track spending in a rolling 60-second window and sleep proactively
# BEFORE the call so we never exceed the budget.
# Set GROQ_TPM_LIMIT in .env to override (e.g. 30000 for Dev tier).

_TPM_LIMIT        = int(os.getenv("GROQ_TPM_LIMIT", "6000"))
_tpm_window_start = 0.0
_tpm_tokens_used  = 0


def _tpm_pace(estimated_tokens: int) -> None:
    """Sleep if necessary to stay under _TPM_LIMIT tokens/minute."""
    global _tpm_window_start, _tpm_tokens_used
    now = time.monotonic()
    window_age = now - _tpm_window_start
    if window_age >= 60.0:
        _tpm_window_start = now
        _tpm_tokens_used  = 0
    remaining = _TPM_LIMIT - _tpm_tokens_used
    if estimated_tokens > remaining:
        wait = max(60.0 - window_age + 1.0, 1.0)
        log.info(
            f"[TPM pace] {_tpm_tokens_used}/{_TPM_LIMIT} tokens used. "
            f"Need {estimated_tokens} more — sleeping {wait:.1f}s for window reset."
        )
        time.sleep(wait)
        _tpm_window_start = time.monotonic()
        _tpm_tokens_used  = 0
    _tpm_tokens_used += estimated_tokens


def _parse_retry_after(err: str) -> float:
    """Extract wait seconds from Groq 429 error message."""
    import re as _re
    m = _re.search(r"try again in ([\d.]+)s", err)
    if m:
        return min(float(m.group(1)) + 0.5, 120)
    m = _re.search(r"try again in ([\d.]+)ms", err)
    if m:
        return min(float(m.group(1)) / 1000 + 0.5, 120)
    return 10.0


def _is_tpm_limit(err: str) -> bool:
    """
    True when this 429 is a TPM (tokens-per-minute) limit.
    TPM is ORGANISATION-WIDE — rotating to another key in the same org
    hits the exact same counter, so the only fix is to wait.
    RPM (requests-per-minute) IS per-key, so rotation helps there.
    """
    e = err.lower()
    return (
        "tokens per minute" in e
        or "tpm" in e
        or "'type': 'tokens'" in err
        or '"type": "tokens"' in err
    )


def _call_llm(model: str, prompt: str,
              retries: int = 8, delay: float = 6.0,
              max_tokens: int = 4000,
              temperature: float = 0.1) -> str:
    """
    Call Groq and return raw text.

    Rate-limit strategy
    ───────────────────
    Groq enforces two independent quotas:

    TPM (tokens/min) — ORGANISATION-WIDE across all your keys.
        Rotating keys is useless. Wait for the Retry-After period, then retry.

    RPM (requests/min) — PER API KEY.
        Rotate immediately to the next key; no sleep needed.

    The error payload identifies which: 'type': 'tokens'  vs  'type': 'requests'
    """
    estimated_tokens = len(prompt) // 4 + max_tokens
    _tpm_pace(estimated_tokens)

    models_to_try = [model]
    if FALLBACK_MODEL and FALLBACK_MODEL != model:
        models_to_try.append(FALLBACK_MODEL)

    last_exc: Exception | None = None

    for current_model in models_to_try:
        rpm_exhausted: set[int] = set()

        for attempt in range(1, retries + 1):
            try:
                client = _make_client()
                log.info(
                    f"LLM call attempt {attempt}/{retries} — "
                    f"model={current_model}, key=#{_key_index + 1}/{len(_ALL_KEYS)}"
                )

                response = client.chat.completions.create(
                    model=current_model,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are a senior QA engineer specialising in Selenium test automation. "
                                "Output ONLY valid JSON — no explanations, "
                                "no markdown fences, no reasoning text."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    temperature=temperature,
                    max_tokens=max_tokens,
                )

                resp_content = response.choices[0].message.content

                if not resp_content or not resp_content.strip():
                    finish_reason = response.choices[0].finish_reason
                    log.warning(
                        f"Empty response from model (attempt {attempt}). "
                        f"finish_reason={finish_reason}. Retrying..."
                    )
                    last_exc = ValueError(f"Empty response. finish_reason={finish_reason}")
                    time.sleep(delay)
                    continue

                log.info(
                    f"Response received — {len(resp_content)} chars, "
                    f"finish_reason={response.choices[0].finish_reason}"
                )
                global _tpm_tokens_used
                usage = getattr(response, "usage", None)
                if usage:
                    actual = getattr(usage, "total_tokens", 0) or 0
                    if actual:
                        _tpm_tokens_used += max(0, actual - estimated_tokens)
                return resp_content.strip()

            except Exception as e:
                err = str(e)
                last_exc = e

                if "429" in err or "rate_limit" in err.lower():
                    wait = _parse_retry_after(err)

                    if _is_tpm_limit(err):
                        log.warning(
                            f"TPM 429 (org-wide). Waiting {wait:.1f}s for bucket reset..."
                        )
                        time.sleep(wait)
                        global _tpm_window_start
                        _tpm_window_start = time.monotonic()
                        _tpm_tokens_used  = 0
                        rpm_exhausted.clear()
                        continue

                    else:
                        # Per-key RPM: rotate immediately, sleep only if all keys exhausted
                        rpm_exhausted.add(_key_index)
                        log.warning(
                            f"RPM limit on key #{_key_index + 1} "
                            f"({len(rpm_exhausted)}/{len(_ALL_KEYS)} keys exhausted)."
                        )
                        rotated = False
                        for _ in range(len(_ALL_KEYS) - 1):
                            if _rotate_key() and _key_index not in rpm_exhausted:
                                log.info(f"  → Switched to key #{_key_index + 1} immediately")
                                rotated = True
                                break
                        if rotated:
                            continue
                        log.warning(f"All keys RPM-limited. Waiting {wait:.1f}s...")
                        time.sleep(wait)
                        rpm_exhausted.clear()
                        continue

                log.warning(f"LLM failed (attempt {attempt}, model={current_model}): {e}")
                if attempt < retries:
                    time.sleep(delay)

        log.warning(
            f"All {retries} retries exhausted for model={current_model}. "
            f"{'Trying fallback...' if current_model != models_to_try[-1] else 'No more fallbacks.'}"
        )

    raise last_exc or RuntimeError("LLM call failed with no exception captured.")


# ──────────────────────────────────────────────────────────────────────────────
# KB fallback skeleton — used when parsing partially fails
# ──────────────────────────────────────────────────────────────────────────────

def _minimal_kb_skeleton(graph_context: str) -> dict:
    """
    FIX #6: If even the repaired KB JSON can't be parsed (extremely rare),
    return a safe minimal skeleton derived from the graph_context string so
    that Phase 2 can still proceed.  This is a last-resort fallback — it means
    test quality will be lower, but the pipeline won't crash.
    """
    # Try to extract base_url from the graph context string
    url_match = re.search(r"https?://[^\s\"']+", graph_context)
    base_url  = url_match.group(0).rstrip("/") if url_match else ""

    log.warning(
        "Falling back to minimal KB skeleton.  "
        "Form data and critical flows will be empty."
    )
    return {
        "site_name":               base_url,
        "base_url":                base_url,
        "site_description":        "Web application under test.",
        "pages":                   {},
        "form_data": {
            "valid":         {},
            "invalid":       {},
            "boundary":      {},
            "special_chars": {},
        },
        "critical_flows":          [],
        "negative_test_scenarios": [],
    }


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def generate_knowledge_base(graph_context: str,
                             model: str = DEFAULT_MODEL) -> dict:
    """
    Phase 1: Generate site knowledge base from transition graph.

    FIX #6: Added post-parse validation.  If the parsed object is missing
    required keys (which can happen after truncation repair fills in only
    partial data), the missing keys are populated with safe empty defaults
    rather than letting downstream code crash with a KeyError.
    """
    prompt = _build_kb_prompt(graph_context)
    log.info("Phase 1 — Generating knowledge base...")

    raw = _call_llm(model, prompt, max_tokens=KB_MAX_TOKENS)

    try:
        kb = _extract_json(raw)
    except ValueError as exc:
        log.error(f"KB JSON parsing failed entirely: {exc}")
        kb = _minimal_kb_skeleton(graph_context)

    if not isinstance(kb, dict):
        log.error(
            f"LLM returned a {type(kb).__name__} instead of a dict for the KB — "
            f"using minimal skeleton."
        )
        kb = _minimal_kb_skeleton(graph_context)

    # Ensure all required top-level keys exist with safe defaults
    _KB_DEFAULTS: dict = {
        "site_name":               "",
        "base_url":                "",
        "site_description":        "",
        "pages":                   {},
        "form_data": {
            "valid":         {},
            "invalid":       {},
            "boundary":      {},
            "special_chars": {},
        },
        "critical_flows":          [],
        "negative_test_scenarios": [],
    }
    for key, default in _KB_DEFAULTS.items():
        if key not in kb:
            log.warning(f"KB missing key '{key}' — using default.")
            kb[key] = default

    # Ensure form_data sub-keys exist
    for subkey in ("valid", "invalid", "boundary", "special_chars"):
        if subkey not in kb.get("form_data", {}):
            kb.setdefault("form_data", {})[subkey] = {}

    log.info("Knowledge base generated successfully.")
    return kb


def generate_test_cases(graph_context: str,
                       knowledge_base: dict,
                       model: str = DEFAULT_MODEL,
                       graph_facts: dict | None = None) -> list[dict]:
    """
    Phase 2b: LLM-generated creative test cases.
    Parallelized across categories for faster execution.
    """

    from concurrent.futures import ThreadPoolExecutor, as_completed

    has_forms   = (graph_facts or {}).get("has_forms", True)
    states      = (graph_facts or {}).get("states", [])
    transitions = (graph_facts or {}).get("transitions", [])
    base_url    = knowledge_base.get("base_url", "")

    state_lines = [
        f"  - {s['url']}  title: {s.get('title', '')!r}"
        for s in states
        if s.get("page_type") != "external_boundary" and s.get("url")
    ]
    n_states   = len(state_lines)
    states_ctx = "=== SITE PAGES (ONLY use these URLs) ===\n" + "\n".join(state_lines)

    tr_lines = []
    for t in transitions[:40]:
        a = t.get("action", {})
        tr_lines.append(
            f"  {t['id']}: {a.get('text', '')!r} -> {state_map_url(states, t.get('to', ''))}"
        )
    transition_ctx = "=== TRANSITIONS (sample) ===\n" + "\n".join(tr_lines)
    combined_ctx   = f"{states_ctx}\n\n{transition_ctx}"

    plan: list[tuple[str, int, str]] = [
        ("user_scenario",  TESTS_PER_CATEGORY,               combined_ctx),
        ("edge_case",      TESTS_PER_CATEGORY,               combined_ctx),
        ("accessibility",  max(TESTS_PER_CATEGORY, n_states), states_ctx),
        ("cross_page",     TESTS_PER_CATEGORY,               states_ctx),
        ("error_recovery", TESTS_PER_CATEGORY,               combined_ctx),
        ("seo_structure",  max(TESTS_PER_CATEGORY, n_states), states_ctx),
    ]

    if has_forms:
        log.info("Site has forms — adding form/boundary/security categories.")
        form_ctx = "\n".join([
            f"  - {s['url']}  fields: {[f['name'] for f in s.get('form_fields', [])]}"
            for s in states if s.get("form_fields")
        ])
        plan += [
            ("form_valid",   TESTS_PER_CATEGORY, f"Form pages:\n{form_ctx}"),
            ("form_invalid", TESTS_PER_CATEGORY, f"Form pages:\n{form_ctx}"),
            ("form_empty",   TESTS_PER_CATEGORY, f"Form pages:\n{form_ctx}"),
            ("boundary",     TESTS_PER_CATEGORY, f"Form pages:\n{form_ctx}"),
            ("security",     TESTS_PER_CATEGORY, f"Form pages:\n{form_ctx}"),
        ]
    else:
        log.info("Site has no forms — skipping form/boundary/security categories.")

    known_urls_list = [
        s["url"] for s in states
        if s.get("url") and s.get("page_type") != "external_boundary"
    ]
    known_urls_str = "\n".join(f"  {u}" for u in known_urls_list)

    known_fields_set: set[str] = set()
    for s in states:
        for ff in s.get("form_fields", []):
            for key in ("name", "id"):
                val = (ff.get(key) or "").strip()
                if val:
                    known_fields_set.add(val)

    known_fields_str = ", ".join(sorted(known_fields_set)) if known_fields_set else ""

    id_counter = 1
    all_test_cases: list[dict] = []
    failed_categories: list[str] = []


    # ===========================
    # Parallel category generator
    # ===========================

    def _generate_category(category_tuple):
        nonlocal id_counter

        category, total_count, extra_context = category_tuple

        n_chunks = math.ceil(total_count / TESTCASE_CHUNK_SIZE)
        cat_tests: list[dict] = []
        generated_titles: list[str] = []

        log.info(
            f"Phase 2b — '{category}': {total_count} tests in {n_chunks} chunk(s)..."
        )

        for chunk_idx in range(n_chunks):

            chunk_size = min(
                TESTCASE_CHUNK_SIZE,
                total_count - chunk_idx * TESTCASE_CHUNK_SIZE
            )

            if chunk_size <= 0:
                break

            chunk_temp = min(0.1 + chunk_idx * 0.1, 0.4)

            prompt = _build_testcase_prompt(
                graph_context,
                knowledge_base,
                category=category,
                count=chunk_size,
                extra_context=extra_context,
                known_fields_str=known_fields_str,
                known_urls_str=known_urls_str,
                already_generated=generated_titles if generated_titles else None,
            )

            try:

                raw = _call_llm(
                    model,
                    prompt,
                    max_tokens=TESTCASE_MAX_TOKENS,
                    temperature=chunk_temp
                )

                parsed = _extract_json(raw)
                cases  = _unwrap_test_cases(parsed)

                for tc in cases:

                    if not tc.get("test_id"):
                        tc["test_id"] = f"TC_{category.upper()}_{id_counter:03d}"

                    id_counter += 1

                    title = (
                        tc.get("title")
                        or tc.get("description")
                        or ""
                    ).strip()

                    if title:
                        generated_titles.append(title)

                cat_tests.extend(cases)

                log.info(
                    f"  {category} chunk {chunk_idx+1}/{n_chunks}: "
                    f"{len(cases)} tests (temp={chunk_temp:.1f})"
                )

            except Exception as e:

                log.error(
                    f"{category} chunk {chunk_idx+1}/{n_chunks} FAILED: {e}"
                )

                if chunk_idx == 0:
                    failed_categories.append(category)

                break

        log.info(f"'{category}' total: {len(cat_tests)} tests generated.")

        return cat_tests


    # ===========================
    # Run categories in parallel
    # ===========================

    with ThreadPoolExecutor(max_workers=3) as executor:

        futures = [
            executor.submit(_generate_category, item)
            for item in plan
        ]

        for future in as_completed(futures):

            try:
                all_test_cases.extend(future.result())

            except Exception as e:

                log.error(f"Category generation failed: {e}")


    if failed_categories:
        log.warning(f"Failed categories: {failed_categories}")

    log.info(f"Phase 2b total LLM test cases: {len(all_test_cases)}")

    return all_test_cases


def state_map_url(states: list[dict], state_id: str) -> str:
    """Quick helper: resolve state_id -> URL."""
    for s in states:
        if s.get("state_id") == state_id:
            return s.get("url", state_id)
    return state_id