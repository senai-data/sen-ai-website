# Manual QA Test Scenario — "Suggest Alternative Media" Feature

**Target:** https://sen-ai.fr/app/content (Content Pipeline Kanban)
**Feature:** Replace the target media partner of a `netlinking_article` card via the "Find alternative" modal that suggests ranked buyable media.
**Estimated runtime:** ~20 minutes
**Backend endpoints under test:** `POST /content-items/{id}/suggest-media`, `GET /content-items/{id}/suggest-media-result`, `POST /content-items/{id}/accept-suggestion`

---

## 1. Preconditions

Before starting, confirm ALL of the following:

- [ ] **Logged in** as a test account. Use **data@sen-ai.fr** (admin — sees all clients/scans) for full coverage. Have a second non-admin account ready only if you want to verify access scoping (optional).
- [ ] You are on **https://sen-ai.fr/app/content** and the **Kanban board renders** with at least the "To create" column.
- [ ] At least **one `netlinking_article` card** exists in the **"To create"** column (status `identified` or `draft`). Netlinking cards are marked with a `🔍` prefix on the scanned-domain line and a purple accent. If none exist, run a scan that produces netlinking opportunities, or filter the board with `?content_type=netlinking_article`.
- [ ] The card's question must be a **non-safety intent** (e.g. "best routine to combine X", NOT "should I stop taking X / side effects / contre-indications") so suggestions are eligible. Keep ONE safety-intent netlinking card handy for Test Case 11.
- [ ] The client has **content credits > 0** (needed for the "Search the web" test). Check Settings → credits. Have a known credit balance noted: `__________` so you can verify debit/refund.
- [ ] You have a card available where the **auto-picked media has NO price** OR has **no `target_url`** at all (this is what makes the "Find alternative" button appear — see TC1).
- [ ] Browser console open (F12) is recommended to observe network calls and catch silent JS errors, but not required.

**Reference — what the modal columns/labels should say (plain language, NO jargon):**
- Authority badges render as: **Major media** / **Mid-size** / **Small / niche** / **Not rated** (NEVER "Tier-1", "DA tier", "Babbar", "TF/CF").
- Source labels: *Cited in your scan* / *Cited elsewhere* / *Trusted source* / *Our database* / *AI search*.
- Authority shown as "Authority N/100", price as "€N".

---

## 2. Test Cases

### TC1 — "Find alternative" button visibility
**Steps:**
1. On the Kanban "To create" column, locate a **`netlinking_article`** card whose media is **not yet committed** (no `target_url`, OR auto-picked media with a null price).
2. Observe the card footer (idle state, not "Generating").

**Expected:**
- A **`🔄 Find alternative`** button (white with purple border) is shown next to the Generate Article / "Set media URL →" action.
- Now check a **`faq`** card → the button is **ABSENT**.
- Check a `netlinking_article` card that already shows **`✓ Picked`** (i.e. `target_url_source = media_replacement`) → the button is **ABSENT** (hidden after commit).
- Check a `netlinking_article` card with a media that already has a **price** shown (💰 €N from auto-pick) and no null-price → button **ABSENT**.

- [ ] PASS / FAIL

---

### TC2 — Free suggestions load (sources 1–4) + breakdown renders + plain language
**Steps:**
1. Click **`🔄 Find alternative`** on an eligible card.
2. Modal opens titled **"Find alternative media"** with the topic + question shown under the title.
3. Wait for loading spinner ("Searching alternative media…") to resolve (typically 1–3s).

**Expected:**
- Up to **5 suggestion rows** appear, each with: domain name, an **authority badge** (Major media / Mid-size / Small-niche / Not rated), "Authority N/100" when known, "€N" when priced, a source label (e.g. *Cited in your scan*, *Our database*).
- Each row shows **reasons** (green `✓` lines, e.g. "Cited by AIs on this exact question", "Well-established, trusted site (authority N/100)") and/or **risks** (amber `⚠` lines, e.g. "No known price — you'll need to contact the media directly").
- **No jargon** anywhere visible to the user: confirm you do NOT see "Tier-1", "DA", "TF", "CF", "RD", "Babbar", "k-anonymity". (Note: the page-card tooltip on the button mentions "Babbar/LinkFinder" in its hover title — acceptable, it's a tooltip; the modal body must stay plain.)
- Footer shows **"1/5 attempts used"**.

- [ ] PASS / FAIL

---

### TC3 — Match vs Avoid competitor toggle changes the list
**Steps:**
1. With the modal open, note the current ordered list of domains (default strategy = **Follow competitors**).
2. Click the **"Avoid competitors"** radio.
3. Wait for the auto-refresh (toggling `@change` re-fetches).
4. Compare the new list.

**Expected:**
- The list **re-ranks / changes**. Under "Follow competitors", surviving rows should carry a reason like "Your competitors are already cited here: <names>". Under "Avoid competitors", rows should carry "No competitor here — clear ground to stand out" (or the strategy-fallback banner appears — see TC10).
- Footer attempt count **increments** with each toggle (each re-fetch is one attempt against the cap of 5 — be mindful not to exhaust the cap during this test; use a fresh card if needed).

- [ ] PASS / FAIL

---

### TC4 — "Only media with a known price" toggle filters
**Steps:**
1. Open the modal on a fresh eligible card. The **"Only media with a known price"** checkbox defaults **ON** (checked).
2. Note how many rows have a "€N" price vs "—".
3. **Uncheck** the box → list re-fetches.

**Expected:**
- With the box **ON**, every row shows a real price (no price-less media).
- With the box **OFF**, additional media with **no known price** may appear; those rows carry the risk line "No known price — you'll need to contact the media directly".
- Toggling back ON removes the price-less rows again.

- [ ] PASS / FAIL

---

### TC5 — Reject removes a suggestion and excludes it on refresh
**Steps:**
1. Open the modal on a fresh card (note attempts used).
2. Pick a row, click its **"Reject"** link.
3. Observe the row.
4. Click **"↻ Refresh (5 more)"** in the footer.

**Expected:**
- Immediately after Reject, the row **disappears** from the list (local exclude).
- Footer shows "· **1 rejected in this session**".
- After Refresh, the rejected domain **does NOT reappear** (it's passed in `exclude_domains`). Attempts-used increments.

- [ ] PASS / FAIL

---

### TC6 — Accept patches the card (target_url, ✓ Picked chip, price, button hidden, reload)
**Steps:**
1. Open the modal on a fresh eligible card. Note the card's current media (or lack thereof).
2. Pick a suggestion (ideally one WITH a price) and click **"Accept →"**.

**Expected:**
- Modal **closes** and the page **reloads automatically**.
- After reload, the same card now shows a purple **`→ <media domain/title>`** chip with a bold **`✓ Picked`** badge (hover title: "You replaced the auto-picked media with this one via Find alternative.").
- If the accepted media had a price, a **`💰 €N`** chip appears; if it had authority, a **`DA N`** chip appears (this is the card chip — internal label "DA" here is acceptable as it's the established card chip, but the modal itself stayed plain).
- The **`🔄 Find alternative` button is now GONE** on that card (committed → `target_url_source = media_replacement`).

- [ ] PASS / FAIL

---

### TC7 — "Search the web (1 credit)" opt-in flow
**Steps:**
1. Note client's current content credit balance: `__________`.
2. Open the modal on a fresh eligible card.
3. In the footer, click **"🔍 Search the web (1 credit)"**.
4. A **browser confirm dialog** appears: "Search the web for more media? This uses 1 content credit (refunded if it finds nothing new)." → click **OK**.
5. Observe the loading state.
6. Wait for completion (can take **1–3 minutes** — this is expected, see Limitations).

**Expected:**
- Confirm dialog text matches above. Cancelling it does nothing (no fetch, no debit).
- After OK, loading copy reads **"Searching the web for media… this can take 1-3 minutes."**
- On completion, an outcome notice appears:
  - If new media found: purple notice "🔍 AI web search added **N** new media (tagged *AI search* below)." and those rows carry the *AI search* source label.
  - If nothing new: gray notice "🔍 AI web search found no new buyable media for this topic. **Your credit was refunded.**"
- Verify credit balance: **debited by 1 if new media added**, OR **refunded to original** if 0 new (re-check Settings).

- [ ] PASS / FAIL

---

### TC8 — Cap reached after 5 attempts (429 → friendly message)
**Steps:**
1. On a single card, repeatedly trigger fetches (open modal = attempt 1, then click Refresh / toggle strategy) until the footer shows **"5/5 attempts used"**.
2. Attempt a **6th** fetch (click Refresh, or toggle a filter, or click Search the web).

**Expected:**
- The **Refresh** and **Search the web** buttons become **disabled** (greyed, `cursor-not-allowed`) once `attemptsUsed >= attemptsCap`.
- If a 6th call does fire (e.g. via a filter toggle), the server returns **429** and the modal shows a friendly error: "You've explored 5 suggestion runs on this item. That's our budget cap. Pick one from history, accept manually, or reject the item if no good fit exists."
- No crash / no infinite spinner.

- [ ] PASS / FAIL

---

### TC9 — Insufficient credits on web search (402)
**Steps:**
1. Use a client/workspace with **0 content credits** (or spend them down). If you only have the admin account, switch to a workspace whose credit balance is 0.
2. Open the modal on a fresh eligible card.
3. Click **"🔍 Search the web (1 credit)"** → confirm **OK** on the dialog.

**Expected:**
- Server returns **402** and the modal shows: "Searching the web for media costs 1 content credit. You're out — buy more on Settings, or use the free suggestions already shown."
- The previously-loaded **free suggestions remain visible** (the page doesn't blank out).
- Credit balance unchanged (no debit went through).

- [ ] PASS / FAIL

---

### TC10 — Strategy fallback banner
**Steps:**
1. Find a card on a **dense scan** where the strict strategy filter is likely to empty out (e.g. a topic where every candidate has a competitor co-cited).
2. Open the modal, switch to **"Avoid competitors"** (or Follow, depending on the data).

**Expected:**
- When the strict filter would yield zero, an **amber banner** appears above the list: "No media matched your strategy strictly." with the appropriate follow-up:
  - Avoid: "All candidates have at least one competitor co-cited. Showing best ranked anyway — consider Match strategy or relax filters."
  - Follow: "No candidate has a competitor co-cited. Showing best ranked anyway — try Avoid strategy."
- The list still shows the **best-ranked suggestions** (not empty).

- [ ] PASS / FAIL  /  N/A (couldn't reproduce dense-scan condition)

---

### TC11 — Intent-not-eligible (safety question)
**Steps:**
1. Open the modal on a **safety-intent** netlinking card (e.g. "should I stop using X", "side effects", "contre-indications", "effets indésirables").

**Expected:**
- After the fetch resolves, an **amber block** appears: heading "**This question can't get netlinking suggestions.**" followed by the message body, e.g. "Question intent '<category>' blocks third-party brand placement (compliance / editorial fit). Replace with an FAQ on your own site instead."
- **No suggestion rows** are shown.

- [ ] PASS / FAIL

---

### TC12 — Modal close (Escape / backdrop / ×)
**Steps:**
1. Open the modal. Press **Escape** → should close.
2. Re-open. Click the dark **backdrop area** outside the white panel → should close.
3. Re-open. Click the **×** button (top-right) → should close.
4. Re-open. Click **Cancel** in the footer → should close.

**Expected:**
- All four methods close the modal cleanly. State resets (re-opening on another card shows that card's data, no stale suggestions/rejections from the previous session).
- Clicking **inside** the white panel does NOT close it (backdrop close is scoped to `@click.self`).

- [ ] PASS / FAIL

---

### TC13 — Tooltip legend readable, not clipped
**Steps:**
1. Open the modal. Hover the dotted-underlined **"Strategy:"** label, then the **"Only media with a known price"** label.
2. Read the always-visible **legend block** under the controls (Authority / Major-Mid-Niche / € price definitions).

**Expected:**
- Tooltips render fully, are **not clipped** by the modal edge or scroll area, and read in plain language (Follow vs Avoid; On vs Off).
- The legend `<dl>` is visible without scrolling, one definition per line:
  - "Authority — how trusted a site is across the web — 0-100, based on its backlinks"
  - "Major / Mid-size / Niche — media ranked by that authority score"
  - "€ price — what the media charges to publish your article"

- [ ] PASS / FAIL

---

## 3. Edge Cases / Known Limitations — DO NOT FILE AS BUGS

- **Web search often refunds on well-covered topics.** On mainstream / Pierre-Fabre-style topics that are already densely cited in our DB, the AI web search frequently finds **0 new buyable media** and **auto-refunds the credit**. The "found no new buyable media … Your credit was refunded" notice is the **correct, expected** outcome, not a failure.
- **"Not rated" badges are common early.** Audience-match and authority (DA) enrichment fills in **gradually (~300 media/night** via the nightly catalog job). Early on, many suggestions legitimately show **"Not rated"** and have no "Authority N/100" line. This is sparse-data, not a bug.
- **Web search latency 1–3 minutes.** Source 5 does a live LLM web search + LinkFinder price re-validation + scoring. A 1–3 min wait (spinner up to ~6 min timeout) is **normal**. The DB-only path stays at seconds.
- **DB-only timeout message mentions the worker being busy.** If a DB-only fetch times out (>90s), the "the worker may be busy generating an article" message is **by design** — article generation shares the worker queue. Retry after a moment.
- **Audience/voice reasons may be absent** when the workspace/brand brief has no `target_audience` / `editorial_voice` set — those scoring lines stay dark on purpose.
- **Prices shown HT (hors taxes)** and formatted FR-style (e.g. "€1 200"). Expected.
- **Some media show no price ("—") even with the price filter OFF** — these are outreach-only ("contact the media directly"). Expected when the price box is unchecked.

---

## 4. Regression Checks (same cards must still work)

### RC1 — Generate Article still works
- [ ] On a `netlinking_article` card **with a committed media** (target_url set), the **`⚡ Generate Article`** purple button is present and, when clicked, transitions the card to the **Generating** state (progress bar + "Article gen takes 5-15 min"). No regression from the suggest-media feature.

### RC2 — "Set media URL →" still works
- [ ] On a `netlinking_article` card with **no** target_url, the amber **"Set media URL →"** link is present and navigates to `/app/content/{id}` (validation page) where a media URL can be set manually.

### RC3 — Generate FAQ still works
- [ ] On a `faq` card with a target_url, the coral **`⚡ Generate FAQ`** button is present and works (no "Find alternative" button leaked onto FAQ cards).

### RC4 — Accepted media survives reload + appears on validation page
- [ ] After TC6 (Accept), reload the page manually: the **`✓ Picked`** chip + price/DA persist. Open the card's validation page (`/app/content/{id}`) and confirm the accepted media URL is the target there too.

### RC5 — Board filters intact
- [ ] The `?content_type=...` and `?scan_id=...` filters, and `?show_rejected=1`, still render the board correctly after exercising the modal.

---

**Sign-off:** Tester ____________  Date ____________  Build/commit ____________
