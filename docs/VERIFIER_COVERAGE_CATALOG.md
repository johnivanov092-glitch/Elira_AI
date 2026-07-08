# Verifier Coverage Catalog & Matrix

**Audited:** 2026-07-07 · **commit:** `f20257c` · **machine-readable source:** [`backend/app/application/code_agent/verifier_catalog.yaml`](../backend/app/application/code_agent/verifier_catalog.yaml)

This is the code-grounded map of the code-agent's **verifier layer**: what a success
criterion *type* is, which tool can PROVE it, what evidence counts, what must **not**
count, and where coverage is still thin. It exists to stop the whack-a-mole loop
(live prompt → catch a hole → patch a matcher → next hole) by making coverage explicit
*before* the next batch of work.

The source of truth is the code — `taskspec.py` (classification + verdicts),
`criterion_closure.py` (missing-action plan), `tools/_web.py` (browser), `agent_loop.py`
(record wiring). This catalog **describes** that code; it is not yet consumed by it (see
[Implementation plan](#minimal-implementation-plan)).

---

## Coverage matrix

Support: **✅ supported** (classified + verifier + evidence + regression test) ·
**◐ partial** (works for a narrow shape; realistic phrasings fall through) ·
**✗ missing** (classifies as `generic`/misclassified → never verified).

| Criterion type | intent | verifier tool(s) | evidence that closes it | Support | FP risk |
|---|---|---|---|:--:|---|
| `local.fs.exists` — папка/файл создан, проект внутри X | `file_exists` | `path_exists` | present | ✅ | — |
| `local.fs.not_exists` | `file_not_exists` | `path_exists` | absent | ✅ | — |
| `remote.ssh.exists` | `file_exists` | `ssh_exists` / `ssh_read` | present / read ok | ✅ | — |
| `remote.ssh.not_exists` (cleanup) | `file_not_exists` | `ssh_not_exists` | absent (present → **fail**) | ✅ | — |
| `file.content.contains` | `content_contains` | `ssh_assert_contains` | file+pattern match | ✅ | — |
| `file.content.not_contains` | `content_not_contains` | `ssh_assert_not_contains` | file+pattern absent | ✅ | — |
| `cli.command.succeeds` (typecheck/build/test/…) | `command_check` | `run_bash` | exit 0 + known kind | ✅ | any-kind (accepted) |
| `server.started` | `server_started` | `run_server` / `ssh_port_check` | actual_url/LISTENING | ✅ | — |
| `http.page_open` | `page_open` | `http_api` / `browser` | 2xx / render | ✅ | — |
| `browser.dom_contains` | `dom_contains` | `browser` | tokens in rendered DOM (boundary-anchored) | ✅ | prefix-match **resolved** |
| `browser.interaction.fill_click_assert` | `dom_contains`+interaction | `browser(actions)` | post-action DOM has result | ✅ | — |
| `project.scope.created_under` | `file_exists` | `path_exists` | present | ✅ | — |
| `cleanup.confirmed` | `file_not_exists` | `ssh_not_exists` | absent | ✅ | — |
| `viewport_layout` — нет горизонтального скролла на mobile | `viewport_layout` | `browser(viewport=…)` | measured no-horizontal-overflow at the tested width | ✅ | width-bucket attribution (narrow≠desktop) |
| `project.scope.no_parent_changes` | *(→ constraint)* | — | *(can't verify without diff)* | ◐ | — |
| `cli.output.contains` — `cmd` выводит `INFO: 2` | `command_output` | `run_bash` | stdout/stderr contains text (one run closes many) | ✅ | — |
| `cli.command.fails_with_output` — выводит X и падает | `command_output`+nonzero | `run_bash` | text + exit≠0 | ✅ | — |
| `cli.output.not_contains` | `command_output` | `run_bash` | text absent | ◐ | — |
| `browser.form.select_checkbox_assert` — fill/select/checkbox/empty + click | `dom_contains`+interaction | `browser(actions)` | multi-step post-action DOM | ✅ | — |

⚠ = today classifies as `generic` (or misses the interaction flag) → the criterion can
never be confirmed and the run ends honest-partial.

---

## Negative rules (invariants — what must NEVER close a criterion)

These are as load-bearing as the positive rules; they are what keeps `completion_status`
honest instead of a rubber stamp.

- **grep / findstr** (via `run_bash`) is not a verdict.
- **local `read_file` / `grep` / `glob` / `project_map`** close nothing — `path_exists` is
  the only local existence verifier. *(Asymmetry to remember: `ssh_read` proves
  `file_exists`; local `read_file` does not.)*
- **`ssh_read`** proves existence only — never `content_contains` (use `ssh_assert_contains`).
- **`http_api` / HTTP 200** proves `page_open` only — never `dom_contains` (visible text).
- **a browser render** is the only thing that yields `dom_contains`; match is
  boundary-anchored so a value can't prefix-match a longer one.
- **a green build/typecheck** does not close a DOM / interaction criterion.
- **the model's final text** closes nothing — verdicts decide completion.
- a **conditional** criterion never hard-fails; unresolved → `skipped` (n/a).
- a criterion with **no deterministic verifier** stays `generic` → unverified (honest), never auto-confirmed.

---

## Gaps, ranked (evidence = the live smoke runs)

1. **CLI stdout/stderr verifier — ✅ DONE (Batch A).**
   New `command_output` intent + `run_bash` stdout/stderr/exit_code threaded to `record()`;
   one run closes several output criteria for the same command; negative case needs text +
   non-zero exit; closure groups by command; grep/read_file still prove nothing. Covers
   `log-summarizer` (INFO/WARN/ERROR/TOTAL + missing.log) and `csv-inventory-checker`
   (OK/WARN/DOWN/SUBNET). *Known limit:* an abstract `verifier видит X` with no run
   reference (the csv `OUT_OF_SCOPE` line) stays `generic` — phrase it with a run verb.
2. **Browser form grammar — ✅ DONE (Batch B).**
   `browser interaction: … показывает X` now classifies as `dom_contains`+interaction even
   without a rendered/DOM/на-экране word; the browser tool gained `select`/`check`/`uncheck`
   (empty-field is `fill value:""`), so a multi-step fill/select/check/click sequence runs
   for real and the post-interaction DOM (not text/grep) confirms the result token. Covers
   `backup-form-checker`. Verdict is positive-evidence: the tool executes the actions and
   returns the real DOM (verified with a live Playwright form test).
3. **Final-report honesty — ✅ DONE (Batch C).** The scrub neutralises status headers, model
   count claims ("17 verifier criteria"), and universal "all passed" claims when not confirmed
   — and now `scrub_success_marks` also neutralises the model **✅/✔/✓/☑/🟢/👍 glyphs → ▫**
   (only when `completion_status != confirmed`), so a model checkmark table/row can no longer
   *look* done beside the runtime "подтверждено N/M" block. The deterministic panel is the only
   source of "готово". `code: criterion_closure.scrub_success_marks`.
4. **`viewport_layout` — ✅ DONE (Batch D).** `browser(viewport="mobile"/"desktop"/{w,h})`
   sizes the page and MEASURES horizontal overflow: no overflow at the tested width → confirm,
   overflow → **fail** (a real red), no viewport requested → stays unconfirmed (honest). Width
   attribution — a "mobile" claim can't be confirmed by a desktop-width measurement (bucket
   mismatch → unconfirmed, never a false pass). Closure hint routes to the right preset. The
   residual limit (the tool measures overflow at whatever width the model set; it does not
   cross-check that the DOM at that width is the responsive layout) is the same class as the
   Batch B statically-present-token limit. `code: tools/_web.tool_browser` +
   `taskspec._verdict_outcome(viewport_layout)`.
   **`project.scope.no_parent_changes` — stays a constraint by design** (a diff/changed-paths
   verifier is deliberately NOT added — it's routed to constraints so it doesn't hang). Honest.

---

## Minimal implementation plan (proposed — not yet executed)

Batched by leverage; each batch is independently shippable with regression tests. **No code
changed until this catalog is approved.**

**Batch A — `command_output` verifier (closes gap 1).**
- New intent `command_output` in `_criterion_intent`: a criterion with a quoted command +
  an output verb (`выводит`/`печатает`/`prints`/`outputs`/`в вывод`) + expected token; a
  negative variant (`+ завершается с ошибкой`/`non-zero`) sets `expect_nonzero`.
- Thread `run_bash` **stdout/stderr + exit_code** to `record()` as evidence (today only a
  placeholder reaches it). New `_verifier_verdict("run_bash", …)` branch → `command_output`
  carrying output text + exit code; match = command-substring + expected text in output
  (+ exit condition). One run closes several output criteria for the same command.
- Closure: **group** open `command_output` criteria by command → one `run_bash(cmd)` action
  listing all expected outputs (don't re-run 5–10×).
- Negative rule kept: grep/read_file don't prove output.

**Batch B — browser form grammar (closes gap 2).**
- Recognise `browser interaction:` prefix + `показывает`/`после клика` as `dom_contains`
  + interaction even without a DOM-context word.
- `interaction_spec` → an **action LIST** (ordered fill/select/check/click), supporting
  empty value, `select <option>`, `checkbox <label>`, multi-field.
- `_apply_action`: add `select` (Playwright `select_option`/`get_by_label`) and `check`
  (`check()`/`set_checked`). Result-token still the only asserted DOM text.

**Batch C — final-report ✅ scrub (closes gap 3). ✅ DONE.**
- `criterion_closure.scrub_success_marks(text)` replaces ✅/✔/✓/☑/🟢/👍 (with optional VS16)
  by a neutral `▫`; the finalizer calls it **only** when `completion_status != confirmed`
  (right after `gate_completion_claims`), so the deterministic panel is the only source of
  "done". Surrounding text is preserved; a confirmed run keeps its marks (scrub not called).

**Batch D — viewport layout verifier (closes gap 4). ✅ DONE.**
- `browser` gains a `viewport` param (preset or `{width,height}`); `_browser_render` sizes the
  page and measures `document.documentElement.scrollWidth <= innerWidth + 1` → emits a
  `{checked,width,no_hoverflow}` signal. `_verdict_outcome(viewport_layout)`: no overflow →
  confirm, overflow → fail; `_verdict_target_matches` requires a real measurement (`checked`)
  and matches the criterion's width bucket (`_viewport_target`: narrow/wide/any). Closure hint
  `_action_for(viewport_layout)` → `browser(url, viewport="mobile"/"desktop")`.
- `project.scope.no_parent_changes` stays a **constraint** — no diff verifier added (by design).

**Cross-cutting (do in Batch A):** wire the runtime to *read* this catalog for (1) intent
classification sanity, (2) exact closure missing-action text, and (3) a `unsupported/
unverifiable` label so a criterion with no catalog verifier is reported as such instead of
spinning tools. Add a test that every catalog `code:` pointer resolves and every `supported`
row has a regression test.

---

## Where the runtime uses (or should use) this

1. **TaskSpec derivation** — pick the right intent for a criterion (and flag `generic`/
   unsupported early instead of implying it's verifiable).
2. **Closure gate** — emit the exact missing verifier + what it closes
   ("`run_bash(node index.js sample.log)` → closes INFO/WARN/ERROR/TOTAL").
3. **Coverage-gap honesty** — if a criterion maps to no catalog verifier, report it
   `unsupported/unverifiable` and stop hammering tools.
