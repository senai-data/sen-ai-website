"""Shared per-brand config for the Pierre Fabre Oral Care brands (Option B:
one scan PER brand, all on pierrefabre-oralcare.com). Imported by
align_topics_oralcare.py / import_seollm_oralcare.py / fix_oralcare_brief.py,
selected via the BRAND env var (elg | ina | art | elu).

Each brand = its own sen-ai scan (own focus star + dashboard), like the other 5
PF brands. Topics, seo-llm sources and competitors are CATEGORY-SPECIFIC per
brand (Elgydium = toothpaste/whitening/gums/kids; Inava = brushes/interdental;
Arthrodont = gum gel; Eluane = dry mouth). Sister oral brands are NOT competitors
of each other (different categories) so they're omitted from each watchlist.
"""

# key → (topic name [UNIQUE leading prefix], description)
_T = {
    "bebe": ("Hygiène dentaire bébé (0-2 ans)",
             "Première dent, poussée dentaire du nourrisson, hygiène bucco-dentaire des tout-petits, dentifrice/brosse 0-2 ans."),
    "enfant": ("Hygiène dentaire enfant (3-12 ans)",
               "Brossage et dentifrice fluoré enfants, apprentissage de l'hygiène, dents de lait et définitives."),
    "blancheur": ("Blancheur et dents blanches",
                  "Dents jaunes, taches, dentifrices et soins blancheur, éclat de l'émail, blanchiment doux."),
    "caries": ("Caries et prévention carie",
               "Prévention des caries, déminéralisation de l'émail, fluor, dentifrices anti-caries."),
    "gencives": ("Gencives et parodontie",
                 "Gencives qui saignent, gingivite, parodontite, soins protecteurs des gencives au quotidien."),
    "ortho": ("Orthodontie et appareil dentaire",
              "Soins bucco-dentaires avec bagues/appareil, brossage spécifique, prévention caries sous appareil."),
    "plaque": ("Plaque dentaire et tartre",
               "Plaque bactérienne, tartre, dépôts dentaires, dentifrices anti-plaque, hygiène renforcée."),
    "sensibilite": ("Dents sensibles",
                    "Hypersensibilité dentinaire, douleur chaud/froid, dentifrices désensibilisants."),
    "brossage": ("Brossage et brosses à dents manuelles",
                 "Technique de brossage, choix de la brosse manuelle, souplesse des poils, fréquence/durée."),
    "electriques": ("Brosses à dents électriques",
                    "Brosses à dents électriques, têtes de rechange, modes de brossage, efficacité vs manuelle."),
    "interdentaire": ("Hygiène interdentaire (brossettes, fil)",
                      "Nettoyage entre les dents, brossettes interdentaires, fil dentaire, espaces interdentaires."),
    "gel_gencive": ("Gel gingival apaisant (gencives irritées)",
                    "Gel gingival pour gencives douloureuses/irritées/enflammées, poussées dentaires douloureuses, aphtes."),
    "bouche_seche": ("Bouche sèche et xérostomie",
                     "Sécheresse buccale, xérostomie, manque de salive (âge/médicaments), hydratation et confort buccal."),
}


def _topics(*keys):
    return [{"key": k, "name": _T[k][0], "description": _T[k][1]} for k in keys]


# prefix = unique leading part of the topic name, used by import find_topic(startswith)
BRAND_CONFIG = {
    "elg": {
        "brand_name": "Elgydium",
        "topics": _topics("bebe", "enfant", "blancheur", "caries", "gencives", "ortho", "plaque", "sensibilite"),
        "slug_to_prefix": {
            "bebe-elg": "Hygiène dentaire bébé",
            "enfant-elg": "Hygiène dentaire enfant",
            "blancheur-elg": "Blancheur",
            "caries-elg": "Caries",
            "gencives-elg": "Gencives et parodontie",
            "ortho-elg": "Orthodontie",
            "plaque-elg": "Plaque",
            "sensibilite-elg": "Dents sensibles",
        },
        "source_ids": [26, 27, 29, 33, 35, 38, 39, 40],
        "competitors": [
            {"name": "Sensodyne", "domain": "sensodyne.fr", "products": ["Répare & Protège", "Pro-Émail", "Sensibilité & Gencives"]},
            {"name": "Parodontax", "domain": "parodontax.fr", "products": ["Soin Gencives", "Complete Protection"]},
            {"name": "Colgate", "domain": "colgate.fr", "products": ["Colgate Total", "Max White", "Sensitive Pro-Relief"]},
            {"name": "Elmex", "domain": "elmex.fr", "products": ["Protection Caries", "Sensitive", "Anti-Caries Junior"]},
            {"name": "Meridol", "domain": "meridol.fr", "products": ["Protection Gencives", "Halitosis"]},
            {"name": "Signal", "domain": "signal.fr", "products": ["Integral 8", "White Now"]},
            {"name": "Email Diamant", "domain": "email-diamant.fr", "products": ["Le Blancheur"]},
            {"name": "Oral-B", "domain": "oralb.fr", "products": ["3D White", "Pro-Expert"]},
        ],
    },
    "ina": {
        "brand_name": "Inava",
        "topics": _topics("brossage", "electriques", "interdentaire"),
        "slug_to_prefix": {
            "brossage-ina": "Brossage",
            "electriques-ina": "Brosses à dents électriques",
            "interdentaire-ina": "Hygiène interdentaire",
        },
        "source_ids": [28, 32, 37],
        "competitors": [
            {"name": "Oral-B", "domain": "oralb.fr", "products": ["Oral-B Pro", "Oral-B iO", "Interdental", "Brossettes"]},
            {"name": "TePe", "domain": "tepe.com", "products": ["TePe Original", "TePe Angle", "TePe Interdental"]},
            {"name": "Curaprox", "domain": "curaprox.com", "products": ["CS 5460", "CPS Prime"]},
            {"name": "GUM", "domain": "gumshop.fr", "products": ["Soft-Picks", "Trav-Ler", "Brossettes"]},
            {"name": "Colgate", "domain": "colgate.fr", "products": ["Brosses à dents", "360"]},
            {"name": "Signal", "domain": "signal.fr", "products": ["Brosses à dents"]},
        ],
    },
    "art": {
        "brand_name": "Arthrodont",
        "topics": _topics("gel_gencive"),
        "slug_to_prefix": {"gencive-art": "Gel gingival"},
        "source_ids": [34],
        # NOTE: Eludril / Pansoral / Lyso 6 are Pierre Fabre's OWN oral brands
        # (sisters), NOT competitors — they're handled by the sister-ignore step.
        "competitors": [
            {"name": "Parodontax", "domain": "parodontax.fr", "products": ["Soin Gencives"]},
            {"name": "Meridol", "domain": "meridol.fr", "products": ["Protection Gencives"]},
            {"name": "Hextril", "domain": "hextril.fr", "products": ["Bain de bouche"]},
            {"name": "Hyalugel", "domain": "hyalugel.fr", "products": ["Gel buccal", "Bain de bouche"]},
            {"name": "Bloxaphte", "domain": "bloxaphte.fr", "products": ["Spray aphtes", "Gel aphtes"]},
            {"name": "Urgo", "domain": "urgo.fr", "products": ["Urgo Aphtes", "Filmogel Aphtes"]},
        ],
    },
    "elu": {
        "brand_name": "Eluane",
        "topics": _topics("bouche_seche"),
        "slug_to_prefix": {"hygienebouche-elu": "Bouche sèche"},
        "source_ids": [36],
        "competitors": [
            {"name": "Artisial", "domain": "artisial.fr", "products": ["Spray buccal"]},
            {"name": "Aequasyal", "domain": "aequasyal.com", "products": ["Spray bouche sèche"]},
            {"name": "BioXtra", "domain": "bioxtra.fr", "products": ["Gel", "Spray", "Dentifrice"]},
            {"name": "GUM", "domain": "gumshop.fr", "products": ["GUM Hydral"]},
            {"name": "Sensodyne", "domain": "sensodyne.fr", "products": ["Bouche sèche"]},
        ],
    },
}


def get_brand(brand_code: str):
    cfg = BRAND_CONFIG.get((brand_code or "").lower())
    if not cfg:
        raise SystemExit(f"BRAND must be one of {list(BRAND_CONFIG)} (got {brand_code!r})")
    return cfg
