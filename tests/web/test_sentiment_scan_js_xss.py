"""Source-grep test for the sentiment-scan viewer JS (Codex C2).

We can't run a real DOM here without jsdom (not in project), so the next-best
guard is a source check: the metadata block must NOT use `innerHTML` to
insert LLM-derived strings. The pre-C2 implementation built `metaEl.innerHTML`
by concatenating `decision.summary_1line` (Executive Summary text from the
LLM) directly into HTML, which would execute any `<img onerror>` smuggled in.

This test fails if a future edit re-introduces the pattern. It's not a real
DOM-level XSS test, but it would catch the original bug — and it's the only
form of automated guard available without adding a JS test runner.
"""
from __future__ import annotations

import re
from pathlib import Path


JS_PATH = Path(__file__).resolve().parents[2] / "web" / "frontend" / "sentiment-scan.js"


def _read_js() -> str:
    return JS_PATH.read_text(encoding="utf-8")


def test_js_file_exists():
    assert JS_PATH.exists(), f"sentiment-scan.js missing at {JS_PATH}"


def test_metadata_block_does_not_use_innerHTML_for_decision_fields():
    """The renderShell() metadata builder must NOT assign innerHTML with
    LLM-derived strings. Search for the specific anti-pattern that triggered
    Codex C2: `metaEl.innerHTML = ... + decision.summary_1line + ...`.
    """
    js = _read_js()
    # Anti-patterns we want to block:
    bad_patterns = [
        # Direct concatenation of LLM data into metaEl.innerHTML
        re.compile(r"metaEl\s*\.\s*innerHTML\s*=.*decision\."),
        re.compile(r"metaEl\s*\.\s*innerHTML\s*=.*meta\.name"),
        re.compile(r"metaEl\s*\.\s*innerHTML\s*=.*summary_1line"),
    ]
    for pat in bad_patterns:
        m = pat.search(js)
        assert m is None, (
            f"sentiment-scan.js metadata block uses innerHTML with LLM "
            f"data — pattern matched: {pat.pattern!r}"
        )


def test_metadata_block_uses_textContent_or_createTextNode():
    """Positive: the renderShell() metadata builder should use textContent
    or createTextNode for user/LLM-derived data."""
    js = _read_js()
    # The renderShell function should appear; grab a window around it
    idx = js.find("function renderShell")
    assert idx >= 0, "renderShell function not found in sentiment-scan.js"
    # Window: renderShell body until the next top-level function definition.
    body = js[idx:idx + 4000]
    assert (
        "createTextNode" in body or "textContent" in body
    ), "renderShell metadata block must use textContent / createTextNode"


def test_action_badge_uses_whitelisted_class_not_user_data():
    """The BUY/HOLD/SELL badge's className must come from a whitelist —
    never directly concatenated from `decision.action`."""
    js = _read_js()
    # The previous bug: 'badge.className = "badge " + decision.action' would
    # let an LLM-controlled value set arbitrary CSS classes. With the fix
    # we look up a whitelist map.
    # Anti-pattern: className built from decision.action via string concat.
    bad = re.search(
        r"className\s*=\s*['\"]badge\s*['\"]\s*\+\s*decision\.action",
        js,
    )
    assert bad is None, "action badge className uses LLM-derived string directly"


def test_summary_1line_not_concatenated_into_innerHTML():
    """A second, broader check: the literal string `summary_1line` must NOT
    appear on the right-hand side of any `.innerHTML =` assignment.
    """
    js = _read_js()
    # All `.innerHTML =` assignments and the next ~200 chars after them
    for m in re.finditer(r"\.innerHTML\s*=", js):
        window = js[m.start(): m.start() + 400]
        # End the window at the next `;` to avoid running past the assignment
        end = window.find(";")
        if end > 0:
            window = window[:end]
        assert "summary_1line" not in window, (
            f"summary_1line concatenated into an innerHTML assignment near "
            f"offset {m.start()} — re-introduces Codex C2"
        )
