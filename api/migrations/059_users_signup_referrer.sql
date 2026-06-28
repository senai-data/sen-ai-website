-- 059_users_signup_referrer.sql
--
-- Attribution contenu -> inscription. Capture le document.referrer au moment
-- du register (typiquement la page du cocon /guides d'ou vient l'inscrit) pour
-- pouvoir, plus tard, croiser avec Stripe et prouver le ROI business du contenu.
--
-- First-party, last-touch, cookieless (pas de consentement requis). La valeur est
-- fournie par le client (donc spoofable) : OK pour de l'analytics d'attribution,
-- ce n'est pas une frontiere de securite. Cote API : tag-strippee + tronquee.
--
-- Additive only. Pas de backfill - les comptes existants gardent NULL.
-- Pendant de 058 (signup_intent). Capture aujourd'hui sur le formulaire
-- email/mot de passe ; le chemin OAuth Google reste a cabler (referrer perdu
-- a travers la redirection Google).

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS signup_referrer TEXT;

COMMENT ON COLUMN users.signup_referrer IS
    'Attribution contenu->signup : document.referrer capture au register '
    '(souvent une page du cocon /guides). First-party last-touch, cookieless. '
    'A croiser avec Stripe pour le ROI du contenu. NULL si referrer vide/externe '
    'ou pour les comptes anterieurs a la migration 059.';
