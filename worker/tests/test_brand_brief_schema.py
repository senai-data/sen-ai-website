"""BrandBrief Pydantic schema invariants.

Catches drift between the LLM output shape, the JSONB column, and the
brief_injector merge code. The schema is intentionally lenient (everything
optional except ``name``), so the tests focus on :

1. Required-name enforcement
2. Defaults preserve type (no None leaking into list/str fields)
3. Tolerance to common LLM-side messiness (list-of-strings for competitors,
   "since 1736" / "1736-01-01" / int for founded_year, surrounding whitespace)
4. ``extra='ignore'`` keeps unknown fields from blowing up validation
"""

from __future__ import annotations

import pytest

from schemas import BrandBrief, CompetitorInBrief


class TestRequired:
    def test_name_required(self):
        with pytest.raises(Exception):
            BrandBrief.model_validate({})

    def test_empty_name_rejected(self):
        with pytest.raises(Exception):
            BrandBrief.model_validate({"name": ""})

    def test_minimal_valid(self):
        b = BrandBrief.model_validate({"name": "Avène"})
        assert b.name == "Avène"
        assert b.parent_group == ""
        assert b.editorial_voice == ""
        assert b.differentiators == []
        assert b.direct_competitors == []
        assert b.price_tier == ""
        assert b.founded_year is None
        assert b.taglines == []
        assert b.languages == []
        assert b.signature_features == []


class TestPriceTier:
    @pytest.mark.parametrize("raw, expected", [
        ("mass", "mass"),
        ("Premium", "Premium"),                  # casing preserved (free-form)
        ("enterprise B2B", "enterprise B2B"),
        ("  pharmacy  ", "pharmacy"),            # stripped
        ("mid-market", "mid-market"),
    ])
    def test_free_form_preserved(self, raw, expected):
        b = BrandBrief.model_validate({"name": "X", "price_tier": raw})
        assert b.price_tier == expected

    @pytest.mark.parametrize("raw", [None, 42, []])
    def test_non_string_falls_back_to_empty(self, raw):
        b = BrandBrief.model_validate({"name": "X", "price_tier": raw})
        assert b.price_tier == ""


class TestFoundedYear:
    @pytest.mark.parametrize("raw, expected", [
        ("1736", 1736),
        ("since 1736", 1736),
        ("1736-01-01", 1736),
        ("Founded in 1736 as a thermal spa", 1736),
        (1736, 1736),
        (None, None),
        ("", None),
        ("18th century", None),                  # no 4-digit run
        ("12345 invalid", None),                 # \b\d{4}\b doesn't match inside a longer digit run
        ("0000", None),                          # out of range
    ])
    def test_coerce_year(self, raw, expected):
        b = BrandBrief.model_validate({"name": "X", "founded_year": raw})
        assert b.founded_year == expected


class TestStringStripping:
    def test_whitespace_stripped(self):
        b = BrandBrief.model_validate({
            "name": "  Avène  ",
            "parent_group": "  Pierre Fabre\n",
            "description": "\tfoo\t",
        })
        assert b.name == "Avène"
        assert b.parent_group == "Pierre Fabre"
        assert b.description == "foo"


class TestDirectCompetitors:
    def test_list_of_strings(self):
        b = BrandBrief.model_validate({
            "name": "Avène",
            "direct_competitors": ["La Roche-Posay", "Bioderma"],
        })
        assert len(b.direct_competitors) == 2
        assert all(isinstance(c, CompetitorInBrief) for c in b.direct_competitors)
        assert b.direct_competitors[0].name == "La Roche-Posay"
        assert b.direct_competitors[0].products == []

    def test_list_of_dicts(self):
        b = BrandBrief.model_validate({
            "name": "Avène",
            "direct_competitors": [
                {"name": "La Roche-Posay", "products": ["Effaclar"], "domain": "https://www.laroche-posay.fr/"},
                {"name": "Bioderma"},
            ],
        })
        assert b.direct_competitors[0].domain == "laroche-posay.fr"
        assert b.direct_competitors[0].products == ["Effaclar"]
        assert b.direct_competitors[1].domain == ""

    def test_mixed_types_kept(self):
        b = BrandBrief.model_validate({
            "name": "Avène",
            "direct_competitors": [
                "Bioderma",
                {"name": "La Roche-Posay", "products": []},
            ],
        })
        assert len(b.direct_competitors) == 2
        assert b.direct_competitors[0].name == "Bioderma"
        assert b.direct_competitors[1].name == "La Roche-Posay"


class TestExtraFields:
    def test_unknown_fields_dropped(self):
        b = BrandBrief.model_validate({
            "name": "Avène",
            "hallucinated_field": "foo",
            "another_one": 42,
            "edited_by_user": True,  # the handler adds this AFTER validation
        })
        assert b.name == "Avène"
        assert not hasattr(b, "hallucinated_field")


class TestListDefaults:
    def test_lists_isolated(self):
        # Defensive : default_factory should not share a single list instance
        a = BrandBrief.model_validate({"name": "Avène"})
        b = BrandBrief.model_validate({"name": "Klorane"})
        a.differentiators.append("foo")
        a.taglines.append("bar")
        a.languages.append("fr")
        a.signature_features.append("baz")
        assert b.differentiators == []
        assert b.taglines == []
        assert b.languages == []
        assert b.signature_features == []


class TestFullPayload:
    def test_realistic_avene(self):
        payload = {
            "name": "Avène",
            "parent_group": "Pierre Fabre",
            "description": "Premium dermo-cosmetic skincare for sensitive skin.",
            "founded_year": "since 1736",
            "headquarters": "Castres, France",
            "languages": ["fr", "en", "es", "de", "jp"],
            "positioning_statement": "Skincare for the most sensitive skin",
            "taglines": ["Skincare for sensitive skin", "Recommended by dermatologists"],
            "differentiators": ["Thermal spring water", "Pharmacy distribution"],
            "price_tier": "premium pharmacy",
            "distribution": ["pharmacy", "selective"],
            "editorial_voice": "expert, reassuring, science-led",
            "tonality": ["expert", "warm"],
            "target_audience": "Women 25-55 with sensitive skin",
            "audience_segments": ["sensitive skin", "atopic", "post-procedure"],
            "product_lines": ["Cleanance", "Tolerance", "Hydrance"],
            "hero_products": ["Cleanance Comedomed", "Tolerance Cream"],
            "signature_features": ["thermal spring water", "C-Xeposure"],
            "direct_competitors": [
                {"name": "La Roche-Posay", "products": ["Effaclar"], "domain": "laroche-posay.fr"},
            ],
            "indirect_competitors": ["Bioderma"],
            "expertise_topics": ["sensitive skin routine", "rosacea", "atopic dermatitis"],
            "regulatory_constraints": ["EU cosmetic regulation 1223/2009"],
        }
        b = BrandBrief.model_validate(payload)
        assert b.name == "Avène"
        assert b.founded_year == 1736
        assert b.price_tier == "premium pharmacy"
        assert b.languages == ["fr", "en", "es", "de", "jp"]
        assert b.taglines[0] == "Skincare for sensitive skin"
        assert b.signature_features == ["thermal spring water", "C-Xeposure"]
        assert len(b.direct_competitors) == 1
        assert b.direct_competitors[0].domain == "laroche-posay.fr"
        # Round-trip stays in shape
        dumped = b.model_dump()
        again = BrandBrief.model_validate(dumped)
        assert again.name == b.name
        assert again.founded_year == 1736
