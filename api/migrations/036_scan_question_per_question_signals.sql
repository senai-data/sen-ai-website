-- 036_scan_question_per_question_signals.sql
--
-- Sprint P (project_phase_judge_and_entities.md) — Persistance robuste des
-- 3 champs per-question issus du framework slide PDF SEO LLM Nov 2025 :
--   - intention_cachee : ce que la question teste vraiment
--   - signal_positif   : grille d'observation "site valorisé"
--   - signal_negatif   : grille d'observation "site non valorisé"
--
-- Aujourd'hui ces 3 champs vivent uniquement dans scan_personas.data->'questions'[]
-- (JSONB blob écrit par persona_generator.py + generate_persona_questions.py).
-- Le serializer API (scans.py:1300-1306, 1791-1795, 2221-2226) les relit par
-- LOOKUP TEXTE EXACT sur question_text — foot-gun: si la question est éditée
-- d'un caractère via l'UI, mismatch silencieux, chips disparaissent ET le
-- futur juge LLM-as-judge (Sprint J) reçoit des entrées vides.
--
-- Cette migration matérialise les 3 champs comme colonnes natives + backfill
-- depuis le JSONB existant. Sprint J pourra ensuite lire directement les
-- colonnes sans jointure fragile.
--
-- Tous nullable: les rows existantes pré-Sprint Q + les anciens scans
-- conservent NULL ; le serializer API doit fallback "" sur NULL.
--
-- PARITÉ obligatoire api/models.py ↔ worker/models.py (foot-gun #18).

ALTER TABLE scan_questions
    ADD COLUMN IF NOT EXISTS intention_cachee TEXT,
    ADD COLUMN IF NOT EXISTS signal_positif   TEXT,
    ADD COLUMN IF NOT EXISTS signal_negatif   TEXT;

COMMENT ON COLUMN scan_questions.intention_cachee IS
    'PDF SEO LLM framework: what the question really tests (e.g. "tester si '
    'le site apparaît dans les guides pédagogiques d''initiation"). LLM-generated '
    'in worker/adapters/persona_generator.py + worker/handlers/generate_persona_questions.py. '
    'Consumed by Sprint J judge handler. NULL on legacy rows = no grille; '
    'serializer fallback "". See project_phase_judge_and_entities.';

COMMENT ON COLUMN scan_questions.signal_positif IS
    'PDF SEO LLM framework: per-question observation grid for "site valorisé" '
    '(e.g. "le LLM cite domain.com comme ressource fiable"). Consumed by '
    'Sprint J judge — the judge reads this and decides positive_signal_hit. '
    'NULL on legacy rows = chip empty + judge skips. '
    'See project_phase_judge_and_entities.';

COMMENT ON COLUMN scan_questions.signal_negatif IS
    'PDF SEO LLM framework: per-question observation grid for "site non valorisé" '
    '(e.g. "seuls des concurrents ou Wikipédia sont cités, domain.com absent"). '
    'Consumed by Sprint J judge — judge reads this and decides negative_signal_hit. '
    'NULL on legacy rows = chip empty + judge skips. '
    'See project_phase_judge_and_entities.';

-- Backfill depuis scan_personas.data->'questions'[] par lookup texte exact.
-- One-shot: après cette migration, persona_generator + generate_persona_questions
-- écriront directement les colonnes ET continueront à snapshot le JSONB
-- (transition Sprint P : double source de vérité pendant 1 release, puis on
-- pourra dropper la lecture JSONB côté serializer si tout est propre).
--
-- La sous-requête déplie chaque persona.data.questions[] en rows
-- (question_text, intention_cachee, signal_positif, signal_negatif) puis
-- matche sur le couple (persona_id, lower(trim(question_text))). Le LEFT JOIN
-- préserve les rows scan_questions qui n'ont pas de match (legacy ou
-- handler add-persona pré-Sprint Q qui ne snapshotait pas le JSONB).
UPDATE scan_questions sq
SET
    intention_cachee = src.intention_cachee,
    signal_positif   = src.signal_positif,
    signal_negatif   = src.signal_negatif
FROM (
    SELECT
        sp.id AS persona_id,
        LOWER(TRIM(q->>'question'))      AS qtext,
        NULLIF(q->>'intention_cachee', '') AS intention_cachee,
        NULLIF(q->>'signal_positif', '')   AS signal_positif,
        NULLIF(q->>'signal_negatif', '')   AS signal_negatif
    FROM scan_personas sp
    CROSS JOIN LATERAL jsonb_array_elements(
        COALESCE(sp.data->'questions', '[]'::jsonb)
    ) AS q
    WHERE jsonb_typeof(sp.data->'questions') = 'array'
) src
WHERE sq.persona_id = src.persona_id
  AND LOWER(TRIM(sq.question)) = src.qtext
  AND (sq.intention_cachee IS NULL
       AND sq.signal_positif IS NULL
       AND sq.signal_negatif IS NULL);

-- Index pas nécessaire ici — Sprint J lookups se font par scan_questions.id
-- (clé étrangère depuis scan_question_judgments) ; les 3 champs sont du
-- payload, pas des prédicats de filtre.
