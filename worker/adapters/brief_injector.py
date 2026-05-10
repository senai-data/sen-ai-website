"""
Shared utilities to format briefs as prompt context blocks.

Two distinct briefs, two distinct purposes :

1. **Domain brief** (per-scan, stored in `scan.config['domain_brief']`)
   Describes the SCANNED domain — which on a competitor scan is the COMPETITOR's
   site (e.g. PF user scans laroche-posay.fr → domain_brief = LRP info).
   Inject this into ANALYSIS prompts (classify_topics, generate_personas,
   brand_classifier, etc.) so the LLM understands what it's looking at.

2. **Workspace brief** (per-client, stored in `client.apps['client_brief']`)
   Describes the USER's company — their industry, brand voice, positioning,
   audience, products. Inject this into CONTENT GENERATION prompts (FAQ,
   article, newsletter) so the output sounds like the user's brand even when
   the source is a competitor scan.

Both return "" gracefully if no brief exists (backward compatible). Together
they provide the vertical-agnostic specialization the SaaS needs without any
hardcoded brand maps or vertical-specific prompts.
"""


def format_brief_context(scan_config: dict | None) -> str:
    """Extract domain brief from scan config and format as prompt context block."""
    if not scan_config:
        return ""
    brief = scan_config.get("domain_brief")
    if not brief:
        return ""

    lines = ["## Domain Context"]
    if brief.get("company"):
        lines.append(f"Company: {brief['company']}")
    if brief.get("description"):
        lines.append(f"Description: {brief['description']}")
    if brief.get("industry"):
        lines.append(f"Industry: {brief['industry']}")
    if brief.get("country"):
        lines.append(f"Country: {brief['country']}")
    if brief.get("brands"):
        lines.append(f"Own brands: {', '.join(brief['brands'])}")
    if brief.get("product_lines"):
        lines.append(f"Product lines: {', '.join(brief['product_lines'])}")
    if brief.get("services"):
        lines.append(f"Services: {', '.join(brief['services'])}")
    if brief.get("competitors"):
        comp_strs = []
        for c in brief["competitors"]:
            prods = c.get("products", [])
            comp_strs.append(f"{c['name']} ({', '.join(prods)})" if prods else c["name"])
        lines.append(f"Competitors: {'; '.join(comp_strs)}")
    if brief.get("topics"):
        lines.append(f"Key topics: {', '.join(brief['topics'])}")
    if brief.get("target_audience"):
        lines.append(f"Target audience: {brief['target_audience']}")

    return "\n".join(lines)


def format_workspace_brief(client_apps: dict | None) -> str:
    """Extract workspace brief from client.apps and format as 'Your company' block.

    Distinct from format_brief_context (which describes the scanned domain).
    This describes the USER's company — for content generation handlers that
    need to bias output toward the user's brand voice / industry / audience.

    Pass `client.apps` directly (the JSONB column on Client). Returns "" if
    no client_brief has been generated yet (workspace not bootstrapped).
    """
    if not client_apps:
        return ""
    brief = client_apps.get("client_brief")
    if not brief:
        return ""

    lines = ["## Your company (the brand voice for this content)"]
    if brief.get("industry"):
        lines.append(f"Industry: {brief['industry']}")
    if brief.get("company_overview"):
        lines.append(f"Overview: {brief['company_overview']}")
    if brief.get("brand_positioning"):
        lines.append(f"Positioning: {brief['brand_positioning']}")
    if brief.get("editorial_voice"):
        lines.append(f"Editorial voice: {brief['editorial_voice']}")
    if brief.get("target_audience"):
        lines.append(f"Target audience: {brief['target_audience']}")
    if brief.get("products_services"):
        lines.append(f"Products / services: {', '.join(brief['products_services'])}")
    if brief.get("primary_brands"):
        # primary_brands is a list of {name, domain, role, description} dicts
        names = [b.get("name", "") for b in brief["primary_brands"] if b.get("name")]
        if names:
            lines.append(f"Primary brands (priority order): {', '.join(names)}")
    if brief.get("key_competitors"):
        lines.append(f"Known competitors (do NOT promote these): {', '.join(brief['key_competitors'])}")

    return "\n".join(lines)


def format_promoted_brands_block(promoted_brand_names: list[str]) -> str:
    """Format the brands-to-promote list as a high-priority injection block.

    Used when BrandResolver.resolve_promotion() returns the brands the system
    is instructed to promote in this specific content item. This is the
    runtime-resolved brand bias, distinct from the workspace defaults.
    """
    if not promoted_brand_names:
        return ""
    if len(promoted_brand_names) == 1:
        names = promoted_brand_names[0]
    else:
        lead = promoted_brand_names[0]
        rest = ", ".join(promoted_brand_names[1:])
        names = f"{lead} (lead) — supporting: {rest}"
    return (
        "## Brands to promote in this content (priority order)\n"
        f"{names}\n"
        "When the answer naturally fits, feature these brands and their products. "
        "DO NOT promote competitors. If the prompt later mentions 'produits {brand_name}', "
        "it refers to these brands, not the scanned domain."
    )
