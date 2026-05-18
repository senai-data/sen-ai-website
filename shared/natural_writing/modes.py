"""Per-feature mode definitions for the natural-writing service.

The humanizer prompt section + sanitizer gates are tuned per surface so a
2000-word article gets the full anti-AI-detection treatment while a 12-word
tooltip only gets the minimal vocabulary blacklist (the "vary sentence
length 5-8 / 25-35" rule is absurd on a button label).

Modes are config dataclasses, not enums - they're values, not types, and the
caller passes the mode key as a string to `get_prompt_section(mode='full')`.
"""

from __future__ import annotations
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModeConfig:
    """Per-surface natural-writing budget.

    max_patterns / max_vocab control the size of the humanizer prompt section
    injected into LLM system prompts. Bigger = stronger anti-AI signal but
    longer prompt (more tokens, more cost).

    include_negative_instructions toggles the "NO signposting / NO hedging /
    NO formula" rules block. For very short outputs (chat reply, tooltip),
    these rules can be counter-productive (a tooltip MUST start with a
    direct statement - that's already what we want, no signposting risk).

    sanitizer_gates lists which post-processing passes run on the LLM
    output. Article = all gates ; chat = brackets only ; tooltip = none
    (outputs are too short for sanitization to matter).
    """
    name: str
    max_patterns: int      # 0 = skip the GitHub patterns section entirely
    max_vocab: int         # 0 = skip the Wikipedia vocab section
    include_negative_instructions: bool
    sanitizer_gates: tuple = field(default_factory=tuple)
    description: str = ""


# Full mode - long-form article generation (article, newsletter).
FULL = ModeConfig(
    name="full",
    max_patterns=30,
    max_vocab=40,
    include_negative_instructions=True,
    sanitizer_gates=(
        "brackets",          # strip [lowercase_word] placeholder leaks
        "sources_aside",     # dedupe inline <aside class="sources"> block
        "review_tables",     # relinkify "Voir les avis" in review tables
        "fake_experts",      # remove hallucinated Dr/Pr names
        "anonymous_blocks",  # delete vague-attribution blockquotes
    ),
    description="Long-form article / newsletter. Full anti-AI-detection treatment + all sanitizer gates.",
)

# Compact mode - short structured LLM outputs (FAQ, brief, persona, trust sources).
COMPACT = ModeConfig(
    name="compact",
    max_patterns=15,
    max_vocab=20,
    include_negative_instructions=True,
    sanitizer_gates=(
        "brackets",
        "fake_experts",
        "anonymous_blocks",
    ),
    description="FAQ, brief generators, persona questions, trust source discovery. Lighter prompt budget, key sanitizers only.",
)

# Chat mode - conversational chatbot responses.
CHAT = ModeConfig(
    name="chat",
    max_patterns=8,
    max_vocab=10,
    include_negative_instructions=False,  # "vary sentence length" makes no sense in short replies
    sanitizer_gates=("brackets",),         # only the obvious placeholder leak
    description="In-app chatbot replies. Minimal prompt footprint to keep latency + cost low ; conversational nature is naturally less AI-detectable than long-form.",
)

# Tooltip mode - UI copy (tooltips, button labels, error messages, onboarding).
# Mostly used OFFLINE by the static-copy audit script (NW.6 deferred), not at
# runtime - keep here for the lint hook and future audit reuse.
TOOLTIP = ModeConfig(
    name="tooltip",
    max_patterns=0,
    max_vocab=15,
    include_negative_instructions=False,
    sanitizer_gates=(),
    description="UI tooltips, button labels, error messages. Vocabulary blacklist only - patterns and structural rules don't apply at <50-char length.",
)


MODES: dict[str, ModeConfig] = {
    "full":    FULL,
    "compact": COMPACT,
    "chat":    CHAT,
    "tooltip": TOOLTIP,
}


def get_mode(name: str) -> ModeConfig:
    """Lookup mode config by name. Raises KeyError on unknown name -
    callers should know which mode they want (no silent fallback)."""
    if name not in MODES:
        raise KeyError(
            f"Unknown natural_writing mode '{name}' - "
            f"valid modes: {sorted(MODES.keys())}"
        )
    return MODES[name]
