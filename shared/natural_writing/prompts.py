"""Per-mode prompt section assembly.

Thin wrapper around humanizer.format_humanizer_prompt_section() that picks
the right budget (max_patterns, max_vocab) + decides whether to include
the structural "RÈGLES D'ÉCRITURE NATURELLE" block based on the mode.

Single public entry point : `get_prompt_section(mode='full', language='fr')`.
Modes are defined in modes.py. All callers across the SaaS go through here.
"""

from __future__ import annotations
import logging

from .humanizer import (
    get_humanizer_rules,
    format_humanizer_prompt_section,
)
from .modes import get_mode

logger = logging.getLogger(__name__)


def get_prompt_section(mode: str = "full", language: str = "fr") -> str:
    """Return the formatted humanizer prompt section for the given mode.

    Args:
        mode: one of "full" (article, newsletter), "compact" (FAQ, briefs),
            "chat" (chatbot), "tooltip" (UI copy, offline audit only).
        language: target language. "fr" by default. The Wikipedia + GitHub
            sources are English-flavoured but the rule block is bilingual.

    Returns the prompt section ready to inject into an LLM system prompt.
    Empty string if the mode disables both patterns and vocabulary AND the
    negative instructions (tooltip mode with vocabulary=0 only would still
    return a small vocabulary block).
    """
    config = get_mode(mode)

    if config.max_patterns == 0 and config.max_vocab == 0:
        logger.debug(f"natural_writing: mode '{mode}' has zero budget - returning empty section")
        return ""

    rules = get_humanizer_rules(language)

    section = format_humanizer_prompt_section(
        rules,
        language=language,
        max_patterns=config.max_patterns,
        max_vocab=config.max_vocab,
    )

    # The full humanizer formatter always appends the "RÈGLES D'ÉCRITURE
    # NATURELLE" block. For chat / tooltip modes we want to drop it - those
    # rules ("vary sentence length 5-8 / 25-35", "no list-of-three") make
    # no sense on conversational replies or 12-char button labels.
    if not config.include_negative_instructions:
        section = _strip_negative_rules_block(section)

    return section


def _strip_negative_rules_block(section: str) -> str:
    """Remove the trailing 'RÈGLES D'ÉCRITURE NATURELLE' / 'RULES FOR NATURAL
    WRITING' block from the formatted section. Used by chat + tooltip modes
    where the structural rules don't apply to short outputs."""
    if not section:
        return section
    # The block is identified by its heading. Cut everything from that
    # heading onwards. If the heading isn't present (future humanizer
    # version changes), return the section unchanged - safer fallback.
    markers = (
        "## RÈGLES D'ÉCRITURE NATURELLE",
        "## RULES FOR NATURAL WRITING",
    )
    for marker in markers:
        idx = section.find(marker)
        if idx >= 0:
            return section[:idx].rstrip()
    return section
