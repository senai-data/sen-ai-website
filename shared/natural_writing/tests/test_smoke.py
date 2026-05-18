"""Smoke tests for shared/natural_writing/ public API.

Run inside a container that has the package mounted :
    docker exec senai-worker python -m pytest /app/shared/natural_writing/tests/

The tests below are intentionally offline-friendly : they exercise the
modes / prompts / sanitizers layers without forcing a live GitHub or
Wikipedia fetch (those hit the cached fixture if it exists, else fail
gracefully and the assertion just confirms the empty-result fallback).
"""

import sys
import os
import pytest

# Allow `from shared.natural_writing import ...` when tests are run from /app
sys.path.insert(0, "/app")


def test_modes_lookup():
    """Each documented mode resolves to a ModeConfig."""
    from shared.natural_writing import get_mode, MODES
    assert set(MODES.keys()) == {"full", "compact", "chat", "tooltip"}
    for name in MODES:
        cfg = get_mode(name)
        assert cfg.name == name
        assert cfg.max_patterns >= 0
        assert cfg.max_vocab >= 0


def test_unknown_mode_raises():
    """KeyError on unknown mode name - no silent fallback."""
    from shared.natural_writing import get_mode
    with pytest.raises(KeyError):
        get_mode("nonexistent")


def test_sanitizer_brackets():
    """Lowercase single-word bracket placeholder is stripped, others survive."""
    from shared.natural_writing import strip_placeholder_brackets
    out = strip_placeholder_brackets(
        '<p>Texte [flacon]. Marque [Avène]. Note [1], [Note 2].</p>'
    )
    assert '[flacon]' not in out
    assert 'flacon' in out
    assert '[Avène]' in out, "uppercase placeholder should survive"
    assert '[1]' in out, "digit footnote should survive"
    assert '[Note 2]' in out, "multi-word reference should survive"


def test_sanitizer_sources_aside():
    """<aside class='sources'> is stripped, body content survives."""
    from shared.natural_writing import dedupe_sources_aside
    out = dedupe_sources_aside(
        '<article><p>body</p>'
        '<aside class="sources"><h2>Sources</h2><ul><li>x</li></ul></aside>'
        '</article>'
    )
    assert '<aside' not in out
    assert '<p>body</p>' in out


def test_sanitizer_h2_sources_fallback():
    """Plain <h2>Sources</h2><ul> without wrapping aside is also stripped."""
    from shared.natural_writing import dedupe_sources_aside
    out = dedupe_sources_aside(
        '<article><p>body</p><h2>Sources</h2><ul><li>x</li></ul></article>'
    )
    assert '<h2>Sources</h2>' not in out
    assert '<p>body</p>' in out


def test_sanitizer_review_relinkify():
    """`Voir les avis` text-only cells are wrapped in <a href> when domain matches."""
    from shared.natural_writing import relinkify_review_tables
    html = (
        '<article>'
        '<p>link <a href="https://www.sephora.fr/p/123">sephora</a> '
        'and <a href="https://www.amazon.fr/d/456">amzn</a></p>'
        '<h2>Avis</h2><table>'
        '<tr><th>Plat</th><th>Note</th><th>Lien</th></tr>'
        '<tr><td>sephora.fr</td><td>4.7/5</td><td>Voir les avis</td></tr>'
        '<tr><td>amazon.fr</td><td>4.4/5</td><td>Voir les avis</td></tr>'
        '</table></article>'
    )
    out = relinkify_review_tables(html)
    # We started with 2 <a> tags ; after relinkify both rows should add one each.
    assert out.count('<a ') >= 4, f"both rows should have been relinkified - got {out.count('<a ')} <a>"


def test_sanitize_dispatcher():
    """sanitize() runs requested gates in order, skips unknown ones."""
    from shared.natural_writing import sanitize, get_mode
    html = (
        '<article><p>texte [flacon] ok</p>'
        '<aside class="sources"><ul><li>x</li></ul></aside></article>'
    )
    full_gates = get_mode("full").sanitizer_gates
    out = sanitize(html, gates=full_gates)
    assert '[flacon]' not in out
    assert '<aside' not in out
    # Bogus gate name - skipped silently
    out2 = sanitize(html, gates=("brackets", "nonexistent_gate"))
    assert '[flacon]' not in out2


def test_sanitize_empty_inputs():
    """sanitize() handles empty / None / whitespace gracefully."""
    from shared.natural_writing import sanitize
    assert sanitize("", gates=("brackets",)) == ""
    assert sanitize(None, gates=("brackets",)) is None


def test_prompt_section_empty_modes():
    """When both budgets are 0, get_prompt_section returns empty string."""
    from shared.natural_writing import get_prompt_section, ModeConfig, MODES
    # Hack a zero-budget mode in memory for the test
    MODES["__zero"] = ModeConfig(
        name="__zero", max_patterns=0, max_vocab=0,
        include_negative_instructions=False,
    )
    try:
        out = get_prompt_section(mode="__zero")
        assert out == ""
    finally:
        MODES.pop("__zero", None)


def test_prompt_section_chat_no_negative_block():
    """Chat mode strips the 'RÈGLES D'ÉCRITURE NATURELLE' tail block."""
    from shared.natural_writing import get_prompt_section
    section = get_prompt_section(mode="chat", language="fr")
    # Section may be empty if external fetch failed AND no cache - that's OK
    # for this test, we just check the negative-rules header is absent.
    assert "RÈGLES D'ÉCRITURE NATURELLE" not in section


def test_prompt_section_full_includes_negative_block():
    """Full mode includes the negative-rules tail block when cache is warm."""
    from shared.natural_writing import get_prompt_section
    section = get_prompt_section(mode="full", language="fr")
    # If cache is cold AND external fetch failed, section may be empty.
    # When non-empty, the negative block must be present in full mode.
    if section:
        assert "RÈGLES D'ÉCRITURE NATURELLE" in section, (
            "full mode must surface the negative-rules block"
        )
