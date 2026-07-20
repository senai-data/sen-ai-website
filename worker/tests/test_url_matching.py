"""Test vectors for services/url_matching.py (Placements module).

The 18 vectors from the plan (placements-module.md §3.4). They MUST pass
before any deploy touching url_matching.py. Runnable two ways :
  - pytest worker/tests/test_url_matching.py
  - python worker/tests/test_url_matching.py   (no pytest needed)
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.url_matching import (  # noqa: E402
    build_index,
    is_redirect_url,
    match_citation,
    normalize_url,
    registrable_domain,
)


def _match(placement_url, citation_url):
    """Best match level for a single placement vs a single citation."""
    index = build_index([{"id": "p1", "url": placement_url}])
    results = dict(match_citation(index, citation_url))
    return results.get("p1")


def test_01_www_and_trailing_slash():
    # Le cas cité par le user : https://ameli.fr vs https://www.ameli.fr/
    assert _match("https://ameli.fr", "https://www.ameli.fr/") == "exact"


def test_02_scheme_and_trailing_slash():
    assert _match("http://www.topsante.com/a-b-c", "https://topsante.com/a-b-c/") == "exact"


def test_03_tracking_params_stripped():
    assert _match(
        "https://www.doctissimo.fr/beaute/article-785777.htm",
        "https://www.doctissimo.fr/beaute/article-785777.htm?utm_source=chatgpt.com",
    ) == "exact"
    assert _match(
        "https://www.topsante.com/eczema/pollution-977461",
        "https://www.topsante.com/eczema/pollution-977461?xtor=AL-123",
    ) == "exact"


def test_04_index_file_stripped():
    assert _match("https://site.fr/dossier/", "https://site.fr/dossier/index.php") == "exact"


def test_05_percent_encoding_and_nfc():
    assert _match(
        "https://site.fr/dermatologie-p%C3%A9diatrique",
        "https://site.fr/dermatologie-pédiatrique",
    ) == "exact"
    # NFD (e + combining accent) vs NFC (precomposed)
    assert _match(
        "https://site.fr/bebe-eczema",
        "https://site.fr/bebe-eczema",
    ) == "exact"
    assert _match(
        "https://site.fr/dermatologie-pédiatrique",
        "https://site.fr/dermatologie-pédiatrique",
    ) == "exact"


def test_06_case_insensitive():
    assert _match(
        "https://www.beaute-test.com/mag/x.php",
        "HTTPS://WWW.Beaute-Test.COM/mag/X.php",
    ) == "exact"


def test_07_mobile_and_amp_variants():
    assert _match(
        "https://www.aufeminin.com/beaute/soins-visage/article-2804238.html",
        "https://m.aufeminin.com/beaute/soins-visage/article-2804238.html",
    ) == "variant"
    assert _match(
        "https://www.aufeminin.com/beaute/soins-visage/article-2804238.html",
        "https://www.aufeminin.com/beaute/soins-visage/article-2804238.html/amp",
    ) == "variant"


def test_08_significant_params_block_variant():
    # Régression du bug legacy : ids d'article en query NE matchent PAS.
    assert _match(
        "https://site.fr/article.php?id=123",
        "https://site.fr/article.php?id=456",
    ) == "domain"
    # Mêmes params significatifs -> exact.
    assert _match(
        "https://site.fr/article.php?id=123",
        "https://www.site.fr/article.php?id=123&utm_source=x",
    ) == "exact"


def test_09_trailing_dot_and_default_port():
    assert _match("https://ameli.fr/eczema", "https://www.ameli.fr./eczema") == "exact"
    assert _match("https://ameli.fr/eczema", "https://ameli.fr:443/eczema") == "exact"


def test_10_double_slashes():
    assert _match("https://site.fr/a/b", "https://site.fr//a//b") == "exact"


def test_11_www_prefix_only_regression():
    # Bug legacy : replace('www.') corrompait awww.site.com -> asite.com.
    norm = normalize_url("https://awww.site.com/page")
    assert norm["host"] == "awww.site.com"
    assert _match("https://asite.com/page", "https://awww.site.com/page") is None


def test_12_fragment_stripped():
    assert _match("https://site.fr/guide", "https://site.fr/guide#section-2") == "exact"


def test_13_user_input_without_scheme():
    assert _match("www.doctissimo.fr/x", "https://www.doctissimo.fr/x") == "exact"
    assert _match("doctissimo.fr/x", "https://www.doctissimo.fr/x") == "exact"


def test_14_domain_tier_only():
    assert _match(
        "https://www.topsante.com/eczema/mon-article-977461",
        "https://www.topsante.com/autre-rubrique/autre-article-999999",
    ) == "domain"


def test_15_redirect_host_detection():
    assert is_redirect_url(
        "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQE123"
    )
    assert not is_redirect_url("https://www.ameli.fr/")


def test_16_prefix_truncated():
    long_url = (
        "https://www.beaute-test.com/mag/eczema-du-visage-irritation-ou-allergie-"
        "les-signes-pour-faire-la-difference-et-apaiser-la-peau.php"
    )
    truncated = (
        "https://www.beaute-test.com/mag/eczema-du-visage-irritation-ou-allergie-"
        "les-signes-pour-faire"
    )
    assert _match(long_url, truncated) == "prefix"
    # Path court : prefix refusé, domain seulement.
    assert _match("https://site.fr/eczema-du-visage-tout-savoir", "https://site.fr/ecz") == "domain"


def test_17_homepage_placement():
    assert _match("https://ameli.fr", "https://www.ameli.fr/") == "exact"
    # La homepage ne matche PAS les articles du site en exact/variant.
    assert _match("https://ameli.fr", "https://www.ameli.fr/assure/sante/eczema") == "domain"


def test_18_garbage_input_no_crash():
    norm = normalize_url("not a url at all :::")
    assert norm["parse_error"] is True
    assert match_citation(build_index([{"id": "p1", "url": "https://site.fr/a"}]), "") == []
    assert _match("", "https://site.fr/a") is None


def test_registrable_domain_heuristic():
    assert registrable_domain("blog.lemonde.fr") == "lemonde.fr"
    assert registrable_domain("www.bbc.co.uk") == "bbc.co.uk"
    assert registrable_domain("topsante.com") == "topsante.com"


if __name__ == "__main__":
    failures = 0
    tests = sorted(
        (name, fn) for name, fn in list(globals().items())
        if name.startswith("test_") and callable(fn)
    )
    for name, fn in tests:
        try:
            fn()
            print("PASS " + name)
        except AssertionError as exc:
            failures += 1
            print("FAIL " + name + " : " + str(exc))
    print("----")
    print(str(len(tests) - failures) + "/" + str(len(tests)) + " passed")
    sys.exit(1 if failures else 0)
