"""
deterministic_generator.py — Generate comprehensive exploratory test cases
directly from the transition graph. Fully site-agnostic — works for any website.

Everything is derived purely from graph data (states, transitions, URLs, link texts).
No site-specific hardcoding.

Test categories:
  1.  click_navigation     - click real nav links by link text
  2.  page_load            - navigate + assert URL + assert title per state
  3.  back_navigation      - navigate forward then browser Back
  4.  element_presence     - verify core UI elements exist on every page
  5.  content_validation   - verify URL-derived keywords in page body
  6.  negative_url         - standard bad paths, verify graceful handling
  7.  logo_click           - click the home/brand link from every inner page
  8.  multi_step_nav       - BFS 3-hop user journeys through graph
  9.  link_validity        - each unique from->to pair returns a live page
  10. utility_link         - links that appear on many pages (nav/header links)
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse
from collections import defaultdict, deque

# ── Generic Config (no site-specific values) ──────────────────────────────────

# These paths are standard attack/probe paths on ANY web application
NEGATIVE_PATHS = [
    "/this-page-does-not-exist",
    "/xyz123abc",
    "/admin",
    "/wp-admin",
    "/login",
    "/.env",
    "/config.php",
    "/api/v1/users",
    "/dashboard",
    "/phpinfo.php",
    "/%3Cscript%3Ealert(1)%3C%2Fscript%3E",
    "/../../etc/passwd",
    "/null",
    "/undefined",
    "/robots.txt",
    "/#/../admin",
]

# Generic UI elements expected on any website
COMMON_ELEMENTS = [
    {"selector": "nav",    "description": "navigation bar"},
    {"selector": "header", "description": "page header"},
    {"selector": "footer", "description": "page footer"},
    {"selector": "body",   "description": "page body"},
    {"selector": "a",      "description": "at least one hyperlink"},
]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _page_label(url: str) -> str:
    """Human-readable label from URL path."""
    path = urlparse(url).path.strip("/")
    return path.replace("/", " > ") if path else "home"


def _priority(idx: int) -> str:
    return ["high", "medium", "low"][idx % 3]


def _clean_text(text: str) -> str:
    """
    Normalise link text: take the first meaningful short ASCII-ish line.
    Handles multi-line text and non-Latin characters (e.g. Malayalam, Chinese).
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for line in lines:
        # Prefer lines that are mostly ASCII / Latin (Unicode < 1024)
        ascii_ratio = sum(1 for c in line if ord(c) < 1024) / max(len(line), 1)
        if ascii_ratio > 0.7 and 2 <= len(line) <= 80:
            return line
    return lines[0][:80] if lines else ""


def _is_external(href: str, base_domain: str) -> bool:
    try:
        netloc = urlparse(href).netloc
        return bool(netloc) and base_domain not in netloc
    except Exception:
        return False


def _keywords_from_url(url: str) -> list[str]:
    """
    Derive expected page keywords purely from the URL path segments.
    E.g. /department/cse/programs -> ['department', 'cse', 'programs']
    Works for any site without hardcoding.
    """
    path = urlparse(url).path.strip("/")
    segments = [s for s in path.split("/") if s and not s.startswith("_")]
    # Deduplicate while preserving order
    seen: set = set()
    unique = []
    for s in segments:
        key = s.lower()
        if key not in seen:
            seen.add(key)
            unique.append(s)
    return unique[:4]   # limit to 4 keywords per page


def _home_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/"


# ── Graph analysis helpers ─────────────────────────────────────────────────────

def _build_state_map(states: list[dict]) -> dict[str, dict]:
    return {s["state_id"]: s for s in states}


def _build_adjacency(transitions: list[dict]) -> dict[str, list[tuple]]:
    """state_id -> [(to_state_id, transition_id, action_dict)]"""
    adj: dict[str, list] = defaultdict(list)
    for tr in transitions:
        f = tr.get("from", "")
        t = tr.get("to", "")
        if f and t and f != t:
            adj[f].append((t, tr.get("id", ""), tr.get("action", {})))
    return adj


def _find_home_state(states: list[dict], base_url: str) -> str | None:
    """Return the state_id of the root/home page."""
    home = _home_url(base_url)
    for s in states:
        url = s.get("url", "").rstrip("/") + "/"
        if url == home:
            return s["state_id"]
    return None


def _detect_nav_links(transitions: list[dict],
                      state_map: dict,
                      base_domain: str,
                      min_appearances: int = 3) -> list[dict]:
    """
    Detect navigation/header links by frequency.
    A link that appears from >= min_appearances different source pages is
    considered a global nav link (appears in the header/nav bar of the site).
    Returns list of {link_text, href, to_url} dicts, deduplicated.
    """
    # Count how many distinct FROM states each (link_text, href) pair appears on
    link_appearances: dict[tuple, set] = defaultdict(set)
    link_meta: dict[tuple, dict]       = {}

    for tr in transitions:
        action    = tr.get("action", {})
        raw_text  = action.get("text", "")
        href      = action.get("href", "")
        link_text = _clean_text(raw_text)
        from_sid  = tr.get("from", "")
        to_sid    = tr.get("to", "")

        if not link_text or _is_external(href, base_domain):
            continue

        key = (link_text, href)
        link_appearances[key].add(from_sid)
        if key not in link_meta:
            link_meta[key] = {
                "link_text": link_text,
                "href":      href,
                "to_url":    state_map.get(to_sid, {}).get("url", href),
            }

    nav_links = []
    for key, from_states in link_appearances.items():
        if len(from_states) >= min_appearances:
            nav_links.append(link_meta[key])

    return nav_links


# ── 1. Click Navigation Tests ──────────────────────────────────────────────────

def generate_click_navigation_tests(transitions: list[dict],
                                     state_map: dict,
                                     base_domain: str,
                                     id_start: int = 1) -> list[dict]:
    """
    One test per unique (source-page, link-text) pair in the graph.
    Tests actually click the link by its visible text — real user simulation.
    """
    tests   = []
    counter = id_start
    seen: set = set()

    for i, tr in enumerate(transitions):
        action    = tr.get("action", {})
        raw_text  = action.get("text", "")
        href      = action.get("href", "")
        from_sid  = tr.get("from", "")
        to_sid    = tr.get("to", "")

        link_text = _clean_text(raw_text)
        if not link_text or len(link_text) < 2:
            continue
        if _is_external(href, base_domain):
            continue

        from_url  = state_map.get(from_sid, {}).get("url", "")
        to_url    = state_map.get(to_sid,   {}).get("url", "")
        to_title  = state_map.get(to_sid,   {}).get("title", "")
        if not from_url or not to_url:
            continue

        key = (from_url, link_text)
        if key in seen:
            continue
        seen.add(key)

        to_path = urlparse(to_url).path or "/"

        tests.append({
            "id":          f"TC_CLICK_NAV_{counter:03d}",
            "category":    "click_navigation",
            "priority":    _priority(i),
            "description": f"Click '{link_text}' link on '{_page_label(from_url)}' page",
            "preconditions": ["Browser is open"],
            "steps": [
                {"seq": 1, "action": "navigate",    "target": from_url, "value": "",          "expected": "Source page loaded"},
                {"seq": 2, "action": "click_link",  "target": to_url,   "value": link_text,   "expected": f"'{link_text}' link is visible and clicked"},
                {"seq": 3, "action": "assert_url",  "target": to_url,   "value": "",          "expected": f"URL contains '{to_path}'"},
                {"seq": 4, "action": "assert_title","target": to_title, "value": "",          "expected": f"Page title is '{to_title}'"},
            ],
            "expected_result": f"Clicking '{link_text}' navigates to {to_url}",
            "source_transitions": [tr.get("id", "")],
        })
        counter += 1

    return tests


# ── 2. Page Load Tests ─────────────────────────────────────────────────────────

def generate_page_load_tests(states: list[dict], id_start: int = 1) -> list[dict]:
    tests   = []
    counter = id_start
    for i, state in enumerate(states):
        url   = state.get("url", "")
        title = state.get("title", "")
        if not url:
            continue
        path = urlparse(url).path or "/"
        tests.append({
            "id":          f"TC_PAGE_LOAD_{counter:03d}",
            "category":    "page_load",
            "priority":    _priority(i),
            "description": f"Verify '{_page_label(url)}' page loads with correct title",
            "preconditions": ["Browser is open"],
            "steps": [
                {"seq": 1, "action": "navigate",     "target": url,   "value": "", "expected": "Page loaded"},
                {"seq": 2, "action": "assert_url",   "target": url,   "value": "", "expected": f"URL contains '{path}'"},
                {"seq": 3, "action": "assert_title", "target": title, "value": "", "expected": f"Page title is '{title}'"},
            ],
            "expected_result": f"Page at {url} loads with title '{title}'",
            "source_transitions": [state.get("state_id", "")],
        })
        counter += 1
    return tests


# ── 3. Back Navigation Tests ───────────────────────────────────────────────────

def generate_back_navigation_tests(transitions: list[dict],
                                    state_map: dict,
                                    id_start: int = 1,
                                    max_tests: int = 20) -> list[dict]:
    tests   = []
    counter = id_start
    seen: set = set()

    for i, tr in enumerate(transitions):
        if len(tests) >= max_tests:
            break
        from_sid = tr.get("from", "")
        to_sid   = tr.get("to", "")
        from_url = state_map.get(from_sid, {}).get("url", "")
        to_url   = state_map.get(to_sid,   {}).get("url", "")
        if not from_url or not to_url or from_url == to_url:
            continue
        key = (from_url, to_url)
        if key in seen:
            continue
        seen.add(key)

        from_path = urlparse(from_url).path or "/"
        tests.append({
            "id":          f"TC_BACK_NAV_{counter:03d}",
            "category":    "back_navigation",
            "priority":    _priority(i),
            "description": f"Navigate to '{_page_label(to_url)}' then press browser Back",
            "preconditions": ["Browser is open"],
            "steps": [
                {"seq": 1, "action": "navigate",    "target": from_url, "value": "", "expected": "Starting page loaded"},
                {"seq": 2, "action": "navigate",    "target": to_url,   "value": "", "expected": "Target page loaded"},
                {"seq": 3, "action": "browser_back","target": "",       "value": "", "expected": "Browser navigates back"},
                {"seq": 4, "action": "assert_url",  "target": from_url, "value": "", "expected": f"URL returns to '{from_path}'"},
            ],
            "expected_result": f"Browser Back returns to {from_url}",
            "source_transitions": [tr.get("id", "")],
        })
        counter += 1
    return tests


# ── 4. Element Presence Tests ──────────────────────────────────────────────────

def generate_element_presence_tests(states: list[dict], id_start: int = 1) -> list[dict]:
    tests   = []
    counter = id_start
    for i, state in enumerate(states):
        url = state.get("url", "")
        if not url:
            continue
        for j, elem in enumerate(COMMON_ELEMENTS):
            tests.append({
                "id":          f"TC_ELEM_PRESENT_{counter:03d}",
                "category":    "element_presence",
                "priority":    _priority(i + j),
                "description": f"Verify {elem['description']} exists on '{_page_label(url)}'",
                "preconditions": ["Browser is open"],
                "steps": [
                    {"seq": 1, "action": "navigate",               "target": url,             "value": "", "expected": "Page loaded"},
                    {"seq": 2, "action": "assert_element_present", "target": elem["selector"],"value": "", "expected": f"'{elem['selector']}' present"},
                ],
                "expected_result": f"'{elem['description']}' is present on {url}",
                "source_transitions": [state.get("state_id", "")],
            })
            counter += 1
    return tests


# ── 5. Content Validation Tests ────────────────────────────────────────────────

def generate_content_validation_tests(states: list[dict], id_start: int = 1) -> list[dict]:
    """
    Derive keywords from URL path segments — no hardcoding.
    /department/cse/programs -> checks 'department', 'cse', 'programs' on the page.
    """
    tests   = []
    counter = id_start
    for i, state in enumerate(states):
        url = state.get("url", "")
        if not url:
            continue
        keywords = _keywords_from_url(url)
        if not keywords:
            # Fallback: use domain name components
            domain = urlparse(url).netloc.split(".")[0]
            keywords = [domain] if domain else ["content"]
        for j, keyword in enumerate(keywords[:3]):
            tests.append({
                "id":          f"TC_CONTENT_{counter:03d}",
                "category":    "content_validation",
                "priority":    _priority(i + j),
                "description": f"Verify '{_page_label(url)}' page body contains '{keyword}'",
                "preconditions": ["Browser is open"],
                "steps": [
                    {"seq": 1, "action": "navigate",            "target": url,   "value": "",      "expected": "Page loaded"},
                    {"seq": 2, "action": "assert_text_contains","target": "body","value": keyword, "expected": f"Body contains '{keyword}'"},
                ],
                "expected_result": f"Page body at {url} contains the term '{keyword}'",
                "source_transitions": [state.get("state_id", "")],
            })
            counter += 1
    return tests


# ── 6. Negative URL Tests ──────────────────────────────────────────────────────

def generate_negative_url_tests(base_url: str, id_start: int = 1) -> list[dict]:
    """Standard negative paths for any web application."""
    base  = base_url.rstrip("/")
    tests = []
    for i, path in enumerate(NEGATIVE_PATHS):
        bad_url = base + path
        domain  = urlparse(base_url).netloc
        tests.append({
            "id":          f"TC_NEGATIVE_URL_{id_start + i:03d}",
            "category":    "negative_url",
            "priority":    _priority(i),
            "description": f"Access non-existent path '{path}' — verify graceful error handling",
            "preconditions": ["Browser is open"],
            "steps": [
                {"seq": 1, "action": "navigate",   "target": bad_url, "value": "", "expected": "Page loads even with bad URL"},
                {"seq": 2, "action": "assert_url", "target": domain,  "value": "", "expected": "Browser stays on same domain"},
            ],
            "expected_result": "Site returns error/404 page within domain without exposing server internals",
            "source_transitions": [],
            "_is_negative": True,
        })
    return tests


# ── 7. Logo / Home Link Click Tests ───────────────────────────────────────────

def generate_logo_click_tests(states: list[dict],
                               transitions: list[dict],
                               state_map: dict,
                               base_url: str,
                               id_start: int = 1) -> list[dict]:
    """
    Detect the home/brand link by finding the link that appears most frequently
    going back to the root page — fully derived from the graph, no hardcoding.
    """
    home = _home_url(base_url)
    tests   = []
    counter = id_start

    # Find transitions that lead back to the home page
    home_sid = next(
        (s["state_id"] for s in states
         if s.get("url", "").rstrip("/") + "/" == home),
        None
    )
    if not home_sid:
        return []

    # Count how many times each link text leads to home (most frequent = likely logo)
    logo_text_counts: dict[str, int] = defaultdict(int)
    for tr in transitions:
        if tr.get("to") == home_sid:
            text = _clean_text(tr.get("action", {}).get("text", ""))
            if text:
                logo_text_counts[text] += 1

    # Use the most common link text as the logo link text
    if not logo_text_counts:
        return []
    best_logo_text = max(logo_text_counts, key=lambda k: logo_text_counts[k])

    # Generate one test per non-home page
    seen_from: set = set()
    for i, state in enumerate(states):
        from_url = state.get("url", "")
        if not from_url or from_url.rstrip("/") + "/" == home:
            continue
        if from_url in seen_from:
            continue
        seen_from.add(from_url)

        tests.append({
            "id":          f"TC_LOGO_CLICK_{counter:03d}",
            "category":    "logo_click",
            "priority":    "high",
            "description": f"Click home/logo link on '{_page_label(from_url)}' and verify home page",
            "preconditions": ["Browser is open"],
            "steps": [
                {"seq": 1, "action": "navigate",    "target": from_url,      "value": "",             "expected": "Page loaded"},
                {"seq": 2, "action": "click_link",  "target": home,          "value": best_logo_text, "expected": "Home/logo link clicked"},
                {"seq": 3, "action": "assert_url",  "target": home,          "value": "",             "expected": "URL is home page"},
            ],
            "expected_result": f"Clicking home link from '{_page_label(from_url)}' returns to {home}",
            "source_transitions": [],
        })
        counter += 1

    return tests


# ── 8. Multi-Step Navigation Tests ────────────────────────────────────────────

def generate_multi_step_nav_tests(states: list[dict],
                                   transitions: list[dict],
                                   state_map: dict,
                                   id_start: int = 1,
                                   max_paths: int = 25,
                                   path_length: int = 3) -> list[dict]:
    adj = _build_adjacency(transitions)
    paths = []
    visited: set = set()

    for start in [s["state_id"] for s in states if s.get("url")]:
        queue = deque([[start]])
        while queue and len(paths) < max_paths:
            path = queue.popleft()
            if len(path) == path_length + 1:
                key = tuple(path)
                if key not in visited:
                    visited.add(key)
                    paths.append(path)
                continue
            for (nxt, _, _) in adj.get(path[-1], []):
                if nxt not in path:
                    queue.append(path + [nxt])

    tests   = []
    counter = id_start
    for i, state_path in enumerate(paths):
        urls = [state_map.get(sid, {}).get("url", "") for sid in state_path]
        if not all(urls):
            continue
        steps = [
            {"seq": j + 1, "action": "navigate", "target": u, "value": "", "expected": "Page loaded"}
            for j, u in enumerate(urls)
        ]
        steps.append({"seq": len(urls) + 1, "action": "assert_url",
                      "target": urls[-1], "value": "",
                      "expected": f"Arrived at {_page_label(urls[-1])}"})
        labels = " > ".join(_page_label(u) for u in urls)
        tests.append({
            "id":          f"TC_MULTI_NAV_{counter:03d}",
            "category":    "multi_step_nav",
            "priority":    _priority(i),
            "description": f"User journey: {labels}",
            "preconditions": ["Browser is open"],
            "steps":       steps,
            "expected_result": f"User completes {len(urls)}-page journey, lands at {urls[-1]}",
            "source_transitions": list(state_path),
        })
        counter += 1
    return tests


# ── 9. Link Validity Tests ─────────────────────────────────────────────────────

def generate_link_validity_tests(transitions: list[dict],
                                  state_map: dict,
                                  base_domain: str,
                                  id_start: int = 1) -> list[dict]:
    """Each unique internal from->to URL pair gets a load + body-present check."""
    tests      = []
    counter    = id_start
    seen_pairs: set = set()

    for i, tr in enumerate(transitions):
        from_sid = tr.get("from", "")
        to_sid   = tr.get("to", "")
        from_url = state_map.get(from_sid, {}).get("url", "")
        to_url   = state_map.get(to_sid,   {}).get("url", "")
        if not from_url or not to_url or from_url == to_url:
            continue
        if _is_external(to_url, base_domain):
            continue
        pair = (from_url, to_url)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)

        link_text = _clean_text(tr.get("action", {}).get("text", "")) or to_url
        to_path   = urlparse(to_url).path or "/"

        tests.append({
            "id":          f"TC_LINK_VALID_{counter:03d}",
            "category":    "link_validity",
            "priority":    _priority(i),
            "description": f"Verify link '{link_text}' from '{_page_label(from_url)}' leads to valid page",
            "preconditions": ["Browser is open"],
            "steps": [
                {"seq": 1, "action": "navigate",               "target": from_url,"value": "", "expected": "Source page loaded"},
                {"seq": 2, "action": "navigate",               "target": to_url,  "value": "", "expected": "Link destination loads"},
                {"seq": 3, "action": "assert_url",             "target": to_url,  "value": "", "expected": f"URL is '{to_path}'"},
                {"seq": 4, "action": "assert_element_present", "target": "body",  "value": "", "expected": "Page body is not blank"},
            ],
            "expected_result": f"Link resolves to a valid live page at {to_url}",
            "source_transitions": [tr.get("id", "")],
        })
        counter += 1
    return tests


# ── 10. Utility / Global Nav Link Tests ───────────────────────────────────────

def generate_utility_link_tests(transitions: list[dict],
                                 state_map: dict,
                                 base_domain: str,
                                 id_start: int = 1,
                                 min_appearances: int = 3) -> list[dict]:
    """
    Detect links that appear on many pages (header/nav utility links)
    purely by frequency in the graph — no hardcoded link names.
    """
    nav_links = _detect_nav_links(transitions, state_map, base_domain, min_appearances)
    tests     = []
    counter   = id_start
    seen: set = set()

    for i, link in enumerate(nav_links):
        link_text = link["link_text"]
        to_url    = link["to_url"]
        if not link_text or link_text in seen:
            continue
        seen.add(link_text)

        # Test from every page this link appears on (up to 3 for brevity)
        source_pages = [
            state_map.get(tr.get("from", ""), {}).get("url", "")
            for tr in transitions
            if _clean_text(tr.get("action", {}).get("text", "")) == link_text
        ]
        source_pages = list(dict.fromkeys(p for p in source_pages if p))[:3]

        for j, from_url in enumerate(source_pages):
            tests.append({
                "id":          f"TC_UTIL_LINK_{counter:03d}",
                "category":    "utility_link",
                "priority":    _priority(i + j),
                "description": f"Verify global nav link '{link_text}' is present and clickable on '{_page_label(from_url)}'",
                "preconditions": ["Browser is open"],
                "steps": [
                    {"seq": 1, "action": "navigate",               "target": from_url, "value": "",          "expected": "Page loaded"},
                    {"seq": 2, "action": "assert_element_present", "target": "a",      "value": link_text,   "expected": f"'{link_text}' link exists"},
                    {"seq": 3, "action": "click_link",             "target": to_url,   "value": link_text,   "expected": f"'{link_text}' is clickable"},
                    {"seq": 4, "action": "assert_url",             "target": to_url,   "value": "",          "expected": "Destination page loaded"},
                ],
                "expected_result": f"Global nav link '{link_text}' is present and navigates to {to_url}",
                "source_transitions": [],
            })
            counter += 1

    return tests


# ── Main public API ────────────────────────────────────────────────────────────

def generate_all(graph_path: str, base_url: str = "",
                 max_multi_step: int = 25) -> list[dict]:
    """
    Generate all deterministic test cases from the transition graph.
    Fully site-agnostic — works for any website.
    Returns flat list of test case dicts.
    """
    with open(graph_path, encoding="utf-8") as f:
        graph = json.load(f)

    states      = graph.get("states", [])
    transitions = graph.get("transitions", [])
    meta        = graph.get("meta", {})
    base_url    = base_url or meta.get("seed_url", "").rstrip("/")
    base_domain = urlparse(base_url).netloc

    state_map = _build_state_map(states)
    internal_states = [
        s for s in states
        if s.get("page_type") != "external_boundary"
        and base_domain in s.get("url", "")
    ]

    all_tests: list[dict] = []

    def add(tests: list[dict]) -> None:
        all_tests.extend(tests)

    # 1. Click-based navigation — most realistic user simulation
    add(generate_click_navigation_tests(transitions, state_map, base_domain,
                                         id_start=len(all_tests) + 1))

    # 2. Page load + title verification for every state
    add(generate_page_load_tests(internal_states,
                                  id_start=len(all_tests) + 1))

    # 3. Browser Back button simulation
    add(generate_back_navigation_tests(transitions, state_map,
                                        id_start=len(all_tests) + 1, max_tests=20))

    # 4. Core UI element presence on every page
    add(generate_element_presence_tests(internal_states,
                                         id_start=len(all_tests) + 1))

    # 5. Content validation via URL-derived keywords
    add(generate_content_validation_tests(internal_states,
                                           id_start=len(all_tests) + 1))

    # 6. Standard negative/bad URL tests
    add(generate_negative_url_tests(base_url,
                                     id_start=len(all_tests) + 1))

    # 7. Logo / home link from every inner page
    add(generate_logo_click_tests(internal_states, transitions, state_map, base_url,
                                   id_start=len(all_tests) + 1))

    # 8. Multi-step user journeys (BFS paths)
    add(generate_multi_step_nav_tests(internal_states, transitions, state_map,
                                       id_start=len(all_tests) + 1,
                                       max_paths=max_multi_step))

    # 9. Link validity for every unique page pair
    add(generate_link_validity_tests(transitions, state_map, base_domain,
                                      id_start=len(all_tests) + 1))

    # 10. Global/utility nav links (detected by frequency, not hardcoded)
    add(generate_utility_link_tests(transitions, state_map, base_domain,
                                     id_start=len(all_tests) + 1))

    return all_tests


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Deterministic exploratory test generator — works for any website"
    )
    parser.add_argument("--graph",      default="results/transition_graph.json")
    parser.add_argument("--output",     default="results/test_cases_deterministic.json")
    parser.add_argument("--multi-step", type=int, default=25)
    args = parser.parse_args()

    tests = generate_all(args.graph, max_multi_step=args.multi_step)

    by_cat: dict = {}
    for tc in tests:
        cat = tc.get("category", "other")
        by_cat[cat] = by_cat.get(cat, 0) + 1

    out = {
        "meta": {
            "generated_by": "deterministic_generator.py",
            "graph_file":   args.graph,
            "total_tests":  len(tests),
            "by_category":  by_cat,
        },
        "test_cases": tests,
    }

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"\nGenerated {len(tests)} deterministic test cases:")
    for cat, cnt in sorted(by_cat.items()):
        bar = "#" * min(cnt // 2, 40)
        print(f"  {cat:<25} : {cnt:>4}  {bar}")
    print(f"\n-> {args.output}")
