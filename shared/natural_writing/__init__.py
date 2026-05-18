"""Natural-writing service - public API for cross-SaaS naturalness layer.

Two main entry points :

  >>> from shared.natural_writing import get_prompt_section, sanitize
  >>>
  >>> # 1. Inject humanizer rules into an LLM system prompt
  >>> system_prompt = base_prompt + "\\n\\n" + get_prompt_section(mode='full')
  >>>
  >>> # 2. Clean LLM output after generation (per-mode gates)
  >>> cleaned_html = sanitize(raw_html, gates=get_mode('full').sanitizer_gates,
  ...                         brand_content=brand_text)

The 4 supported modes are documented in modes.py :
  - "full"    : article, newsletter (long-form)
  - "compact" : FAQ, brief, persona, trust source discovery
  - "chat"    : in-app chatbot replies
  - "tooltip" : UI copy (used offline by the static-copy audit script)

Cache for fetched anti-AI patterns lives at /app/cache/natural_writing/humanizer/
(7-day TTL, refreshed automatically). Override with NW_CACHE_DIR env var.
"""

from .modes import (
    MODES,
    ModeConfig,
    get_mode,
    FULL,
    COMPACT,
    CHAT,
    TOOLTIP,
)
from .prompts import get_prompt_section
from .sanitizers import (
    sanitize,
    strip_placeholder_brackets,
    dedupe_sources_aside,
    relinkify_review_tables,
    strip_fake_experts,
    remove_anonymous_blockquotes,
)
from .humanizer import (
    get_humanizer_rules,
    get_humanizer_prompt,        # original API kept for back-compat with seo_llm callers
    format_humanizer_prompt_section,
)

__all__ = [
    # Mode config
    "MODES",
    "ModeConfig",
    "get_mode",
    "FULL", "COMPACT", "CHAT", "TOOLTIP",
    # Prompt assembly
    "get_prompt_section",
    # Sanitizers
    "sanitize",
    "strip_placeholder_brackets",
    "dedupe_sources_aside",
    "relinkify_review_tables",
    "strip_fake_experts",
    "remove_anonymous_blockquotes",
    # Humanizer (lower-level, for advanced callers)
    "get_humanizer_rules",
    "get_humanizer_prompt",
    "format_humanizer_prompt_section",
]
