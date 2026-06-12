# Migration sen-ai.fr : o2switch → Cloudflare

> Rédigé le 2026-06-05. Source de vérité du plan. L'app reste sur le VPS Hetzner ;
> seuls le **DNS** puis le **registrar** quittent o2switch.

---

## 0. TL;DR

- **Ce qui bouge** : la zone DNS (aujourd'hui chez o2switch `ns1/ns2.o2switch.net`) part chez Cloudflare, puis plus tard l'enregistrement du domaine `.fr`.
- **Ce qui ne bouge pas** : l'application (Astro SSR + FastAPI + Postgres + workers + nginx) reste sur le VPS Hetzner `135.181.156.218`. Aucune ligne de code applicatif à changer pour la migration DNS de base.
- **Risque n°1 — email M365** : un MX/SPF/autodiscover mal recopié = mail mort. La zone est documentée ci-dessous à l'identique.
- **Risque n°2 — certificat TLS** : le cert live du VPS **expire le 2026-07-01** et le renouvellement auto n'est pas prouvé. La migration est l'occasion de fiabiliser ça (Cloudflare Origin CA = 15 ans, zéro renouvellement).
- **Stratégie** : migration **en 3 phases**, la phase 1 (DNS-only / « grey cloud ») étant un lift-and-shift à risque nul, puis la phase 2 (proxy / « orange cloud ») optionnelle et séparée, puis la phase 3 (transfert registrar avant le 2026-09-09).

---

## 1. État des lieux (audit du 2026-06-05)

| Élément | Valeur actuelle |
|---|---|
| Domaine | `sen-ai.fr` (registrar + DNS chez o2switch) |
| Nameservers | `ns1.o2switch.net`, `ns2.o2switch.net` (TTL 21600) |
| Hébergement app | VPS Hetzner `135.181.156.218` (CX23, Helsinki) |
| Stack | docker-compose : nginx (alpine) → astro:4321 (SSR node) + api:8000 (FastAPI) + worker + worker-content + postgres:16 |
| Reverse proxy | nginx, ports 80/443, 3 server blocks (HTTP→HTTPS, www→apex, apex) |
| TLS | Let's Encrypt sur l'hôte VPS `/etc/letsencrypt/live/sen-ai.fr/`, monté en RO dans nginx. **Cert émis 2026-04-02, expire 2026-07-01.** Renouvellement auto **non prouvé** (aucun timer/cron certbot dans le repo). |
| Reports statiques | servis par nginx depuis `/r/{slug}/*.html` (regex 12 chars), `Cache-Control: private, max-age=300` |
| Email | Microsoft 365 (MX Outlook) + Brevo (transactionnel) |
| o2switch | offre Unique Cloud 230,40 €/an, renouvelée 2026-09-09 → **échéance 2026-09-09**. Seule fonction restante = la zone DNS. |

### Pourquoi le mail de renouvellement SSL d'o2switch échoue (le trigger de cette session)

o2switch (cPanel AutoSSL) tente de renouveler **son propre** certificat pour `sen-ai.fr`, mais le `A record` pointe vers le VPS Hetzner depuis longtemps. Le challenge ACME http-01 servi par o2switch ne peut donc plus être validé (404 côté VPS). **Ce certificat o2switch est mort et inutile** — le vrai cert vit sur le VPS. → On peut **ignorer** cette erreur, ou retirer le domaine de l'AutoSSL cPanel pour faire taire l'alerte. Elle disparaîtra de toute façon quand on quittera o2switch.

---

## 2. Zone DNS actuelle (à recréer à l'identique dans Cloudflare)

Relevé en live le 2026-06-05 (DNS-over-HTTPS). **Source autoritative complète = export de la zone depuis cPanel o2switch → Zone Editor** (faire l'export AVANT toute manip pour ne rien rater).

| Type | Nom | Valeur | TTL actuel | Proxy CF visé |
|---|---|---|---|---|
| A | `@` (sen-ai.fr) | `135.181.156.218` | 14400 | grey en phase 1, orange en phase 2 |
| A | `www` | `135.181.156.218` | 14400 | grey en phase 1, orange en phase 2 |
| MX | `@` | `senai-fr0i.mail.protection.outlook.com` (priorité 0) | 3600 | **grey obligatoire** (MX jamais proxifiable) |
| TXT | `@` | `v=spf1 include:spf.protection.outlook.com -all` | 14400 | grey (TXT non proxifiable) |
| TXT | `@` | `MS=ms41352629` (vérif M365) | 14400 | grey |
| TXT | `@` | `brevo-code:1878249a7e3d10ac5a4be2cd958b9bdf` (vérif Brevo) | 14400 | grey |
| CNAME | `autodiscover` | `autodiscover.outlook.com` | 3600 | **grey obligatoire** |
| TXT | `_dmarc` | `v=DMARC1; p=none; rua=mailto:rua@dmarc.brevo.com` | 14400 | grey |
| NS | `@` | `ns1/ns2.o2switch.net` | 21600 | **NE PAS recopier** — remplacés par les NS Cloudflare |

**Absents (vérifiés)** : pas de DKIM M365 (`selector1/2._domainkey`), pas de `brevo._domainkey`. À reconfirmer dans l'export cPanel — Brevo peut utiliser un autre sélecteur (`mail._domainkey`, `brevo1/2._domainkey`).

> ⚠️ **Observation hors-scope migration mais à corriger** : le SPF n'inclut **que** Outlook (`-all` strict), pas Brevo. Si l'app envoie des emails transactionnels (vérif compte, reset password) via Brevo, ils **échouent SPF**. À traiter séparément (ajouter `include:spf.brevo.com` au SPF + configurer le DKIM Brevo). Ne PAS l'introduire pendant la migration pour ne pas mélanger les variables.

---

## 3. Trois décisions à trancher

### Décision A — Mode proxy Cloudflare (le choix structurant)

| | **Grey cloud (DNS-only)** | **Orange cloud (proxied)** |
|---|---|---|
| Effort | quasi nul | nginx à patcher + cert à changer |
| TLS | Let's Encrypt reste sur le VPS (à fiabiliser) | Cloudflare Origin CA (15 ans, zéro renouvellement) |
| CDN / WAF / anti-DDoS | non | oui |
| IP origine masquée | non (déjà exposée) | oui |
| Cache `/r/*` | inchangé | **règle « Bypass » obligatoire** |
| Footguns | aucun nouveau | real-IP, timeout 100 s, cache (cf §6) |

**Reco** : commencer **grey cloud** (phase 1, risque nul), puis passer **orange cloud** plus tard et séparément (phase 2) une fois la phase 1 stable. L'orange cloud apporte la vraie valeur (anti-DDoS + fin du casse-tête certbot via Origin CA) mais doit être fait délibérément.

### Décision B — Registrar

- **Étape gratuite et réversible** (phase 1) : changer les nameservers chez o2switch vers ceux de Cloudflare. Le domaine reste *enregistré* chez o2switch, mais le DNS est servi par Cloudflare.
- **Transfert d'enregistrement** (phase 3, avant 2026-09-09) : ⚠️ **à vérifier — Cloudflare Registrar ne supporte pas tous les TLD, et `.fr` n'est historiquement pas garanti.** Si `.fr` n'est pas supporté chez Cloudflare Registrar :
  - garder le **DNS chez Cloudflare (gratuit)** et transférer l'**enregistrement** vers un registrar `.fr` pas cher (OVH ~7 €/an, Gandi, Porkbun si dispo).
  - Vérifier aussi si o2switch permet de conserver le domaine seul sans l'offre hébergement. Sinon, **sortir le domaine avant l'échéance du 2026-09-09**.

### Décision C — nginx → Caddy (optionnel, inspiré de flair-ai-hub)

`flair-ai-hub` utilise **Caddy** (auto-HTTPS Let's Encrypt, renouvellement automatique, résolution d'upstream dynamique). Adopter Caddy sur le VPS réglerait **deux footguns documentés** de ce projet :
1. le renouvellement de cert manuel/fragile (Caddy le fait seul) ;
2. le bug récurrent « nginx cache l'ancienne IP du container → 502 » après `docker compose up -d` (Caddy re-résout l'upstream à la volée).

→ **Hors chemin critique de la migration.** À considérer en phase 2+ si on reste en grey cloud. Si on passe orange cloud avec Origin CA, le besoin Caddy diminue (plus de renouvellement LE). Voir §8.

---

## 4. Runbook — Phase 1 : DNS chez Cloudflare (grey cloud, zéro downtime)

**Pré-requis (J-2 à J-7)**
1. Export complet de la zone depuis cPanel o2switch → *Zone Editor* (sauvegarde + référence).
2. **Vérifier le renouvellement du cert VPS** (urgent, voir §7) — ne pas migrer avec un cert qui va lapser le 2026-07-01.
3. Baisser les TTL des enregistrements chez o2switch à **300 s** (48 h avant le switch NS) pour un rollback rapide si besoin.

**Mise en place Cloudflare**
4. Créer un compte Cloudflare, plan **Free**. *Add a Site* → `sen-ai.fr`. Cloudflare **scanne** la zone o2switch et pré-importe les enregistrements.
5. **Auditer l'import** ligne par ligne contre le tableau §2 et l'export cPanel. Ajouter manuellement tout ce qui manque (Cloudflare rate parfois MX/TXT/DKIM). Vérifier en particulier : MX, les 3 TXT (SPF, MS=, brevo-code), `autodiscover`, `_dmarc`.
6. Mettre **tous** les enregistrements email en **grey cloud** (DNS only). En phase 1, mettre aussi `@` et `www` en **grey cloud**.
7. SSL/TLS Cloudflare → mode **Full (strict)** (pas « Flexible » qui casserait les redirections HTTPS du nginx). En grey cloud ce réglage est sans effet, mais on le pose proprement pour la phase 2.

**Bascule des nameservers**
8. Chez **o2switch** (registrar), remplacer `ns1/ns2.o2switch.net` par les 2 nameservers Cloudflare fournis.
9. Attendre la propagation (Cloudflare envoie un email « Active » ; généralement < 1 h, jusqu'à 24 h).

**Vérification (cf §5)** — tester site, app, OAuth, reports, et **surtout l'email** (envoi + réception sur une boîte `@sen-ai.fr`).

> En grey cloud, le comportement est **identique** à o2switch (mêmes IP, même cert LE, mêmes MX). Le seul changement est *qui sert la zone*. Rollback = remettre les NS o2switch.

---

## 5. Checklist de vérification (après bascule NS)

```powershell
# Nameservers délégués à Cloudflare
nslookup -type=NS sen-ai.fr 1.1.1.1
# A apex + www → 135.181.156.218
nslookup -type=A sen-ai.fr 1.1.1.1
nslookup -type=A www.sen-ai.fr 1.1.1.1
# MX intact
nslookup -type=MX sen-ai.fr 1.1.1.1
# SPF + vérifs
nslookup -type=TXT sen-ai.fr 1.1.1.1
nslookup -type=TXT _dmarc.sen-ai.fr 1.1.1.1
```
(Si le DNS UDP est filtré sur le poste, utiliser l'API DoH : `https://dns.google/resolve?name=sen-ai.fr&type=MX`.)

Fonctionnel :
- [ ] `https://sen-ai.fr` charge (page marketing, SSR)
- [ ] `https://www.sen-ai.fr` redirige vers l'apex
- [ ] Login app + **Google OAuth** (callback `https://sen-ai.fr/api/auth/google/callback` — inchangé car même domaine)
- [ ] `GET https://sen-ai.fr/api/health` → 200
- [ ] Un rapport `/r/{slug}/...html` se charge
- [ ] Génération d'un PDF compliance
- [ ] **Email** : envoi depuis et réception vers une adresse `@sen-ai.fr` (M365). Tester aussi un email transactionnel de l'app (Brevo) si applicable.
- [ ] Webhook Stripe atteint `/api/stripe/webhook` (relancer un event test depuis le dashboard Stripe)

---

## 6. Runbook — Phase 2 (optionnelle) : passage orange cloud + Origin CA

À faire **après** une phase 1 stable. Apporte anti-DDoS/WAF/CDN + supprime le renouvellement Let's Encrypt.

1. **Cloudflare Origin CA** : Dashboard → SSL/TLS → Origin Server → *Create Certificate* (validité 15 ans). Installer `fullchain` + `key` sur le VPS, pointer nginx dessus (remplace les chemins `/etc/letsencrypt/live/...`). SSL/TLS mode = **Full (strict)**.
2. **Patch nginx « real IP »** (sinon toutes les requêtes apparaissent avec une IP Cloudflare → le rate-limiting slowapi et l'audit log deviennent faux) :
   ```nginx
   # dans le server block 443 apex
   real_ip_header CF-Connecting-IP;
   # + set_real_ip_from <chaque plage IP Cloudflare> (https://www.cloudflare.com/ips/)
   ```
3. **Cache Rule** `sen-ai.fr/r/*` → **Bypass** (déjà documenté dans `docs/README-reports.md` : sinon l'edge CF garderait un rapport en cache des heures après un unpublish, en écrasant le `max-age=300`).
4. **Always Use HTTPS** = On ; **Min TLS 1.2**.
5. Passer `@` et `www` en **orange cloud**.

**Footguns orange cloud à valider :**
- ⏱️ **Timeout 100 s** : sur le plan Free, Cloudflare coupe une requête proxifiée à 100 s (erreur 524). nginx a `proxy_read_timeout 120s` sur `/api/`. Vérifier qu'**aucun endpoint synchrone** ne dépasse 100 s (les scans sont async via worker, OK ; surveiller la génération PDF compliance / weasyprint si elle est synchrone et lourde).
- 📦 Upload : limite Free = 100 MB ; nginx limite déjà à 10 MB → OK.
- 🔁 Le challenge ACME http-01 d'éventuels autres certs ne fonctionne plus pareil derrière le proxy → raison de plus de passer à Origin CA (plus de LE du tout).

---

## 7. 🔴 Action urgente : le cert VPS expire le 2026-07-01 et NE se renouvelle pas

**Diagnostic effectué le 2026-06-05 (SSH sur le VPS) — cause racine confirmée :**

- Cert `CN=sen-ai.fr` (+ `www`), ECDSA, Let's Encrypt, **expire 2026-07-01** (~23 j).
- `certbot.timer` est **actif** (2×/jour) et tourne bien — mais **échoue à chaque passage** :
  ```
  Failed to renew certificate sen-ai.fr with error: Could not bind TCP port 80
  because it is already in use by another process (such as a web server).
  ```
- **Cause** : le renouvellement est configuré en mode **`standalone`** (`/etc/letsencrypt/renewal/sen-ai.fr.conf` → `authenticator = standalone`). certbot veut prendre le port 80 lui-même, mais le **container nginx le détient déjà**. L'émission initiale du 2 avril a marché car nginx était arrêté ; depuis, plus aucun renouvellement possible.
- **Sans action, le site passe en erreur TLS le 1er juillet.**

### Fix immédiat (stopper l'hémorragie — buys 90 jours)
Renouvellement one-shot en libérant brièvement le port 80 (~60 s de downtime) :
```bash
cd /root/sen-ai-website
docker compose stop nginx
certbot renew --force-renewal      # standalone peut alors binder le port 80
docker compose start nginx
docker compose restart nginx       # footgun cache IP (cf project_nginx_ip_cache)
```

### Fix durable (au choix, à câbler ensuite)
- **Option webroot** (zéro downtime, reste sur LE) : ajouter dans le server-block port 80 de `nginx.conf` un `location ^~ /.well-known/acme-challenge/ { root /var/www/certbot; }` AVANT le `return 301`, monter un volume webroot dans le container nginx, puis passer le renewal en `authenticator = webroot` + `webroot_path = /var/www/certbot` + deploy-hook `docker compose exec nginx nginx -s reload`.
- **Option DNS-01 Cloudflare (cible recommandée)** : **une fois la phase 1 faite** (DNS chez Cloudflare), installer `certbot-dns-cloudflare` + un token API Cloudflare scoped *Zone:DNS:Edit*, et passer le renewal en `authenticator = dns-cloudflare`. Plus aucune dépendance au port 80, marche en grey **et** en orange cloud. C'est la fin définitive du problème.
- **Option Origin CA (si/quand orange cloud, phase 2)** : cert Cloudflare Origin 15 ans sur nginx → on abandonne Let's Encrypt.

C'est l'élément le plus **time-sensitive** du dossier — le fix immédiat est à faire dans les ~3 prochaines semaines quoi qu'il arrive, idéalement maintenant.

---

## 8. Améliorations d'architecture inspirées de flair-ai-hub (post-migration)

`flair-ai-hub` (site statique Astro) déploie sur **Cloudflare Workers/Pages** via `wrangler.jsonc`, ou sur VPS via **Caddy**, avec un fichier **`_headers`** pour la sécurité. sen-ai n'est **pas** un site statique (SSR + API + DB + workers), donc **pas de lift-and-shift vers Workers**. Mais 3 idées transposables :

1. **Caddy à la place de nginx** (cf Décision C) : auto-HTTPS + renouvellement auto + upstream dynamique → tue les footguns « cert manuel » et « 502 IP cache ». Migration nginx→Caddy = chantier séparé, à isoler (déployer + smoke-test entre chaque changement risqué, cf feedback projet).
2. **En-têtes de sécurité centralisés** : `flair-ai-hub` pose CSP / HSTS / X-Frame-Options dans `public/_headers`. En orange cloud, on peut poser HSTS + en-têtes via **Cloudflare Rules / Transform Rules** plutôt que de les disperser dans nginx.
3. **`/r/` sur Cloudflare** : à terme, les rapports statiques pourraient être servis par Cloudflare (R2 + Pages) au lieu du volume nginx — mais le `max-age=300 / unpublish` impose la Cache Rule Bypass ; gain faible, pas prioritaire.

---

## 9. Rollback

- **Avant bascule NS** : aucun changement de prod, rien à annuler.
- **Après bascule NS, en grey cloud** : remettre `ns1/ns2.o2switch.net` chez le registrar o2switch. Propagation accélérée par les TTL abaissés à 300 s (pré-requis §4.3). La zone o2switch d'origine étant intacte, retour à l'état initial.
- **Phase 2 orange cloud** : repasser l'enregistrement concerné en grey cloud (effet immédiat côté Cloudflare), et restaurer le cert LE sur nginx si on avait basculé sur Origin CA.

---

## 10. Séquencement recommandé

1. **Cette semaine** : §7 — sécuriser le cert VPS (urgent, indépendant).
2. **Phase 1** : DNS chez Cloudflare en grey cloud (zéro risque), valider §5.
3. **Phase 2** (quand stable) : orange cloud + Origin CA + real-IP + Cache Rule.
4. **Phase 3** (avant 2026-09-09) : transfert/relocalisation de l'enregistrement `.fr`, non-renouvellement o2switch (~185 €/an économisés).
5. **Plus tard, optionnel** : nginx → Caddy.

## Décisions ouvertes à confirmer
- [ ] Décision A : grey-first puis orange, ou directement orange ?
- [ ] Décision B : registrar cible pour le `.fr` (vérifier support Cloudflare `.fr`) ?
- [ ] Décision C : adopter Caddy, ou rester nginx ?
