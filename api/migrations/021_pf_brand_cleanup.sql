-- 021_pf_brand_cleanup.sql
--
-- One-off cleanup for the Pierre Fabre client workspace.
--
-- Migration 019 backfilled clients.primary_brand_ids from every brand ever
-- classified my_brand across any scan — for PF this produced 190 entries
-- including product line names (Xémose, Cicalfate, Toléderm…), competitors
-- (Bioderma, La Roche-Posay, Uriage), and duplicate rows for the same brand.
--
-- This migration narrows primary_brand_ids to the 6 canonical PF user
-- brands, in priority order with Avène as lead. Domains are filled in for
-- Ducray and René Furterer where they were missing. We do NOT delete any
-- client_brands rows — they may be referenced by scan_brand_classifications,
-- scan.promotion_brand_ids, scan_content_items.promoted_brand_ids, etc.
-- Pollution from duplicate / product-line entries stays in the table but
-- exits primary_brand_ids, so the auto-suggest pipeline + UI both behave.
--
-- Not idempotent: re-running overwrites the same primary_brand_ids array.
-- Safe because the array is set explicitly, not appended.

-- Fill missing domains on the canonical rows for Ducray and René Furterer
UPDATE client_brands SET domain = 'ducray.com'
WHERE id = '712ebc77-1836-4660-9df4-41419613e838'
  AND (domain IS NULL OR domain = '');

UPDATE client_brands SET domain = 'renefurterer.com'
WHERE id = '17eaea3c-dbb7-471b-a994-d99e41400422'
  AND (domain IS NULL OR domain = '');

-- Narrow primary_brand_ids to the 6 PF user brands. Order = priority,
-- index 0 is the lead the FAQPageMatcher targets first.
UPDATE clients
SET primary_brand_ids = ARRAY[
    '2fd1c26f-48b6-492d-a3d8-e3a71a5223f1'::uuid,  -- Avène (eau-thermale-avene.fr)
    '4a4acd9e-c753-4699-90ec-efae223941a8'::uuid,  -- Aderma (aderma.fr)
    '712ebc77-1836-4660-9df4-41419613e838'::uuid,  -- Ducray (ducray.com)
    '72e1faf8-307d-4726-952b-cae560c76458'::uuid,  -- Klorane (klorane.com)
    '17eaea3c-dbb7-471b-a994-d99e41400422'::uuid,  -- René Furterer (renefurterer.com)
    '5780742f-0f5e-4ac5-98ab-92cee62efef1'::uuid   -- Pierre Fabre Oral Care (no domain yet — set via UI)
]
WHERE id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';
