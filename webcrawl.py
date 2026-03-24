
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import logging
from collections import Counter, deque
from typing import Optional
from urllib.parse import urljoin, urlparse, urlencode, parse_qsl

import sys
sys.stdout.reconfigure(line_buffering=True)

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("Missing dependencies. Run:  pip install requests beautifulsoup4")
    sys.exit(1)

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import (
        WebDriverException,
        StaleElementReferenceException,
        TimeoutException,
    )
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

def logp(msg: str, depth: int = 0):
    log.info("  " * depth + msg)



# BASE_URL    = "https://llm-exploratory-hub.lovable.app/"
# BASE_URL    = "https://explor-bot-forge.lovable.app/"
BASE_URL    = "https://testerweb.lovable.app/products"

OUTPUT_FILE = "results/transition_graph.json"

MAX_DEPTH        = 2      
MAX_PAGES        = 10    
REQUEST_TIMEOUT  = 15     
CRAWL_DELAY      = 0.3    

# ── Hybrid / Browser Config ───────────────────────────────────────────────────
ENABLE_BROWSER_FALLBACK    = True
BROWSER_TIMEOUT            = 10
BROWSER_INTERACTION_DELAY  = 0.5
MAX_CLICKABLES_PER_PAGE    = 6   

# ── Blocked / external patterns ───────────────────────────────────────────────
BLOCKED_PATHS = [
    "/checkout", "/payment", "/subscribe", "/oauth", "/logout",
    "/log-out", "/sign-out", "/delete", "/remove",
]
EXTERNAL_DOMAINS = [
    "stripe.com", "paypal.com", "accounts.google.com",
    "github.com", "facebook.com", "twitter.com",
]

_DANGER = re.compile(
    r"\b(delete|remove|logout|log.?out|sign.?out|pay(ment)?|purchase|buy|confirm|cancel|reset)\b",
    re.IGNORECASE,
)
_DANGER_CLICK = re.compile(
    r"\b(logout|log.?out|sign.?out|delete|remove|pay(ment)?|purchase|cancel)\b",
    re.IGNORECASE,
)

def norm(url: str, base: str = "") -> str:
    """
    Normalise a URL: resolve relative refs, strip trailing slash, drop
    fragment, and SORT query parameters so that ?a=1&b=2 and ?b=2&a=1
    produce the same canonical URL.
.
    """
    try:
        resolved = urljoin(base, url) if base else url
        p = urlparse(resolved)
        if p.scheme not in ("http", "https"):
            return ""
        path = p.path.rstrip("/") or "/"
        # Sort query params for stable canonical form
        query = urlencode(sorted(parse_qsl(p.query, keep_blank_values=True)))
        return f"{p.scheme}://{p.netloc}{path}" + (f"?{query}" if query else "")
    except Exception:
        return ""


def netloc_of(url: str) -> str:
    return urlparse(url).netloc


def same_origin(a: str, b: str) -> bool:
    return netloc_of(a) == netloc_of(b)


def is_external(url: str, base_domain: str) -> bool:
    nl = netloc_of(url)
    if nl and nl != base_domain:
        return True
    return any(ext in nl for ext in EXTERNAL_DOMAINS)


def is_blocked(url: str) -> bool:
    path = urlparse(url).path
    return any(path.lower().startswith(b) for b in BLOCKED_PATHS)


def skip_href(href: str) -> bool:
    return (
        not href
        or href.startswith(("mailto:", "tel:", "javascript:", "#", "data:"))
        or _DANGER.search(href) is not None
    )


def _is_safe_to_click(text: str) -> bool:
    return not _DANGER_CLICK.search(text or "")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STATE / PAGE HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def state_id(url: str) -> str:
    """
    Human-readable state ID.  Uses the normalised URL (with sorted query
    params) so two routes to the same canonical URL share one state node.
    """
    url = norm(url)  # ensure canonical form before hashing
    p   = urlparse(url)
    slug = (p.path.strip("/").replace("/", "-")) or "root"
    h    = url_hash(url)
    return f"{p.netloc}__{slug}__{h}"


def classify_page(soup: BeautifulSoup) -> str:
    if soup.find("input", {"type": "password"}):
        return "auth_page"
    if soup.find(attrs={"role": re.compile(r"^(dialog|alertdialog)$")}):
        return "modal_page"
    forms  = soup.find_all("form")
    inputs = soup.find_all("input")
    if forms and len(inputs) >= 2:
        return "form_page"
    if soup.find("table"):
        return "data_page"
    if len(soup.find_all("a", href=True)) > 20:
        return "nav_hub"
    return "generic_page"


def extract_form_fields(soup: BeautifulSoup) -> list:
    fields = []
    seen   = set()
    for el in soup.find_all(["input", "select", "textarea"]):
        ftype = (el.get("type") or el.name or "").lower()
        if ftype in ("submit", "button", "image", "reset", "hidden"):
            continue
        name = el.get("name") or el.get("id")
        if not name or name in seen:
            continue
        seen.add(name)
        label_text = None
        el_id = el.get("id")
        if el_id:
            lbl = soup.find("label", {"for": el_id})
            if lbl:
                label_text = lbl.get_text(strip=True)[:80]
        opts = None
        if el.name == "select":
            opts = [
                {"value": o.get("value", ""), "text": o.get_text(strip=True)}
                for o in el.find_all("option")[:10]
            ]
        fields.append({
            "name":        name,
            "id":          el.get("id"),
            "type":        ftype,
            "label":       label_text,
            "placeholder": el.get("placeholder"),
            "required":    el.has_attr("required"),
            "options":     opts,
        })
    return fields


def extract_links(soup: BeautifulSoup, base_url: str) -> list[dict]:
    links = []
    seen  = set()
    for a in soup.find_all("a", href=True):
        raw = a["href"].strip()
        if skip_href(raw):
            continue
        href = norm(raw, base_url)
        if not href or href in seen:
            continue
        seen.add(href)
        text = a.get_text(strip=True)[:120]
        links.append({"href": href, "text": text, "tag": "a"})
    return links


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# JS-HEAVY DETECTION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_SPA_MARKERS = re.compile(
    r'id=["\'](?:root|app)["\']|data-reactroot',
    re.IGNORECASE,
)


def is_js_heavy(html: str, links: list) -> bool:
    if len(links) < 3:
        return True
    if _SPA_MARKERS.search(html):
        return True
    soup        = BeautifulSoup(html, "html.parser")
    script_text = " ".join(s.get_text() for s in soup.find_all("script"))
    body_text   = soup.get_text(separator=" ", strip=True)
    if len(script_text) > 5 * max(len(body_text), 1):
        return True
    title = soup.title.get_text(strip=True) if soup.title else ""
    if title and len(body_text) < 150:
        return True
    return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DOM STRUCTURAL DIFFER  (replaces raw char-count threshold — FIX #5)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _structural_ids(html: str) -> set[str]:
    """
    Return a set of structural identifiers from the HTML:
    element IDs, ARIA roles, and data-testid values.
    Used to detect meaningful DOM mutations vs cosmetic changes.
    """
    soup    = BeautifulSoup(html, "html.parser")
    markers = set()
    for el in soup.find_all(True):
        if el.get("id"):
            markers.add(f"id:{el['id']}")
        if el.get("role"):
            markers.add(f"role:{el['role']}")
        if el.get("data-testid"):
            markers.add(f"testid:{el['data-testid']}")
    return markers


def has_structural_change(pre_html: str, post_html: str) -> bool:
    """
    Return True only if the click produced a meaningful structural DOM
    change — new IDs, new ARIA roles, or new data-testid attributes appeared.
    Ignores spinner/loader mutations and text-only changes.
    """
    pre  = _structural_ids(pre_html)
    post = _structural_ids(post_html)
    new_markers = post - pre
    # Filter out common transient markers (loaders, tooltips)
    noise = re.compile(r"(loading|spinner|tooltip|skeleton|ripple)", re.IGNORECASE)
    meaningful = {m for m in new_markers if not noise.search(m)}
    return bool(meaningful)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# HTTP SESSION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/121.0.0.0 Safari/537.36"
        ),
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return s


def fetch(
    session: requests.Session, url: str, depth: int = 0
) -> Optional[requests.Response]:
    try:
        resp = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        ct   = resp.headers.get("Content-Type", "")
        if "html" not in ct:
            logp(f"[SKIP] Non-HTML ({ct[:30]}): {url}", depth)
            return None
        return resp
    except Exception as e:
        logp(f"[WARN] fetch {url}: {e}", depth)
        return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ALERT HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def handle_alert(driver: "webdriver.Chrome", prompt_value: str = "test") -> dict | None:
    try:
        alert = driver.switch_to.alert
        text  = alert.text or ""
        try:
            alert.send_keys(prompt_value)
            atype = "prompt"
        except Exception:
            atype = "alert"
        alert.accept()
        logp(f"[ALERT] Accepted {atype}: '{text[:80]}'")
        return {"alert_type": atype, "alert_text": text, "action": "accept"}
    except Exception:
        return None


def _flush_any_alert(driver: "webdriver.Chrome") -> None:
    handle_alert(driver)



class GraphBuilder:
    def __init__(self, seed: str, max_depth: int = MAX_DEPTH):
        self.seed         = norm(seed)
        self.base_domain  = netloc_of(self.seed)
        self.max_depth    = max_depth
        self.states       : dict[str, dict] = {}
        self.transitions  : list[dict]      = []
        self._keys        : set             = set()
        self._tc          : int             = 0
        self.visited_urls : set[str]        = set()

    def _reg(self, s: dict):
        sid = s["state_id"]
        if sid not in self.states:
            self.states[sid] = s
        else:
            if s.get("form_fields") and not self.states[sid].get("form_fields"):
                self.states[sid]["form_fields"] = s["form_fields"]

    def _add_transition(
        self, from_id: str, to_id: str, link: dict, behavior: str
    ):
        if from_id == to_id:
            return
        text = (link.get("text") or "").strip()
        key  = (from_id, to_id, behavior, text)
        if key in self._keys:
            return
        self._keys.add(key)
        self._tc += 1
        self.transitions.append({
            "id":       f"T{self._tc:04d}",
            "from":     from_id,
            "to":       to_id,
            "behavior": behavior,
            "action": {
                "tag":  link.get("tag", "a"),
                "text": text,
                "href": link.get("href"),
            },
        })

    def _make_state(
        self, url: str, resp: requests.Response
    ) -> tuple[dict, BeautifulSoup]:
        soup  = BeautifulSoup(resp.text, "html.parser")
        title = soup.title.get_text(strip=True) if soup.title else ""
        s = {
            "state_id":  state_id(url),
            "url":       url,
            "title":     title,
            "page_type": classify_page(soup),
            "status":    resp.status_code,
        }
        fields = extract_form_fields(soup)
        if fields:
            s["form_fields"] = fields
        return s, soup

    def build(self) -> dict:
        session = make_session()
        queue   = deque([(self.seed, 0, None, {})])

        while queue:
            url, depth, from_sid, link_info = queue.popleft()

            if url in self.visited_urls:
                continue
            if len(self.visited_urls) >= MAX_PAGES:
                logp(f"[STOP] Page cap {MAX_PAGES} reached")
                break
            if depth > self.max_depth:
                continue

            logp(f"[VISIT d={depth}] {url}", depth)
            self.visited_urls.add(url)

            resp = fetch(session, url, depth)
            if resp is None:
                continue

            final_url = norm(resp.url)
            if final_url != url:
                logp(f"  -> redirected to {final_url}", depth)
                self.visited_urls.add(final_url)
                url = final_url

            state, soup = self._make_state(url, resp)
            self._reg(state)
            sid = state["state_id"]

            if from_sid and from_sid != sid:
                beh = "client_side_navigation" if same_origin(url, self.seed) else "navigation"
                self._add_transition(from_sid, sid, link_info, beh)

            logp(
                f"[INFO] type={state['page_type']} | "
                f"{len(state.get('form_fields', []))} fields | {resp.status_code}",
                depth,
            )

            if depth >= self.max_depth:
                continue

            for lk in extract_links(soup, url):
                href = lk["href"]
                if href in self.visited_urls:
                    target_sid = state_id(href)
                    if target_sid in self.states:
                        self._add_transition(sid, target_sid, lk, "client_side_navigation")
                    continue
                if is_external(href, self.base_domain):
                    ext_sid = hashlib.md5(f"external:{href}".encode()).hexdigest()
                    if ext_sid not in self.states:
                        self.states[ext_sid] = {
                            "state_id":  ext_sid,
                            "url":       href,
                            "title":     f"External: {netloc_of(href)}",
                            "page_type": "external_boundary",
                        }
                    self._add_transition(sid, ext_sid, lk, "navigation")
                    continue
                if is_blocked(href):
                    logp(f"[BLOCK] {href[:70]}", depth + 1)
                    continue
                queue.append((href, depth + 1, sid, lk))

            time.sleep(CRAWL_DELAY)

        return self._output()

    def _output(self) -> dict:
        beh = dict(Counter(t["behavior"] for t in self.transitions))
        sw  = sum(1 for s in self.states.values() if s.get("form_fields"))
        tf  = sum(len(s.get("form_fields", [])) for s in self.states.values())
        return {
            "meta": {
                "seed_url":          self.seed,
                "max_depth":         self.max_depth,
                "states":            len(self.states),
                "transitions":       len(self.transitions),
                "behaviors":         beh,
                "states_with_forms": sw,
                "total_form_fields": tf,
            },
            "states":      list(self.states.values()),
            "transitions": self.transitions,
        }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# HEADLESS CHROME FACTORY
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _make_headless_driver() -> "webdriver.Chrome":
    opts = ChromeOptions()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1280,900")
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    )
    return webdriver.Chrome(options=opts)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# HYBRID CRAWLER — HTTP fast path + Selenium fallback
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class HybridCrawler(GraphBuilder):
    """
    Extends GraphBuilder with automatic Selenium fallback when a page is
    detected to be JavaScript-heavy.

    All 7 bugs from the original are fixed here — see module docstring.
    """

    def __init__(self, seed: str, max_depth: int = MAX_DEPTH):
        super().__init__(seed, max_depth)
        self._driver        : Optional["webdriver.Chrome"] = None
        self._browser_pages : int  = 0
        self._browser_queue : list = []

    # ── Driver lifecycle ──────────────────────────────────────────────────────

    def _get_driver(self) -> "webdriver.Chrome":
        if self._driver is None:
            if not SELENIUM_AVAILABLE:
                raise RuntimeError(
                    "Selenium not installed. Run: pip install selenium"
                )
            logp("[BROWSER] Initializing headless Chrome")
            self._driver = _make_headless_driver()
        return self._driver

    def _close_driver(self):
        if self._driver is not None:
            try:
                self._driver.quit()
                logp("[BROWSER] Driver closed")
            except Exception:
                pass
            self._driver = None

    # ── State builder from live DOM ───────────────────────────────────────────

    def _make_state_from_browser(
        self, url: str, driver: "webdriver.Chrome"
    ) -> tuple[dict, BeautifulSoup]:
        html  = driver.page_source
        soup  = BeautifulSoup(html, "html.parser")
        title = soup.title.get_text(strip=True) if soup.title else driver.title
        s = {
            "state_id":   state_id(url),
            "url":        url,
            "title":      title,
            "page_type":  classify_page(soup),
            "status":     200,
            "crawl_mode": "browser",
        }
        fields = extract_form_fields(soup)
        if fields:
            s["form_fields"] = fields
        return s, soup

    # ── Clickable collector ───────────────────────────────────────────────────

    def _find_clickables(
        self, driver: "webdriver.Chrome"
    ) -> list[tuple]:
        """
        Collect all visible interactive elements.  Called fresh before every
        click so we never hold stale element references across navigations.

        FIX #2: previously this was called ONCE before the click loop; after
        driver.back() every element in the list became stale, causing
        StaleElementReferenceException on every subsequent click.
        """
        results     = []
        seen_keys   : set = set()
        selectors   = [
            (By.TAG_NAME, "button"),
            (By.CSS_SELECTOR, "input[type='submit']"),
            (By.CSS_SELECTOR, "input[type='button']"),
            (By.TAG_NAME, "a"),
        ]
        for by, selector in selectors:
            for el in driver.find_elements(by, selector)[:80]:
                try:
                    if not el.is_displayed():
                        continue
                    text = (el.text or el.get_attribute("value") or "").strip()
                    tag  = el.tag_name.lower()
                    key  = (tag, text[:60])
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    results.append((el, text, tag))
                except StaleElementReferenceException:
                    continue
        return results

    # ── Core browser crawl for a single URL ──────────────────────────────────

    def crawl_browser(
        self,
        url: str,
        parent_sid: Optional[str],   # renamed from from_sid to avoid confusion
        depth: int,
    ):
        """
        Load *url* in the headless browser and:
          1. Record the state.
          2. Extract all rendered links (including hidden nav menus via JS).
          3. Click each safe interactive element; record URL-change and
             structural DOM-change transitions.

        """
        # ── FIX #1: mark visited immediately ─────────────────────────────────
        self.visited_urls.add(url)

        driver = self._get_driver()
        logp(f"[BROWSER] Loading: {url}", depth)

        try:
            driver.get(url)
            WebDriverWait(driver, BROWSER_TIMEOUT).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            time.sleep(0.8)
        except TimeoutException:
            logp(f"[BROWSER] Timeout: {url}", depth)
            return
        except WebDriverException as exc:
            logp(f"[BROWSER] Error loading {url}: {exc}", depth)
            return

        _flush_any_alert(driver)
        current_url = norm(driver.current_url)
        state, soup = self._make_state_from_browser(current_url, driver)
        self._reg(state)
        sid = state["state_id"]

        # ── FIX #6: use parent_sid (caller's sid), not the current page's ────
        if parent_sid and parent_sid != sid:
            self._add_transition(
                parent_sid, sid,
                {"href": current_url, "text": state["title"], "tag": "a"},
                "client_side_navigation",
            )

        logp(
            f"[BROWSER] type={state['page_type']} | "
            f"{len(state.get('form_fields', []))} fields",
            depth,
        )

        # ── Extract all rendered links via JS (catches hidden nav menus) ──────
        try:
            all_hrefs = driver.execute_script(
                "return Array.from(document.querySelectorAll('a[href]'))"
                ".map(a => [a.href, a.innerText.trim().substring(0,120)])"
                ".filter(p => p[0]);"
            )
        except WebDriverException:
            all_hrefs = []

        for raw_href, link_text in all_hrefs:
            href = norm(raw_href, current_url)
            if not href or is_external(href, self.base_domain) or is_blocked(href):
                continue
            if skip_href(raw_href):
                continue
            lk = {"href": href, "text": link_text, "tag": "a"}
            if href in self.visited_urls:
                target_sid = state_id(href)
                if target_sid in self.states:
                    self._add_transition(sid, target_sid, lk, "client_side_navigation")
            else:
                self._browser_queue.append((href, depth + 1, sid, lk))

        # Also catch links that appear only in the rendered BeautifulSoup
        for lk in extract_links(soup, current_url):
            href = lk["href"]
            if is_external(href, self.base_domain) or is_blocked(href):
                continue
            if href not in self.visited_urls:
                self._browser_queue.append((href, depth + 1, sid, lk))

        # ── Click interactions ────────────────────────────────────────────────
        if depth >= self.max_depth:
            return

        pre_html = driver.page_source   # snapshot before any clicks

        for click_index in range(MAX_CLICKABLES_PER_PAGE):

            try:
                clickables = self._find_clickables(driver)
            except WebDriverException:
                break

            if click_index >= len(clickables):
                break   # no more elements to process

            element, elem_text, elem_tag = clickables[click_index]

            if not _is_safe_to_click(elem_text):
                logp(f"[BROWSER][SKIP] Danger: '{elem_text}'", depth)
                continue

            _flush_any_alert(driver)
            pre_url      = norm(driver.current_url)
            pre_html_snap = driver.page_source

            try:
                driver.execute_script("arguments[0].scrollIntoView(true);", element)
                element.click()
                time.sleep(BROWSER_INTERACTION_DELAY)
                alert_meta = handle_alert(driver)
                if alert_meta:
                    existing = self.states.get(sid, {})
                    existing.setdefault("alerts", []).append(
                        {"triggered_by": elem_text, **alert_meta}
                    )
                    if sid in self.states:
                        self.states[sid] = existing
            except (StaleElementReferenceException, WebDriverException):
                _flush_any_alert(driver)
                try:
                    driver.get(url)
                    time.sleep(0.8)
                except WebDriverException:
                    pass
                continue

            _flush_any_alert(driver)
            post_url  = norm(driver.current_url)
            post_html = driver.page_source

            # ── Case A: URL changed → record navigation/form transition ───────
            if post_url and post_url != pre_url:
                behavior = (
                    "form_submission"
                    if elem_tag in ("input", "button")
                    else "client_side_navigation"
                )
                self._record_browser_transition(
                    sid,
                    post_url,
                    lk={"text": elem_text, "tag": elem_tag, "href": post_url},
                    behavior=behavior,
                    depth=depth,
                    driver=driver,
                )
                # Navigate back to continue exploring current page
                try:
                    driver.back()
                    time.sleep(0.8)
                    _flush_any_alert(driver)
                except WebDriverException:
                    try:
                        driver.get(url)
                        time.sleep(0.8)
                    except WebDriverException:
                        break

            # ── Case B: Structural DOM change (modal/drawer opened) ───────────

            elif has_structural_change(pre_html_snap, post_html):
                logp(
                    f"[BROWSER] Structural DOM change via '{elem_text}' on {url}",
                    depth,
                )
                new_state, _ = self._make_state_from_browser(post_url or url, driver)
                self._reg(new_state)
                new_sid = new_state["state_id"]
                if new_sid != sid:
                    self._add_transition(
                        sid, new_sid,
                        {"text": elem_text, "tag": elem_tag, "href": post_url or url},
                        "client_side_navigation",
                    )
                # Close any opened dialog and return to base page state
                try:
                    driver.get(url)
                    time.sleep(0.8)
                    _flush_any_alert(driver)
                except WebDriverException:
                    break

    # ── Transition recorder ───────────────────────────────────────────────────

    def _record_browser_transition(
        self,
        from_sid: str,
        new_url:  str,
        lk:       dict,
        behavior: str,
        depth:    int,
        driver:   "webdriver.Chrome",
    ):
        new_url = norm(new_url)
        if not new_url or is_external(new_url, self.base_domain) or is_blocked(new_url):
            return

        new_state, _ = self._make_state_from_browser(new_url, driver)
        self._reg(new_state)
        new_sid = new_state["state_id"]
        self._add_transition(from_sid, new_sid, lk, behavior)
        logp(
            f"[BROWSER][T] {behavior}: ...{from_sid[-8:]} → ...{new_sid[-8:]} "
            f"via '{lk.get('text', '')}'",
            depth,
        )
        if new_url not in self.visited_urls and depth < self.max_depth:
            self._browser_queue.append((new_url, depth + 1, new_sid, lk))

    # ── Overridden BFS build() with hybrid HTTP → Browser fallback ────────────

    def build(self) -> dict:
        """
        BFS crawl with automatic HTTP → Selenium fallback.


        Only URLs not yet in visited_urls are added to the queue, and
        visited_urls is checked again at the top of the outer loop — so
        race conditions between HTTP and browser discovery are handled safely.
        """
        session = make_session()
        queue   = deque([(self.seed, 0, None, {})])

        try:
            while queue or self._browser_queue:

                # ── FIX #3/#7: drain browser queue preserving original tuples ─
                # We move items from _browser_queue to the BFS queue only if the
                # URL hasn't been visited yet.  Depth and from_sid are NOT reset.
                while self._browser_queue:
                    item = self._browser_queue.pop(0)
                    burl = item[0]
                    if burl not in self.visited_urls:
                        queue.append(item)

                if not queue:
                    break

                url, depth, from_sid, link_info = queue.popleft()

                if url in self.visited_urls:
                    continue
                if len(self.visited_urls) >= MAX_PAGES:
                    logp(f"[STOP] Page cap {MAX_PAGES} reached")
                    break
                if depth > self.max_depth:
                    continue

                logp(f"[VISIT d={depth}] {url}", depth)
                self.visited_urls.add(url)

                # ── HTTP fast path ────────────────────────────────────────────
                resp = fetch(session, url, depth)

                if resp is not None:
                    final_url = norm(resp.url)
                    if final_url != url:
                        logp(f"  -> redirected to {final_url}", depth)
                        self.visited_urls.add(final_url)
                        url = final_url

                    state, soup = self._make_state(url, resp)
                    self._reg(state)
                    sid = state["state_id"]

                    if from_sid and from_sid != sid:
                        beh = (
                            "client_side_navigation"
                            if same_origin(url, self.seed)
                            else "navigation"
                        )
                        self._add_transition(from_sid, sid, link_info, beh)

                    logp(
                        f"[HTTP] type={state['page_type']} | "
                        f"{len(state.get('form_fields', []))} fields | {resp.status_code}",
                        depth,
                    )

                    links = extract_links(soup, url)
                    pre_tc = len(self.transitions)

                    for lk in links:
                        href = lk["href"]
                        if href in self.visited_urls:
                            target_sid = state_id(href)
                            if target_sid in self.states:
                                self._add_transition(sid, target_sid, lk, "client_side_navigation")
                            continue
                        if is_external(href, self.base_domain):
                            ext_sid = hashlib.md5(f"external:{href}".encode()).hexdigest()
                            if ext_sid not in self.states:
                                self.states[ext_sid] = {
                                    "state_id":  ext_sid,
                                    "url":       href,
                                    "title":     f"External: {netloc_of(href)}",
                                    "page_type": "external_boundary",
                                }
                            self._add_transition(sid, ext_sid, lk, "navigation")
                            continue
                        if is_blocked(href):
                            logp(f"[BLOCK] {href[:70]}", depth + 1)
                            continue
                        queue.append((href, depth + 1, sid, lk))

                    new_transitions = len(self.transitions) - pre_tc

                    # ── Decide whether browser fallback is needed ─────────────
                    if ENABLE_BROWSER_FALLBACK and SELENIUM_AVAILABLE:
                        js_heavy = is_js_heavy(resp.text, links)
                        no_new   = (new_transitions == 0 and len(links) == 0)
                        if js_heavy or no_new:
                            logp(
                                f"[HYBRID] js_heavy={js_heavy} no_new={no_new}"
                                f" → browser fallback",
                                depth,
                            )
                            self._browser_pages += 1
                            # FIX #6: pass sid (current page) as parent_sid
                            # so browser-discovered transitions connect correctly
                            self.crawl_browser(url, parent_sid=sid, depth=depth)

                else:
                    # HTTP failed entirely — try browser directly
                    sid = state_id(url)
                    if ENABLE_BROWSER_FALLBACK and SELENIUM_AVAILABLE:
                        logp(f"[HYBRID] HTTP failed → browser fallback", depth)
                        self._browser_pages += 1
                        self.crawl_browser(url, parent_sid=from_sid, depth=depth)

                time.sleep(CRAWL_DELAY)

        finally:
            self._close_driver()

        return self._output()

    def _output(self) -> dict:
        out = super()._output()
        out["meta"]["browser_fallback_pages"]   = self._browser_pages
        out["meta"]["browser_fallback_enabled"] = ENABLE_BROWSER_FALLBACK
        return out


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENTRY POINT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main():
    os.makedirs(os.path.dirname(OUTPUT_FILE) or ".", exist_ok=True)

    logp(f"Starting crawl : {BASE_URL}")
    logp(f"Max depth      : {MAX_DEPTH}  |  Max pages: {MAX_PAGES}")
    logp(f"Browser fallback: {'ENABLED' if ENABLE_BROWSER_FALLBACK else 'DISABLED'}")

    t0      = time.time()
    crawler = HybridCrawler(seed=BASE_URL, max_depth=MAX_DEPTH)
    result  = crawler.build()

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    m       = result["meta"]
    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s")
    print(f"States      : {m['states']}")
    print(f"Transitions : {m['transitions']}")
    print(f"Forms       : {m['states_with_forms']} pages with forms | "
          f"{m['total_form_fields']} total fields")
    print(f"Behaviors   : {m['behaviors']}")
    print(f"Browser fallback pages: {m['browser_fallback_pages']}")
    print(f"Output      : {OUTPUT_FILE}")


if __name__ == "__main__":
    main()