# Client reports — sen-ai.fr/r/

Standalone HTML client deliverables (Pierre Fabre etc.) published at unguessable
URLs. Served as **pure static** by Nginx, fully isolated from the Astro app.

## How it works

```
┌─ Upload via UI: https://sen-ai.fr/app/admin/reports
│
├─ FastAPI (api/routers/reports.py)
│     • generates a 12-char crypto-random alnum slug
│     • injects <meta name=robots content="noindex, nofollow"> into <head>
│     • writes file at /opt/sen-ai/reports/{client}/{period}/{slug}/{name}.html
│     • creates symlink /opt/sen-ai/reports/_serve/{slug} → ../{client}/{period}/{slug}/
│     • inserts a row into the `reports` DB table (audit-logged)
│
└─ Nginx serves /r/{slug}/{name}.html via alias of /var/www/reports/_serve/
      • adds X-Robots-Tag: noindex, nofollow, noarchive
      • adds X-Content-Type-Options, Referrer-Policy, X-Frame-Options
      • short cache (max-age=300) so unpublishes propagate fast
      • whitelist regex on URL — anything else returns 404
```

The slug is the only public identifier. Client and period stay private to anyone
with VPS access (the human-organized hierarchy is read-only inside the Nginx
container, and not browsable since `autoindex off` and the URL whitelist).

## Disk layout on VPS

```
/opt/sen-ai/reports/
├── _serve/                                  ← Nginx serves from here
│   └── aB3cD4eF5gH6  →  ../pierrefabre/avril-2026/aB3cD4eF5gH6/
└── pierrefabre/
    └── avril-2026/
        └── aB3cD4eF5gH6/
            └── rapport-q2.html
```

## One-time VPS setup

```bash
ssh -i ~/.ssh/id_ed25519 root@135.181.156.218 "
  mkdir -p /opt/sen-ai/reports/_serve &&
  chmod 755 /opt/sen-ai/reports /opt/sen-ai/reports/_serve
"
```

## Deployment of the feature itself

```bash
# 1. Local: apply migrations to commit / scp these files
#    - api/migrations/017_reports.sql
#    - api/models.py            (Report class added)
#    - api/services/reports_publisher.py
#    - api/routers/reports.py
#    - api/main.py              (router registration)
#    - nginx/nginx.conf         (location /r/ block)
#    - docker-compose.yml       (volumes for nginx + api)
#    - public/robots.txt        (Disallow: /r/)
#    - src/layouts/DashboardLayout.astro  (sidebar link)
#    - src/pages/app/admin/reports.astro  (UI page)

# 2. scp everything to /root/sen-ai-website/ on the VPS
#    (use existing scp+rebuild workflow)

# 3. On VPS — apply migration
ssh -i ~/.ssh/id_ed25519 root@135.181.156.218 \
  "docker exec -i senai-postgres psql -U senai -d senai" \
  < api/migrations/017_reports.sql

# 4. On VPS — recreate containers (nginx + api need new volume mounts; astro needs robots.txt)
ssh -i ~/.ssh/id_ed25519 root@135.181.156.218 "
  cd /root/sen-ai-website &&
  docker compose up -d nginx &&
  docker compose build api && docker compose up -d api &&
  docker compose build astro && docker compose up -d astro &&
  docker compose restart nginx
"
```

## Daily use

1. Connect to https://sen-ai.fr/app/admin/reports (superadmin only)
2. Drag-drop the .html file
3. Type `client` (autocomplete after first use)
4. Period defaults to current French month (e.g. `avril-2026`)
5. Click **Publish report** — URL is auto-copied to clipboard
6. Paste the URL into your client email

## Verification (run from local machine)

```bash
URL=https://sen-ai.fr/r/<slug>/<name>.html

# 1. report accessible
curl -I "$URL"

# 2. all security headers present
curl -sI "$URL" | grep -iE "x-robots-tag|cache-control|x-content-type|referrer-policy|x-frame"
# expect:
#   X-Robots-Tag: noindex, nofollow, noarchive
#   X-Content-Type-Options: nosniff
#   Referrer-Policy: no-referrer
#   X-Frame-Options: SAMEORIGIN
#   Cache-Control: private, max-age=300, must-revalidate

# 3. /r/ alone → 404
curl -o /dev/null -s -w "%{http_code}\n" https://sen-ai.fr/r/

# 4. /r/{slug}/ alone → 404
curl -o /dev/null -s -w "%{http_code}\n" https://sen-ai.fr/r/aB3cD4eF5gH6/

# 5. inexistent slug → 404
curl -o /dev/null -s -w "%{http_code}\n" https://sen-ai.fr/r/zzzzzzzzzzzz/x.html

# 6. robots.txt blocks /r/
curl -s https://sen-ai.fr/robots.txt | grep "/r/"
# expect: Disallow: /r/

# 7. Googlebot user-agent still gets noindex header (defense in depth)
curl -sI -A "Googlebot" "$URL" | grep -i x-robots-tag
```

## On the VPS — manual inspection

```bash
ssh -i ~/.ssh/id_ed25519 root@135.181.156.218 \
  "ls -R /opt/sen-ai/reports/pierrefabre/"

# Astro should NOT see /r/* requests:
ssh -i ~/.ssh/id_ed25519 root@135.181.156.218 \
  "docker logs senai-astro --since 5m 2>&1 | grep '/r/' || echo 'OK — astro never saw /r/'"

# Nginx should:
ssh -i ~/.ssh/id_ed25519 root@135.181.156.218 \
  "docker logs senai-nginx --since 5m 2>&1 | grep '/r/' | head"
```

## Cloudflare migration (sept. 2026)

When you move DNS to Cloudflare, add a Cache Rule for `sen-ai.fr/r/*`:
- **Cache Level: Bypass** (otherwise Cloudflare's edge would override the
  `max-age=300` we send and a hot rapport could persist for hours after
  unpublish).

Nothing else changes — the Nginx block, the volumes, the scripts, the layout
on disk all stay identical.
