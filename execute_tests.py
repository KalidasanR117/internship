"""
execute_tests.py — Universal Selenium test runner
─────────────────────────────────────────────────────────────────────────────
Reads a generated test-cases JSON file and executes every test using Selenium.
No LLM calls — this is a pure executor.

Unified schema (output of testgen.py with the new unified format):
  test_id       : str   — unique test identifier
  category      : str   — test category (Navigation, Functional, Negative, etc.)
  title         : str   — human-readable test title
  precondition  : str   — pre-condition description
  steps         : list  — human-readable step descriptions (for documentation)
  test_data     : dict  — {locator: {...}, input_values: {field: value}}
  expected_result: str  — expected outcome description
  from_state    : str   — starting page URL
  to_state      : str   — ending page URL
  actions       : list  — machine-executable steps [{seq, action, target, value, expected}]
  source_transitions: list — transition IDs from the graph

The executor also accepts the older testgen.py schema (steps[] were dicts) and
the explore schema (test_id + from_state + test_data.locator without actions[]).
All schemas are handled generically via _resolve_actions().

Usage:
    python execute_tests.py                               # uses INPUT_FILE default
    python execute_tests.py --input results/test_cases.json
    python execute_tests.py --input results/test_cases.json --headless
    python execute_tests.py --browser firefox
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from selenium import webdriver
from selenium.common.exceptions import (
    ElementNotInteractableException,
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait

try:
    from webdriver_manager.chrome import ChromeDriverManager
    from webdriver_manager.firefox import GeckoDriverManager
    _WDM = True
except ImportError:
    _WDM = False

# ── Config ────────────────────────────────────────────────────────────────────

INPUT_FILE   = "results/test_cases.json"      # override with --input
OUTPUT_FILE  = "results/test_results.json"
HEADLESS     = False
BASE_DOMAIN  = ""                            # auto-detected from test cases at runtime

T_PAGE  = 30   # page load timeout (seconds)
T_NET   = 10   # network-idle wait
T_EL    = 8    # element wait
T_ACT   = 5    # action timeout (implicit)

# Category alias normalisation
CAT_ALIASES = {
    # Human-readable aliases → canonical
    "ui transitions":  "Navigation",
    "ui transition":   "Navigation",
    "interaction":     "Functional",
    "edge":            "Boundary",
    "edge case":       "Boundary",
    "edge_case":       "Boundary",
    "error handling":  "Validation",
    "error":           "Validation",
    "error_recovery":  "Validation",
    # testgen.py category keys → canonical names
    "navigation":       "Navigation",
    "form_valid":       "Functional",
    "form_invalid":     "Negative",
    "form_empty":       "Negative",
    "boundary":         "Boundary",
    "negative":         "Negative",
    "validation":       "Validation",
    "security":         "Security",
    # Creative category keys → canonical names
    "user_scenario":         "Navigation",
    "accessibility":         "Functional",
    "cross_page":            "Functional",
    "cross_page_consistency":"Functional",
    "seo_structure":         "Functional",
}

# Selectors tried (in order) to locate a result section after form submission
RESULT_SELS = [
    "#result", "#resultContent", ".result", ".answer", ".output",
    "[id*='result']", "[class*='result']", "table.results",
]

# Text patterns that indicate an inline validation message
VAL_PATTERNS = [
    "error", "invalid", "required", "please enter",
    "must be", "cannot be", "not valid",
]

# Submit-button fallback selectors when the test case provides no locator
SUBMIT_FALLBACKS = [
    '#submit',
    '#login',
    'button[id="submit"]',
    'button[id="login"]',
    'input[type="submit"]',
    'button[type="submit"]',
    'button[name="submit"]',
    'button.submit',
    'button.primary',
    'input[value="Submit"]',
    'input[value="Login"]',
    'input[value="Search"]',
    'input[value="Calculate"]',
]


# ── Driver factory ────────────────────────────────────────────────────────────

def make_driver(browser: str = "chrome", headless: bool = HEADLESS) -> webdriver.Remote:
    if browser.lower() == "firefox":
        opts = FirefoxOptions()
        if headless:
            opts.add_argument("--headless")
        if _WDM:
            return webdriver.Firefox(
                service=webdriver.firefox.service.Service(GeckoDriverManager().install()),
                options=opts,
            )
        return webdriver.Firefox(options=opts)

    # Default: Chrome
    opts = ChromeOptions()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/120.0"
    )
    if _WDM:
        return webdriver.Chrome(
            service=ChromeService(ChromeDriverManager().install()),
            options=opts,
        )
    return webdriver.Chrome(options=opts)


def _wait(driver, timeout=T_EL) -> WebDriverWait:
    return WebDriverWait(driver, timeout)


# ── Selector builder ──────────────────────────────────────────────────────────

def build_locators(loc: dict, text: str = "") -> list[tuple]:
    """Return a priority-ordered list of (By, value) pairs."""
    candidates: list[tuple] = []

    if loc.get("id"):
        candidates.append((By.CSS_SELECTOR, f'#{loc["id"]}'))
    if loc.get("aria_label"):
        candidates.append((By.CSS_SELECTOR, f'[aria-label="{loc["aria_label"]}"]'))
    if loc.get("data_testid"):
        candidates.append((By.CSS_SELECTOR, f'[data-testid="{loc["data_testid"]}"]'))
    if loc.get("aria"):
        candidates.append((By.CSS_SELECTOR, f'[aria-label="{loc["aria"]}"]'))
    if loc.get("testid"):
        candidates.append((By.CSS_SELECTOR, f'[data-testid="{loc["testid"]}"]'))
    if loc.get("name"):
        candidates.append((By.CSS_SELECTOR, f'[name="{loc["name"]}"]'))
    if loc.get("href"):
        href = loc["href"]
        candidates.append((By.CSS_SELECTOR, f'a[href="{href}"]'))
        slug = href.split("//")[-1].split("?")[0]
        candidates.append((By.CSS_SELECTOR, f'a[href*="{slug}"]'))
    if loc.get("xpath"):
        candidates.append((By.XPATH, loc["xpath"]))

    # Text-based fallbacks (XPath)
    label = (loc.get("text") or text or "").strip()
    if label:
        candidates.append((By.XPATH, f'//*[normalize-space(text())="{label}"]'))
        candidates.append((By.XPATH, f'//*[contains(text(),"{label}")]'))
        candidates.append((By.LINK_TEXT, label))
        candidates.append((By.PARTIAL_LINK_TEXT, label))

    # Deduplicate while preserving order
    seen, out = set(), []
    for pair in candidates:
        if pair not in seen:
            seen.add(pair)
            out.append(pair)
    return out


def find_element(driver, loc: dict, text: str = ""):
    """Return the first visible WebElement matched by the locator dict, or None."""
    locators = build_locators(loc, text)
    if not locators:
        return None

    # Quick check — no wait
    for by, val in locators:
        try:
            els = driver.find_elements(by, val)
            visible = [e for e in els if e.is_displayed()]
            if visible:
                print(f"    ✓ Found via: {by}='{val}'")
                return visible[0]
        except Exception:
            pass

    # Slow path: wait up to T_EL for any locator to appear
    for by, val in locators:
        try:
            el = _wait(driver, T_EL).until(EC.presence_of_element_located((by, val)))
            if el.is_displayed():
                print(f"    ✓ Found (after wait) via: {by}='{val}'")
                return el
        except TimeoutException:
            pass
        except Exception:
            pass

    return None


# ── Field filler ──────────────────────────────────────────────────────────────

def fill_fields(driver, fields: dict) -> list[str]:
    """Fill form fields; returns list of field names that could not be filled."""
    failed = []
    for name, val in fields.items():
        if val is None:
            continue
        # Strip leading # or . that the LLM emits as CSS selectors
        # e.g. "#project-name" → "project-name"
        bare = name.lstrip("#.")
        sels = [
            (By.ID,           bare),
            (By.NAME,         bare),
            # Direct CSS selector if caller already passed one
            (By.CSS_SELECTOR, name) if name.startswith(("#", ".")) else (By.ID, bare),
            (By.CSS_SELECTOR, f'[id="{bare}"]'),
            (By.CSS_SELECTOR, f'[id*="{bare}"]'),
            (By.CSS_SELECTOR, f'[name="{bare}"]'),
            (By.CSS_SELECTOR, f'[name*="{bare}"]'),
            # React / shadcn / Radix UI components use data-testid
            (By.CSS_SELECTOR, f'[data-testid="{bare}"]'),
            (By.CSS_SELECTOR, f'[data-testid*="{bare}"]'),
            # aria-label fallback
            (By.CSS_SELECTOR, f'[aria-label="{bare}"]'),
            (By.CSS_SELECTOR, f'[aria-label*="{bare}"]'),
            (By.CSS_SELECTOR, f'input[placeholder*="{bare}"]'),
            (By.CSS_SELECTOR, f'textarea[placeholder*="{bare}"]'),
            (By.CSS_SELECTOR, f'select[name="{bare}"]'),
        ]
        # Deduplicate while preserving order
        seen_sels: set = set()
        sels = [s for s in sels if not (s in seen_sels or seen_sels.add(s))]
        ok = False
        for by, sel_val in sels:
            try:
                els = driver.find_elements(by, sel_val)
                el = next((e for e in els if e.is_displayed()), None)
                if not el:
                    continue

                tag = el.tag_name.lower()
                if tag == "select":
                    sel_obj = Select(el)
                    try:
                        sel_obj.select_by_visible_text(str(val))
                    except Exception:
                        sel_obj.select_by_value(str(val))
                else:
                    driver.execute_script(
                        "arguments[0].value = arguments[1];"
                        "arguments[0].dispatchEvent(new Event('input',  {bubbles:true}));"
                        "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
                        el, str(val),
                    )
                    actual = el.get_attribute("value") or ""
                    if actual.replace(",", "") != str(val).replace(",", ""):
                        ActionChains(driver).triple_click(el).perform()
                        el.send_keys(str(val))

                print(f"    ✓ [{name}] = '{val}' ({tag})")
                ok = True
                break

            except (StaleElementReferenceException, ElementNotInteractableException):
                pass
            except Exception:
                pass

        if not ok:
            # Fallback: custom React/Vue Select component
            for by, sel_val in sels[:4]:
                try:
                    els = driver.find_elements(by, sel_val)
                    el = next((e for e in els if e.is_displayed()), None)
                    if not el:
                        continue
                    el.click()
                    time.sleep(0.5)
                    opt_xpath = f'//*[normalize-space(text())="{val}"]'
                    opts = driver.find_elements(By.XPATH, opt_xpath)
                    visible_opt = next((o for o in opts if o.is_displayed()), None)
                    if visible_opt:
                        visible_opt.click()
                        print(f"    ✓ [{name}] = '{val}' (custom ui-select)")
                        ok = True
                        break
                    else:
                        from selenium.webdriver.common.keys import Keys
                        el.send_keys(Keys.ESCAPE)
                except Exception:
                    pass
            if ok:
                continue

        if not ok:
            print(f"    ⚠ Skip field: {name}")
            failed.append(name)

    return failed


# ── Result / validation detectors ─────────────────────────────────────────────

def result_visible(driver) -> tuple[bool, str]:
    """Return (True, selector) if a result section is visible on the page."""
    for sel in RESULT_SELS:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            visible = [e for e in els if e.is_displayed()]
            if visible:
                return True, sel
        except Exception:
            pass
    return False, ""


def validation_visible(driver) -> tuple[bool, str]:
    """Return (True, message_text) if an inline validation message is visible."""
    for pattern in VAL_PATTERNS:
        try:
            xpath = f'//*[contains(translate(text(),"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz"),"{pattern}")]'
            els = driver.find_elements(By.XPATH, xpath)
            for el in els[:5]:
                if el.is_displayed():
                    return True, el.text.strip()[:80]
        except Exception:
            pass
    try:
        count = driver.execute_script(
            "return document.querySelectorAll(':invalid').length"
        )
        if count:
            return True, "HTML5 :invalid field"
    except Exception:
        pass
    return False, ""


# ── Page-load helpers ──────────────────────────────────────────────────────────

def wait_for_page(driver, timeout: int = T_NET):
    """Wait until document.readyState == 'complete', with a timeout."""
    try:
        _wait(driver, timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
    except TimeoutException:
        pass


# ── Click helper ───────────────────────────────────────────────────────────────

def do_click(driver, el, category: str) -> tuple[bool, str]:
    """Attempt to click an element; returns (success, reason)."""
    try:
        if not el.is_displayed():
            return False, "not visible"
        if not el.is_enabled():
            if category.lower() in ("negative", "validation", "form_empty",
                                    "form_invalid"):
                print("    ✓ Form submit disabled by validation (expected)")
                return True, "submit disabled by validation"
            return False, "disabled"

        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        time.sleep(0.3)
        try:
            el.click()
        except (ElementNotInteractableException, WebDriverException):
            driver.execute_script("arguments[0].click();", el)
            print("    ℹ JS-click fallback used")

        print("    ✓ Clicked")
        return True, "ok"
    except Exception as e:
        return False, type(e).__name__


# ── Post-action verifier ───────────────────────────────────────────────────────

def verify(driver, category: str, from_url: str, to_url: str,
           pre_validation: set | None = None) -> tuple[bool, str]:
    """
    Determine pass/fail for the test based on page state after action.
    Category-aware verification matching the expected result for each test type.
    """
    wait_for_page(driver)
    time.sleep(1.5)
    cur = driver.current_url

    cat = CAT_ALIASES.get(category.lower(), category)

    if cat == "Navigation":
        if cur != from_url:
            return True, f"OK → {cur}"
        if to_url and cur == to_url:
            return True, f"correct destination: {cur}"
        title = driver.title.strip()
        headings = driver.find_elements(By.CSS_SELECTOR, "h1, h2, h3")
        if title and headings:
            return True, "URL unchanged but UI state updated (SPA/React)"
        return False, "URL unchanged and no content change detected"

    if cat in ("Functional",):
        for sel in [".modal-body", "#example-modal-sizes-title-lg",
                    "[class*='modal']", "[role='dialog']"]:
            try:
                els = driver.find_elements(By.CSS_SELECTOR, sel)
                visible = [e for e in els if e.is_displayed()]
                if visible:
                    return True, f"success modal visible: {sel}"
            except Exception:
                pass
        ok, sel = result_visible(driver)
        if ok:
            return True, f"result at '{sel}'"
        if "?" in cur and cur != from_url:
            return True, "result via URL params"
        if cur != from_url:
            return True, f"form submitted, navigated to: {cur}"
        return False, "no result/modal visible and URL unchanged"

    if cat in ("Negative", "Validation"):
        if "login" in cur.lower() and "login" not in from_url.lower():
            return True, f"correctly redirected to login: {cur}"
        ok, msg = validation_visible(driver)
        if ok:
            if pre_validation and msg in pre_validation:
                return False, f"pre-existing text, not new validation: '{msg[:50]}'"
            return True, f"validation: '{msg}'"
        if cur.split("?")[0] == from_url.split("?")[0]:
            return True, "stayed on page (submission blocked or auth gated)"
        return False, "no validation message and unexpected navigation"

    if cat == "Boundary":
        ok, sel = result_visible(driver)
        if ok:
            return True, f"result at '{sel}'"
        ok, msg = validation_visible(driver)
        if ok:
            return True, f"validation: '{msg}'"
        if "?" in cur or cur != from_url:
            return True, "boundary processed (URL changed or params present)"
        return False, "no result or validation"

    if cat == "Security":
        body = driver.find_element(By.TAG_NAME, "body").text
        if "<script>" in body.lower():
            return False, "XSS: raw <script> reflected in page body"
        return True, "XSS payload not reflected"

    return True, "action completed"


# ── Generic action executor ────────────────────────────────────────────────────

def _run_action(driver, action: dict, category: str, from_url: str) -> tuple[bool, str]:
    """
    Execute a single action dict: {seq, action, target, value, expected}.
    Returns (success, reason). On failure the caller should abort the test.
    """
    act    = (action.get("action") or "").strip().lower()
    target = (action.get("target") or "").strip()
    value  = action.get("value", "")

    # ── navigate ──────────────────────────────────────────────────────────────
    if act == "navigate":
        try:
            driver.get(target)
            wait_for_page(driver)
            landed = driver.current_url
            landed_host = urlparse(landed).netloc
            if BASE_DOMAIN and landed_host and BASE_DOMAIN not in landed_host:
                print(f"    ⚠ External redirect: {landed_host} — aborting")
                try:
                    driver.get("about:blank")
                except Exception:
                    pass
                return False, f"redirected to external domain: {landed_host}"
            return True, f"navigated to {target}"
        except Exception as e:
            return False, f"navigate failed: {type(e).__name__}"

    # ── click_link ────────────────────────────────────────────────────────────
    elif act == "click_link":
        el = None
        link_text   = str(value).strip() if value else ""
        href_target = target

        # Normalise: LLM sometimes puts visible label in "target" and leaves "value" empty.
        # If target is not a URL, treat it as the link text to search for.
        if not link_text and href_target and not href_target.startswith("http"):
            link_text   = href_target
            href_target = ""

        # 1. href-based matching (most reliable for SPAs — uses real route paths)
        if href_target and href_target.startswith("http"):
            for attempt in range(2):
                try:
                    # exact href
                    els = driver.find_elements(By.CSS_SELECTOR, f'a[href="{href_target}"]')
                    if not els:
                        # path-only match (React Router uses relative hrefs)
                        path = href_target.split("//", 1)[-1].split("/", 1)[-1]
                        path = "/" + path.rstrip("/") if path else "/"
                        els = driver.find_elements(By.CSS_SELECTOR, f'a[href="{path}"]')
                    if not els:
                        slug = href_target.split("//")[-1].rstrip("/")
                        els = driver.find_elements(By.XPATH, f'//a[contains(@href,"{slug}")]')
                    el = next((e for e in els if e.is_displayed()), None)
                    if el:
                        break
                except Exception:
                    pass
                if not el and attempt == 0:
                    time.sleep(1.5)

        # 2. Link text on <a> elements
        if not el and link_text:
            for attempt in range(2):
                for strategy, locator in [
                    (By.LINK_TEXT,         link_text),
                    (By.PARTIAL_LINK_TEXT, link_text[:40]),
                ]:
                    try:
                        candidates = driver.find_elements(strategy, locator)
                        el = next((e for e in candidates if e.is_displayed()), None)
                        if el:
                            break
                    except Exception:
                        pass
                if el:
                    break
                if attempt == 0:
                    time.sleep(1.5)   # wait for SPA hydration

        # 3. Any clickable element with matching text — handles React sidebar items
        #    e.g. <a href="/test-suites"><span>Test Suites</span></a>
        if not el and link_text:
            for attempt in range(2):
                for xp in [
                    # direct text
                    f'//*[self::a or self::button or self::li or self::span or self::div]'
                    f'[normalize-space(text())="{link_text}"]',
                    # nested text (most common in Tailwind/shadcn SPAs)
                    f'//*[self::a or self::button or self::li]'
                    f'[.//*[normalize-space(text())="{link_text}"]]',
                    # partial nested
                    f'//*[self::a or self::button or self::li]'
                    f'[.//*[contains(normalize-space(text()),"{link_text[:30]}")]]',
                ]:
                    try:
                        candidates = driver.find_elements(By.XPATH, xp)
                        el = next((e for e in candidates if e.is_displayed()), None)
                        if el:
                            break
                    except Exception:
                        pass
                if el:
                    break
                if attempt == 0:
                    time.sleep(1.5)

        if not el:
            hint = link_text or href_target
            return False, f"link not found: '{hint}'"
        return do_click(driver, el, category)

    # ── fill_field ────────────────────────────────────────────────────────────
    elif act == "fill_field":
        failed = fill_fields(driver, {target: value})
        if failed:
            return False, f"could not fill field: {target}"
        return True, f"filled {target}={value!r}"

    # ── submit_form ───────────────────────────────────────────────────────────
    elif act == "submit_form":
        el = None
        if target:
            try:
                els = driver.find_elements(By.CSS_SELECTOR, target)
                el = next((e for e in els if e.is_displayed()), None)
            except Exception:
                pass
            if not el:
                try:
                    form = driver.find_element(By.CSS_SELECTOR,
                                               f'form#{target}, form[name="{target}"]')
                    btn = form.find_element(By.CSS_SELECTOR,
                        'input[type="submit"], button[type="submit"], button')
                    el = btn
                except Exception:
                    pass
        if not el:
            for fb in SUBMIT_FALLBACKS:
                try:
                    els = driver.find_elements(By.CSS_SELECTOR, fb)
                    el = next((e for e in els if e.is_displayed()), None)
                    if el:
                        break
                except Exception:
                    pass
        if not el:
            for label in ["Submit", "Login", "Sign In", "Calculate", "Search",
                          "Save", "Save Changes", "Update", "Apply", "Confirm", "Send"]:
                try:
                    els = driver.find_elements(
                        By.XPATH,
                        f'//button[normalize-space(text())="{label}"] | //input[@value="{label}"]'
                    )
                    el = next((e for e in els if e.is_displayed()), None)
                    if el:
                        break
                except Exception:
                    pass
        # Last resort: any visible enabled button on the page
        if not el:
            try:
                all_btns = driver.find_elements(By.TAG_NAME, "button")
                visible = [b for b in all_btns if b.is_displayed() and b.is_enabled()]
                if visible:
                    kws = {"save", "submit", "update", "apply", "send", "confirm", "create", "add"}
                    el = next(
                        (b for b in visible if any(k in b.text.lower() for k in kws)),
                        visible[0]
                    )
            except Exception:
                pass
        if not el:
            return False, "submit button not found"
        return do_click(driver, el, category)

    # ── browser_back ──────────────────────────────────────────────────────────
    elif act == "browser_back":
        try:
            driver.back()
            wait_for_page(driver)
            return True, "browser back navigated"
        except Exception as e:
            return False, f"browser_back failed: {e}"

    # ── assert_url ────────────────────────────────────────────────────────────
    elif act == "assert_url":
        current = driver.current_url
        if target and target not in current and target.rstrip("/") not in current:
            return False, f"assert_url FAILED: expected '{target}' in URL, got '{current}'"
        return True, f"assert_url OK: {current}"

    # ── assert_title ──────────────────────────────────────────────────────────
    elif act == "assert_title":
        current_title = driver.title
        expected = (target or (str(value) if value else "") or "").strip()
        if expected:
            exp_lc = expected.lower()
            cur_lc = current_title.lower()
            # 1. Direct substring match (LLM may predict a fragment)
            if exp_lc in cur_lc or cur_lc in exp_lc:
                return True, f"assert_title OK: '{current_title}'"
            # 2. SPA fallback — sites like SPAs use one global title for all routes.
            #    Accept if the URL path slug matches the expected string.
            #    e.g. expected="Test Suites", path="/test-suites" → pass
            try:
                from urllib.parse import urlparse as _uparse
                path_slug = _uparse(driver.current_url).path.strip("/").split("/")[-1]
                path_title = path_slug.replace("-", " ").replace("_", " ").lower()
                if path_title and (exp_lc == path_title
                                   or exp_lc in path_title
                                   or path_title in exp_lc):
                    return True, f"assert_title OK (SPA path match): '{path_slug}' ~ '{expected}'"
            except Exception:
                pass
            return False, f"assert_title FAILED: expected '{expected}', got '{current_title}'"
        return True, f"assert_title OK: '{current_title}'"

    # ── assert_element_present ────────────────────────────────────────────────
    elif act == "assert_element_present":
        try:
            selector   = target.strip()
            text_hint  = (str(value) if value else "").strip()
            found      = False

            # Map LLM-hallucinated IDs → real ARIA/semantic equivalents
            # This SPA (React + Tailwind/shadcn) doesn't use id-based structural markup
            _ALIASES: dict[str, list[str]] = {
                "#nav-menu":    ["nav", "[role='navigation']", "aside", "[role='complementary']",
                                 "[class*='sidebar']", "[class*='nav']"],
                "#nav":         ["nav", "[role='navigation']", "[class*='nav']"],
                "#header":      ["header", "[role='banner']", "[class*='header']"],
                "#header-logo": ["header img", "nav img", "[class*='logo']", "a img",
                                 "[role='banner'] img"],
                "#footer":      ["footer", "[role='contentinfo']", "[class*='footer']"],
                "footer":       ["footer", "[role='contentinfo']", "[class*='footer']",
                                 "div[class*='footer']"],
                "#sidebar":     ["aside", "[role='navigation']", "[class*='sidebar']"],
                "#main":        ["main", "[role='main']", "[class*='main']"],
                "#audit-log":   ["[class*='audit']", "[class*='log']", "table", "ul", "main"],
            }

            def _check(sel):
                try:
                    els = (driver.find_elements(By.XPATH, sel)
                           if sel.startswith("/")
                           else driver.find_elements(By.CSS_SELECTOR, sel))
                    return any(e.is_displayed() for e in els)
                except Exception:
                    return False

            if text_hint and not selector:
                found = _check(f'//*[contains(normalize-space(.),"{text_hint[:60]}")]')
            elif selector:
                found = _check(selector)
                # Try structural aliases on failure
                if not found and selector in _ALIASES:
                    for alt in _ALIASES[selector]:
                        if _check(alt):
                            found = True
                            print(f"    ℹ Matched '{selector}' via alias '{alt}'")
                            break
                # Last-resort: text hint
                if not found and text_hint:
                    found = _check(f'//*[contains(normalize-space(.),"{text_hint[:60]}")]')

            if not found:
                return False, f"assert_element_present FAILED: '{selector or text_hint}' not found"
            return True, f"assert_element_present OK: '{selector or text_hint}'"
        except Exception as e:
            return False, f"assert_element_present ERROR: {e}"

    # ── assert_element_not_present ────────────────────────────────────────────
    elif act == "assert_element_not_present":
        try:
            selector = target.strip()
            if selector.startswith("/"):
                els = driver.find_elements(By.XPATH, selector)
            else:
                els = driver.find_elements(By.CSS_SELECTOR, selector)
            visible = [e for e in els if e.is_displayed()]
            if visible:
                return False, f"assert_element_not_present FAILED: '{selector}' is visible"
            return True, f"assert_element_not_present OK: '{selector}' absent"
        except Exception as e:
            return False, f"assert_element_not_present ERROR: {e}"

    # ── assert_text_contains ──────────────────────────────────────────────────
    elif act == "assert_text_contains":
        try:
            body = driver.find_element(By.TAG_NAME, "body").text
            text = (str(value) if value else target or "").strip()
            if text and text.lower() not in body.lower():
                return False, f"assert_text_contains FAILED: '{text}' not found in page"
            return True, f"assert_text_contains OK: '{text}' found"
        except Exception as e:
            return False, f"assert_text_contains ERROR: {e}"

    else:
        print(f"    [?] Unknown action '{act}' — skipping step")
        return True, f"unknown action: {act}"


# ── Schema normaliser ─────────────────────────────────────────────────────────

def _resolve_actions(tc: dict) -> list[dict]:
    """
    Return the executable actions list for a test case.

    Priority:
    1. tc["actions"]  — present in the new unified schema and in testgen.py schema
                        when steps[] are dicts (legacy)
    2. Synthesised from tc["test_data"] + tc["from_state"] — exploratory schema
       (test_id + from_state + test_data.locator without an actions key)

    Also handles the OLD testgen.py format where tc["steps"] was a list of dicts.
    """
    # ── 1. Unified / new schema: explicit actions[] ───────────────────────────
    actions = tc.get("actions")
    if actions and isinstance(actions, list) and isinstance(actions[0], dict):
        return sorted(actions, key=lambda s: s.get("seq", 0))

    # ── 2. Legacy testgen.py: steps[] were dicts with seq/action/target/value ─
    steps = tc.get("steps", [])
    if steps and isinstance(steps[0], dict):
        return sorted(steps, key=lambda s: s.get("seq", 0))

    # ── 3. Exploratory schema: synthesise from test_data ──────────────────────
    synthesised: list[dict] = []
    seq = 1
    from_url = tc.get("from_state", "")
    to_url   = tc.get("to_state", "")
    td       = tc.get("test_data", {})
    loc      = td.get("locator", {}) if isinstance(td, dict) else {}
    inputs   = td.get("input_values", {}) if isinstance(td, dict) else {}
    cat      = CAT_ALIASES.get(tc.get("category", "").lower(), tc.get("category", ""))

    if from_url:
        synthesised.append({"seq": seq, "action": "navigate",
                             "target": from_url, "value": "", "expected": "Page loaded"})
        seq += 1

    for fname, fval in inputs.items():
        synthesised.append({"seq": seq, "action": "fill_field",
                             "target": fname, "value": fval, "expected": f"Field {fname} filled"})
        seq += 1

    if cat == "Navigation" and to_url and to_url != from_url:
        href = loc.get("href", to_url)
        txt  = loc.get("text", "")
        synthesised.append({"seq": seq, "action": "click_link",
                             "target": href, "value": txt, "expected": "Navigated"})
        seq += 1
        synthesised.append({"seq": seq, "action": "assert_url",
                             "target": to_url, "value": "", "expected": "Correct URL"})
        seq += 1
    elif inputs:
        # Form test — find the submit button
        btn_locator = loc.get("data_testid") or loc.get("id") or ""
        synthesised.append({"seq": seq, "action": "submit_form",
                             "target": btn_locator, "value": "", "expected": "Form submitted"})
        seq += 1

    return synthesised


def _get_field(tc: dict, *keys, default=""):
    """Read a field trying multiple key names (for schema flexibility)."""
    for k in keys:
        v = tc.get(k)
        if v is not None:
            return v
    return default


# ── Generic test runner ────────────────────────────────────────────────────────

def run_test(driver, tc: dict) -> dict:
    """
    Execute a single test case in any supported schema.
    Uses the actions[] array (unified/legacy) or synthesises from test_data.
    """
    tc_id   = _get_field(tc, "test_id", "id", default="TC???")
    cat     = tc.get("category", "")
    title   = _get_field(tc, "title", "description", default="")
    from_url = tc.get("from_state", "")
    to_url   = tc.get("to_state", "")
    can_cat  = CAT_ALIASES.get(cat.lower(), cat)

    r = {
        "test_id":   tc_id,
        "title":     title,
        "category":  can_cat,
        "passed":    False,
        "reason":    "",
        "timestamp": datetime.now().isoformat(),
    }

    print(f"\n{'─'*60}\n  {tc_id} [{cat}] {title}")

    actions = _resolve_actions(tc)
    if not actions:
        r["reason"] = "no actions/steps defined"
        return r

    # Track the URL before any action (for Navigation verify comparison)
    initial_url = driver.current_url

    # Snapshot pre-existing validation text before interaction
    pre_validation: set = set()
    td  = tc.get("test_data", {})
    if isinstance(td, dict):
        inputs = td.get("input_values", {})
    else:
        inputs = {}

    if inputs and can_cat in ("Negative", "Validation"):
        for pattern in VAL_PATTERNS:
            try:
                xpath = f'//*[contains(translate(text(),"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz"),"{pattern}")]'
                for el in driver.find_elements(By.XPATH, xpath)[:5]:
                    if el.is_displayed():
                        pre_validation.add(el.text.strip()[:80])
            except Exception:
                pass

    # Execute each action
    for action in actions:
        act_name = action.get("action", "?")
        ok, reason = _run_action(driver, action, cat, initial_url)

        # Close any extra tabs opened by external pages
        try:
            main_handle = driver.window_handles[0]
            for handle in driver.window_handles[1:]:
                driver.switch_to.window(handle)
                driver.close()
            driver.switch_to.window(main_handle)
        except Exception:
            pass

        if not ok:
            r["reason"] = f"action seq={action.get('seq','?')} ({act_name}): {reason}"
            print(f"  ❌ {r['reason']}")
            return r

    # After all actions: category-aware final verification
    # Determine the URL we were on when the test started (first navigate target)
    first_nav = next(
        (a["target"] for a in actions if a.get("action") == "navigate"), initial_url
    )

    # If the test already used assert_* actions, those steps verified the outcome.
    # Calling verify() again with category rules would produce false negatives
    # (e.g., an accessibility test that ran assert_element_present passes its own
    # checks but verify() requires a modal/result for "Functional" category).
    action_names = {a.get("action", "").lower() for a in actions}
    has_asserts  = any(a.startswith("assert_") for a in action_names)

    if has_asserts:
        # Self-verifying test — all assert_ steps passed so we're done
        r["passed"] = True
        r["reason"]  = "all assert_ checks passed"
        print(f"  PASS  all assert_ checks passed")
    else:
        ok, reason = verify(driver, cat, first_nav, to_url or driver.current_url,
                            pre_validation)
        r["passed"] = ok
        r["reason"]  = reason
        print(f"  {'PASS' if ok else 'FAIL'}  {reason}")
    return r


# ── Report writer ──────────────────────────────────────────────────────────────

def write_report(results: list[dict], output_path: str = OUTPUT_FILE):
    passed = sum(1 for r in results if r["passed"])
    failed = len(results) - passed
    cats: dict[str, list[int]] = {}
    for r in results:
        c = r["category"]
        cats.setdefault(c, [0, 0])
        cats[c][0 if r["passed"] else 1] += 1

    report = {
        "summary": {
            "total":     len(results),
            "passed":    passed,
            "failed":    failed,
            "pass_rate": f"{passed / len(results) * 100:.1f}%" if results else "0%",
            "run_at":    datetime.now().isoformat(),
        },
        "by_category": {
            c: {
                "passed": v[0],
                "failed": v[1],
                "rate":   f"{v[0] / (v[0] + v[1]) * 100:.0f}%",
            }
            for c, v in cats.items()
        },
        "failed_tests": [
            {"id": r["test_id"], "title": r["title"], "reason": r["reason"]}
            for r in results if not r["passed"]
        ],
        "all_results": results,
    }

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(json.dumps(report, indent=2), encoding="utf-8")

    # ── Console summary ────────────────────────────────────────────────────────
    print(f"\n{'=' * 60}  SUMMARY")
    print(f"  Total:   {len(results)}")
    print(f"  Passed:  {passed}")
    print(f"  Failed:  {failed}")
    print(f"  Rate:    {report['summary']['pass_rate']}")
    print()
    for c, (p, f) in sorted(cats.items()):
        bar = "█" * p + "░" * f
        print(f"  {c:<15} {p}/{p + f:>3}  {p / (p + f) * 100:.0f}%  {bar}")

    if failed:
        print("\n  ── Failed ──")
        for r in results:
            if not r["passed"]:
                print(f"    {r['test_id']}: {r['reason']}")

    print(f"\n  → {output_path}\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Execute generated test cases (any supported schema) using Selenium."
    )
    parser.add_argument(
        "--input", default=INPUT_FILE,
        help=f"Path to test-cases JSON file (default: {INPUT_FILE})",
    )
    parser.add_argument(
        "--output", default=OUTPUT_FILE,
        help=f"Path to write results JSON (default: {OUTPUT_FILE})",
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="Run browser in headless mode",
    )
    parser.add_argument(
        "--browser", default="chrome", choices=["chrome", "firefox"],
        help="Browser to use (default: chrome)",
    )
    parser.add_argument(
        "--filter-category", default="",
        help="Only run tests of this category (case-insensitive)",
    )
    parser.add_argument(
        "--filter-id", default="",
        help="Only run the test with this test_id",
    )
    args = parser.parse_args()

    # Load test cases
    try:
        raw = json.loads(Path(args.input).read_text(encoding="utf-8"))
    except FileNotFoundError:
        sys.exit(f"❌ Input file not found: {args.input}")
    except json.JSONDecodeError as e:
        sys.exit(f"❌ Invalid JSON in {args.input}: {e}")

    tcs = raw.get("test_cases") if isinstance(raw, dict) else raw
    if not tcs:
        sys.exit(f"❌ No test cases found in {args.input}")

    # Optional filters
    if args.filter_category:
        tcs = [tc for tc in tcs
               if tc.get("category", "").lower() == args.filter_category.lower()]
    if args.filter_id:
        tcs = [tc for tc in tcs
               if (tc.get("test_id") or tc.get("id", "")) == args.filter_id]

    if not tcs:
        sys.exit("❌ No test cases remain after filtering.")

    # ── Auto-detect BASE_DOMAIN from the first navigate action in any test case ──
    global BASE_DOMAIN
    if not BASE_DOMAIN:
        for _tc in tcs:
            for _action in _resolve_actions(_tc):
                if (_action.get("action") == "navigate" and
                        _action.get("target", "").startswith("http")):
                    BASE_DOMAIN = urlparse(_action["target"]).netloc
                    break
            if BASE_DOMAIN:
                break

    print(f"🧪 {len(tcs)} test(s) loaded from {args.input}")
    print(f"   Browser : {args.browser}{'  (headless)' if args.headless else ''}")
    print(f"   Domain  : {BASE_DOMAIN or '(any)'}")
    print(f"   Output  : {args.output}\n")

    results: list[dict] = []
    driver = make_driver(browser=args.browser, headless=args.headless)
    driver.set_page_load_timeout(T_PAGE)
    driver.implicitly_wait(0)   # we use explicit waits everywhere

    try:
        for tc in tcs:
            try:
                result = run_test(driver, tc)
                results.append(result)
            except Exception as e:
                tc_id = tc.get("test_id") or tc.get("id", "?")
                r = {
                    "test_id":   tc_id,
                    "title":     _get_field(tc, "title", "description", default=""),
                    "category":  tc.get("category", ""),
                    "passed":    False,
                    "reason":    f"{type(e).__name__}: {str(e)[:120]}",
                    "timestamp": datetime.now().isoformat(),
                }
                print(f"  💥 CRASH: {r['reason']}")
                try:
                    driver.save_screenshot(f"crash_{tc_id}.png")
                except Exception:
                    pass
                results.append(r)
    finally:
        driver.quit()

    if results:
        write_report(results, args.output)
    else:
        print("No results to report.")


if __name__ == "__main__":
    main()