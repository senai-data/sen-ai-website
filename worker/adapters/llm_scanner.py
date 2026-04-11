"""LLM Scanner using seo-llm components directly.

Uses LLMClient, CitationExtractor, and BrandAnalyzer from seo-llm.
"""

import logging
import time

from seo_llm.src.llm_client import LLMClient
from seo_llm.src.citation_extractor import CitationExtractor
from seo_llm.src.brand_analyzer import BrandAnalyzer
from seo_llm.src.config import get_llm_test_prompt

logger = logging.getLogger(__name__)


def create_llm_client(provider: str, api_key: str, model: str = None) -> LLMClient:
    """Create an LLMClient instance for the given provider."""
    if provider == "openai":
        return LLMClient(provider="openai", api_key=api_key, model=model or "gpt-4.1-mini")
    elif provider == "gemini":
        return LLMClient(provider="gemini", api_key=api_key, model=model or "gemini-2.5-flash")
    else:
        raise ValueError(f"Unknown provider: {provider}")


def format_persona_summary(persona: dict) -> str:
    """Format persona as context for the LLM test prompt."""
    profil = persona.get("profil_demographique", {})
    return (
        f"L'utilisateur est {persona.get('nom', 'un visiteur')}. "
        f"Âge: {profil.get('age', '?')}. "
        f"Profession: {profil.get('situation_professionnelle', '?')}. "
        f"Niveau d'expertise: {profil.get('niveau_expertise', '?')}."
    )


def test_question(question: str, persona: dict, llm_client: LLMClient,
                   target_domain: str, brand_analyzer: BrandAnalyzer = None) -> dict:
    """Test a question with an LLM, extract citations and analyze brand mentions.

    Args:
        question: The question text
        persona: Persona dict with profil_demographique
        llm_client: LLMClient instance (OpenAI or Gemini)
        target_domain: Domain to check in citations (user's site)
        brand_analyzer: Optional BrandAnalyzer instance for brand mention analysis
    """
    persona_summary = format_persona_summary(persona)
    prompt_template = get_llm_test_prompt(llm_client.provider)
    prompt = prompt_template.format(persona_summary=persona_summary, question=question)

    start = time.time()

    # 1. Generate LLM response with web search / grounding
    response = llm_client.generate(
        prompt,
        temperature=0.7,
        max_tokens=8000,
        agent_name="platform_scan",
        use_grounding=True,
    )
    duration_ms = int((time.time() - start) * 1000)

    # 2. Extract citations (seo-llm CitationExtractor)
    extractor = CitationExtractor(site_domain=target_domain)
    citations = extractor.extract_citations(
        response_text=response["text"],
        grounding_sources=response.get("grounding_sources", []),
        provider=llm_client.provider,
    )

    # Analyze citation results
    target_cited = any(c.get("est_site_cible") for c in citations)
    target_position = None
    competitor_domains = {}
    for i, c in enumerate(citations):
        if c.get("est_site_cible") and target_position is None:
            target_position = i + 1
        elif c.get("domaine") and not c.get("est_site_cible"):
            domain = c["domaine"]
            if domain not in ("google.com", "youtube.com"):
                competitor_domains[domain] = competitor_domains.get(domain, 0) + 1

    # 3. Brand mention analysis (seo-llm BrandAnalyzer)
    brand_mentions = []
    brand_analysis = {}
    if brand_analyzer:
        try:
            brand_result = brand_analyzer.analyze_response(response["text"], question)
            if brand_result:
                brand_mentions = brand_result.get("brand_mentions", [])
                brand_analysis = brand_result.get("brand_analyse", {})
        except Exception as e:
            logger.warning(f"BrandAnalyzer failed: {e}")

    return {
        "provider": llm_client.provider,
        "model": response.get("model", llm_client.model),
        "response_text": response["text"],
        "citations": citations,
        "target_cited": target_cited,
        "target_position": target_position,
        "total_citations": len(citations),
        "competitor_domains": competitor_domains,
        "brand_mentions": brand_mentions,
        "brand_analysis": brand_analysis,
        "duration_ms": duration_ms,
        "input_tokens": response.get("usage", {}).get("prompt_tokens", 0),
        "output_tokens": response.get("usage", {}).get("completion_tokens", 0),
    }
