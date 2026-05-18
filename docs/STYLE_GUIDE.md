# SaaS copy style guide

This guide governs any text users see in sen-ai.fr - tooltips, button labels,
error messages, empty states, onboarding copy, validation messages, and
section descriptions. The rules align with the `shared/natural_writing/`
service that processes LLM output, so user-facing copy and LLM-generated
copy read with the same voice.

Apply this guide to **new** copy from now on. Existing copy gets refactored
opportunistically when nearby files are touched (no big-bang retro audit).
The pre-commit lint hook (`scripts/lint_ai_tells.py`) flags violations
on staged files but does not block commits - it's a nudge, not a gate.

---

## The 6 universal rules

These apply to every surface, every length, every language.

### 1. Use `is`, `has` instead of `represents`, `embodies`, `constitutes`

| Avoid | Prefer |
|---|---|
| "Topic diversity represents the lexical spread" | "Topic diversity is the lexical spread" |
| "This score embodies the editorial review" | "This score is the editorial review verdict" |
| "Sources constitutes a key signal" | "Sources is a key signal" |

LLMs over-use copula avoidance ; humans pick the simplest verb. Same energy
applies to UI copy : "Length is 1 960 words" beats "Length represents the
total word count of the article".

### 2. No signposting

Cut "Let's dive in", "Here's what you need to know", "In this section",
"Voyons maintenant", "Plongeons dans le sujet". The label IS the
signpost - explanatory pretext is filler.

| Avoid | Prefer |
|---|---|
| "Let's look at how this works" | "How this works" |
| "Voyons les sources citées" | "Sources citées" |
| "Here's the breakdown" | "Breakdown" |

### 3. No hedging stack

One qualifier maximum. Stop chaining "could potentially possibly" or
"may sometimes generally".

| Avoid | Prefer |
|---|---|
| "This might possibly improve ranking" | "This may improve ranking" |
| "We could potentially try regenerating" | "Try regenerating" |
| "The article could perhaps be too long" | "The article may be too long" |

### 4. Vary sentence length but keep tooltips short

Long-form (articles, newsletter) : alternate 5-8-word punchy lines with
25-35-word longer ones. Tooltips / labels / errors : aim for under 12 words
per sentence. Two short sentences beat one long compound.

| Avoid | Prefer |
|---|---|
| "Topic diversity score measuring how varied your vocabulary is, ranging from 0 to 100, with values between 30 and 70 being the sweet spot" | "Topic diversity score, 0-100. Sweet spot 30-70." |

### 5. No em-dashes anywhere

Em-dashes (`—`, `–`) are a strong LLM tell. Use hyphens (`-`), commas, or
periods instead. Wikipedia "Signs of AI writing" calls this out explicitly.

| Avoid | Prefer |
|---|---|
| `Target SOSEO - dynamic` (em-dash) | `Target SOSEO - dynamic` (hyphen) |
| `the article - generated yesterday - is here` | `the article, generated yesterday, is here` |

The unicode codepoint difference matters - the lint hook flags `—` and `–`.

### 6. No vague attributions

If you cite a source, name it. "Experts believe", "studies show", "research
suggests" without a named source is an AI tell AND an editorial weakness.

| Avoid | Prefer |
|---|---|
| "Experts recommend regenerating" | "The validator recommends regenerating" |
| "Studies show this helps SEO" | "YourTextGuru data shows this lifts SOSEO" |

---

## The vocabulary blacklist (French + English)

Drawn from the Wikipedia "Signs of AI writing" list. These words are
statistically over-represented in LLM output. Avoid in any user copy.

### English

`delve`, `delving`, `leverage`, `leveraging`, `navigate`, `navigating`,
`tapestry`, `nuanced`, `intricate`, `crucial`, `pivotal`, `vital`,
`pivot`, `pivots`, `seamlessly`, `furthermore`, `moreover`, `additionally`,
`enhance`, `enhances`, `enhancing`, `foster`, `fostering`, `embark`,
`unleash`, `unlock`, `endeavor`, `myriad`, `plethora`, `glean`, `harness`,
`testament`, `align with`, `align toward`, `dive into`, `dive deep`,
`it is important to note`, `it should be noted`, `in essence`,
`at its core`, `the future looks`, `looking ahead`.

### French

`représente`, `incarne`, `constitue`, `joue un rôle crucial`,
`moment charnière`, `incontournable`, `indéniablement`, `indubitablement`,
`il est important de noter`, `il convient de`, `il est essentiel de`,
`il est crucial de`, `il est fondamental de`, `en conclusion`,
`en somme`, `en définitive`, `force est de constater`,
`l'avenir s'annonce prometteur`, `cependant`, `toutefois`, `néanmoins`,
`en revanche`, `par ailleurs`, `dans cet article`, `nous allons explorer`,
`plongeons dans`, `découvrons ensemble`, `voyons maintenant`,
`méritent une attention particulière`, `n'hésitez pas à`,
`les experts estiment`.

These words are not banned in code comments or commit messages - only in
user-facing copy and prompts. A developer doc that says "we leverage
PostgreSQL" is fine ; a tooltip that says "Leverage SOSEO to improve
visibility" is not.

---

## Surface-specific guidance

### Tooltips (`<span title="...">` or `ⓘ` hover)

- Under 12 words per sentence
- Two short sentences max
- Start with the noun or verb, not a determiner
- Avoid trailing "..." or "ⓘ" inside the tooltip itself
- SEO jargon in tooltips is OK for power users (SOSEO, DSEO, RAPP,
  fan-out, grammes) - they expect technical terms there

Example :

| Bad | Good |
|---|---|
| "This shows you the topic diversity which is the lexical spread..." | "Topic diversity (DSEO) - 0-100. Sweet spot 30-70." |

### Button labels

- 1-3 words
- Verb first (Generate, Approve, Reject, Refresh)
- No trailing emoji unless it's a status indicator (✓ Approve)

### Empty states

- 1-2 sentences explaining what the user sees and what to do
- End with a clickable action

Example :

| Bad | Good |
|---|---|
| "There are no articles to show in this view at the moment because you have not yet generated any. To begin, please use the 'Generate Article' button below to..." | "No articles yet. Click 'Generate Article' to create your first." |

### Error messages

- State the problem, not the cause
- One actionable sentence telling the user what to do next
- No "Oops" / "Sorry" filler
- No stack traces or error codes in user-facing copy

Example :

| Bad | Good |
|---|---|
| "An error occurred while attempting to fetch the latest data : ConnectionTimeoutError at line 247" | "Couldn't reach the server. Try again in a moment." |

### Onboarding copy

- One screen, one action
- Mention the cost or commitment when relevant ("~5-15 min, 3 credits")
- Skip the "Welcome to..." preamble - users know what site they're on

---

## When in doubt

Read the line aloud. If you wouldn't say it that way to a colleague over
coffee, rewrite it.

Show a draft to someone who hasn't seen it - if they pause on a word, that
word is wrong. The pause IS the signal, regardless of dictionary correctness.

The natural-writing service (`shared/natural_writing/`) handles this for
LLM output. For hand-written copy, you are the service.

---

## Related

- `shared/natural_writing/` - the runtime service for LLM output
- `scripts/lint_ai_tells.py` - the pre-commit linter for staged files
- `worker/seo_llm/src/humanizer.py` - the underlying anti-AI rule source
  (mirrored into `shared/natural_writing/humanizer.py`)
- `project_phase_nw_natural_writing.md` (memory) - architectural rationale
