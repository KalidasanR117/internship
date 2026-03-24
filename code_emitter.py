"""
code_emitter.py — Convert parsed JSON test cases into a runnable Selenium pytest file.
This module is purely deterministic — no LLM calls here.
"""
from __future__ import annotations
import re
import textwrap
from typing import Any


# ── Helpers ────────────────────────────────────────────────────────────────────

def _safe_name(text: str) -> str:
    """Turn a description string into a valid Python identifier."""
    s = text.lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = s.strip("_")
    return s[:60] or "test"


def _esc(value: Any) -> str:
    """Escape a value for embedding in a Python string literal."""
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


# ── Step translator ────────────────────────────────────────────────────────────

def _emit_step(step: dict, indent: str = "        ") -> list[str]:
    """Translate a single step dict into one or more lines of Selenium Python."""
    action   = step.get("action", "")
    target   = step.get("target", "")
    value    = step.get("value", "")
    expected = step.get("expected", "")
    lines    = []

    if action == "navigate":
        lines.append(f'{indent}driver.get("{_esc(target)}")')

    elif action == "click_link":
        lines.append(
            f'{indent}driver.find_element(By.LINK_TEXT, "{_esc(target)}").click()'
        )

    elif action == "fill_field":
        lines += [
            f'{indent}el = driver.find_element(By.NAME, "{_esc(target)}")',
            f'{indent}el.clear()',
            f'{indent}el.send_keys("{_esc(value)}")',
        ]

    elif action == "submit_form":
        if target:
            lines.append(
                f'{indent}driver.find_element(By.CSS_SELECTOR, "{_esc(target)}").click()'
            )
        else:
            lines += [
                f'{indent}submit = driver.find_element('
                f'By.CSS_SELECTOR, "input[type=\'submit\'], button[type=\'submit\']")',
                f'{indent}submit.click()',
            ]

    elif action == "assert_url":
        lines.append(
            f'{indent}assert "{_esc(expected)}" in driver.current_url, '
            f'"Expected URL to contain \'{_esc(expected)}\', '
            f'got: " + driver.current_url'
        )

    elif action == "assert_title":
        lines.append(
            f'{indent}assert driver.title == "{_esc(expected)}", '
            f'"Expected title \'{_esc(expected)}\', got: " + driver.title'
        )

    elif action == "assert_element_present":
        lines += [
            f'{indent}assert len(driver.find_elements(By.CSS_SELECTOR, "{_esc(target)}")) > 0, '
            f'"Element \'{_esc(target)}\' not found on page"',
        ]

    elif action == "assert_element_not_present":
        lines += [
            f'{indent}assert len(driver.find_elements(By.CSS_SELECTOR, "{_esc(target)}")) == 0, '
            f'"Element \'{_esc(target)}\' should not be present"',
        ]

    elif action == "assert_text_contains":
        lines += [
            f'{indent}body_text = driver.find_element(By.TAG_NAME, "body").text',
            f'{indent}assert "{_esc(expected)}" in body_text, '
            f'"Expected page to contain \'{_esc(expected)}\'"',
        ]

    else:
        lines.append(f'{indent}# TODO: unsupported action "{_esc(action)}" — target="{_esc(target)}"')

    return lines


# ── Class grouping ─────────────────────────────────────────────────────────────

def _group_by_category(test_cases: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for tc in test_cases:
        cat = tc.get("category", "other").lower()
        groups.setdefault(cat, []).append(tc)
    return groups


_CLASS_NAMES = {
    "navigation":   "TestNavigation",
    "form_valid":   "TestFormValid",
    "form_invalid": "TestFormInvalid",
    "form_empty":   "TestFormEmpty",
    "boundary":     "TestBoundary",
    "negative":     "TestNegative",
    "security":     "TestSecurity",
    "other":        "TestMisc",
}


# ── Main emitter ───────────────────────────────────────────────────────────────

CONFTEST = '''\
# conftest.py — auto-generated pytest fixture for Selenium
import pytest
from selenium import webdriver
from selenium.webdriver.chrome.options import Options


@pytest.fixture(scope="function")
def driver():
    opts = Options()
    # Uncomment the next line to run headlessly:
    # opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    d = webdriver.Chrome(options=opts)
    d.implicitly_wait(5)
    yield d
    d.quit()
'''


def emit_pytest_file(test_cases: list[dict]) -> str:
    """
    Build the full content of a pytest + Selenium file from a list of test case dicts.
    Returns a string — the caller writes it to disk.
    """
    groups = _group_by_category(test_cases)

    lines: list[str] = [
        "# test_suite.py — Auto-generated Selenium pytest suite",
        "# DO NOT EDIT MANUALLY — regenerate with testgen.py",
        "",
        "import pytest",
        "from selenium.webdriver.common.by import By",
        "from selenium.webdriver.support.ui import WebDriverWait",
        "from selenium.webdriver.support import expected_conditions as EC",
        "",
        "",
    ]

    for category, tests in groups.items():
        class_name = _CLASS_NAMES.get(category, f"Test{category.title()}")
        lines.append(f"class {class_name}:")
        lines.append(f'    """Category: {category}"""')
        lines.append("")

        for tc in tests:
            tc_id   = tc.get("id", "TCXXX")
            desc    = tc.get("description", "")
            priority = tc.get("priority", "medium")
            pre     = tc.get("preconditions", [])
            expected = tc.get("expected_result", "")
            src_t   = tc.get("source_transitions", [])

            method_name = f"test_{tc_id.lower()}_{_safe_name(desc)}"

            # Docstring
            lines.append(f"    def {method_name}(self, driver):")
            doc = textwrap.indent(
                textwrap.dedent(f"""\
                    \"\"\"
                    ID          : {tc_id}
                    Priority    : {priority}
                    Description : {desc}
                    Preconditions: {'; '.join(pre) if pre else 'None'}
                    Expected    : {expected}
                    Transitions : {', '.join(src_t)}
                    \"\"\"
                """),
                "        "
            )
            lines.append(doc.rstrip())

            # Steps
            steps = sorted(tc.get("steps", []), key=lambda s: s.get("seq", 0))
            if not steps:
                lines.append("        pass  # No steps defined")
            else:
                for step in steps:
                    step_lines = _emit_step(step)
                    lines.extend(step_lines)

            lines.append("")

        lines.append("")

    return "\n".join(lines)
