# HN launch post : "We watched AI crawlers read our site for 30 days"

> Statut : **PAGE LIVE 2026-07-18** - https://sen-ai.fr/ressources/ai-crawlers-30-days/
> Titre retenu (décision déléguée) : "ChatGPT visited our site 117 times last
> month. It sent us zero clicks". La soumission HN elle-même reste l'action de
> David (son compte, sa présence dans les commentaires).
> Les chiffres viennent de l'analyse des logs nginx du 2026-07-17 (30 jours,
> origine derrière Cloudflare, cache 0,17% donc ~99,8% du trafic visible à
> l'origine) croisée avec le RUM Cloudflare.

---

## We watched AI crawlers read our site for 30 days. Here is who actually showed up.

We build a small SaaS that measures how often AI assistants mention a brand.
At some point the obvious question hit us : what does OUR traffic look like ?
So we pulled 30 days of origin nginx logs (we sit behind Cloudflare with a
near-zero cache ratio, so the origin sees ~everything), classified every
user agent, and cross-checked against real-user monitoring.

**158,000 requests. 4,734 unique IPs. About 20 human visits.**

### The zoo, by the numbers

| Who | Requests | Unique IPs |
|---|---|---|
| Generic crawlers and bots | 61,264 | 368 |
| Scripts and scanners (python, curl, zgrab...) | 41,267 | 718 |
| Empty user agent | 15,207 | 1,049 |
| Googlebot | 8,306 | 50 |
| ClaudeBot (Anthropic) | 669 | 48 |
| OAI-SearchBot (ChatGPT Search index) | 236 | 33 |
| GPTBot (OpenAI training) | 213 | 26 |
| Bytespider (TikTok) | 188 | 86 |
| CCBot (Common Crawl) | 125 | 13 |
| PerplexityBot | 118 | 12 |
| **ChatGPT-User (live visits during chats)** | **117** | **13** |
| Applebot / Amazonbot / Meta / Google-Extended | 187 | 46 |

The "human-looking" browsers ? 29,000 requests from 2,824 IPs. Our RUM
beacon, which only fires in real browsers, counted about 20 visits over the
same period. Almost every Mozilla/5.0 in our logs is a bot wearing a mask.

### Five things that surprised us

**1. ChatGPT visits your site live, in the middle of conversations.**
ChatGPT-User is not a crawler filling an index. It fires when someone asks
ChatGPT a question and the model decides to fetch a page to answer. We got
117 of those. Our site was consulted inside conversations we will never see.

**2. Nobody clicks through - yet.** Referrer analysis over 30 days :
Google sent ~70 clicks, Twitter ~80, Hacker News 30, Bing 40, Reddit 30.
chatgpt.com sent exactly zero. AI assistants read us hundreds of times and
sent us nobody. The asymmetry is the whole story of AI-era publishing :
you feed the answer machine, the answer machine keeps the user.

**3. Your "unique visitors" metric is a bot census.** Cloudflare's overview
proudly showed 2,030 unique visitors. Real humans measured by RUM : ~20
visits. If you make decisions on proxy-level uniques, you are optimizing
for scrapers.

**4. Vulnerability scanners now impersonate AI crawlers.** A chunk of the
requests wearing GPTBot and ClaudeBot user agents were hunting /.env,
/.git/HEAD, secrets.json and firebase-adminsdk.json. The theory writes
itself : sites are starting to allowlist AI bots in their WAF rules, so
scanners dress up as them. Verify AI crawlers by their published IP ranges,
never by user agent alone.

**5. What AI crawlers actually read is boring and instructive.**
robots.txt (512 hits), the sitemap (310), the homepage, /pricing, and our
methodology page. They walk the front door. If your robots.txt, sitemap and
core pages are a mess, that mess IS your AI presence.

### What we changed after reading our own logs

- We keep AI training bots ALLOWED. For a small B2B product, being in the
  training data and the answer indexes is distribution, not theft. Your
  call may differ ; make it consciously instead of by default.
- We stopped looking at proxy-level "unique visitors" entirely. RUM or
  nothing.
- We treat spoofed AI user agents as scanner traffic in our own analytics.
- Hashed static assets now ship immutable cache headers, because 99.8% of
  requests were reaching the origin for no reason.

We build sen-ai.fr, which does this kind of measurement from the other
side : asking the assistants themselves how often they mention a brand,
question by question. The logs above are the inbound half of the same
picture. Happy to answer anything about the methodology.

---

## Titres suggérés (HN)

1. "We watched AI crawlers read our site for 30 days. ~20 of 4,734 visitors were human"
2. "ChatGPT visited our site 117 times last month. It sent us zero clicks"
3. "Vulnerability scanners are now impersonating GPTBot to get past WAF rules"

Le titre 2 est le plus fort (asymétrie lisible en une ligne, chiffres réels).
Le titre 3 est un angle sécurité qui peut sur-performer mais recentre la
discussion loin du produit.

## Checklist avant publication

- [ ] Relecture David + choix du titre
- [ ] Publier la version EN sur une page stable (ex. /ressources/ai-crawlers-30-days/)
- [ ] Vérifier robots.txt propre avant l'afflux (le post le mentionne)
- [ ] Soumettre un matin US (14h-16h heure FR), rester dispo pour les commentaires
- [ ] Ne PAS utiliser de compte HN neuf ; pas de vote ring
