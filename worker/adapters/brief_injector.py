"""
Shared utility to format domain brief as prompt context block.
Used by all LLM call sites that benefit from business context.
Returns empty string if no brief exists (backward compatible).
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
