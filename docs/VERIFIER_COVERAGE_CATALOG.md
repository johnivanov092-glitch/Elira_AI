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
| `browser.interaction.fill_click_assert` | `dom_contains`+interaction | `browser(actions)` | post-action DOM has result | ◐ | — |
| `project.scope.created_under` | `file_exists` | `path_exists` | present | ✅ | — |
| `cleanup.confirmed` | `file_not_exists` | `ssh_not_exists` | absent | ✅ | — |
| `viewport_layout` | `viewport_layout` | `browser` | viewport meta | ◐ | — |
| `project.scope.no_parent_changes` | *(→ constraint)* | — | *(can't verify without diff)* | ◐ | — |
| **`cli.output.contains`** — `cmd` выводит `INFO: 2` | `generic` ⚠ | `run_bash` | stdout/stderr contains text | ✗ | — |
| **`cli.output.not_contains`** | `generic` ⚠ | `run_bash` | text absent | ✗ | — |
| **`cli.command.fails_with_output`** — выводит X и падает | `generic` ⚠ | `run_bash` | text + exit≠0 | ✗ | — |
| **`browser.form.select_checkbox_assert`** — fill/select/checkbox/empty + click | `generic` ⚠ | `browser(actions)` | multi-step post-action DOM | ✗ | — |

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

1. **CLI stdout/stderr verifier — MISSING** (biggest; 9 criteria across 2 runs).
   `log-summarizer` (`INFO/WARN/ERROR/TOTAL`, + `missing.log` → File not found + non-zero)
   and `csv-inventory-checker` (`OK/WARN/DOWN/SUBNET/OUT_OF_SCOPE`) both had files/folders
   confirmed but every `cmd выводит X` criterion stuck as `generic`. `run_bash` only carries
   an exit-code (`command_check`); its stdout never reaches `record()`, and there is no
   `command_output` intent. Also a tool-economy tail (26 calls / 13× run_bash) because the
   closure can't tell the model "one run closes all four".
2. **Browser form grammar — MISSING** (`backup-form-checker`, 2 criteria). Two problems:
   (a) `browser interaction: … показывает X` **without** a DOM-context word (`rendered`/`DOM`/
   `на экране`) classifies `generic`; (b) `interaction_spec` models only a single fill+click —
   no `select`, `checkbox`, empty-field, or multi-step sequence, and `_apply_action` has no
   `select`/`check` action type.
3. **Final-report honesty — PARTIAL.** The scrub neutralises status headers, model count
   claims ("17 verifier criteria"), and universal "all passed" claims when not confirmed —
   but a model **✅-checkmark table/row** ("interaction … ✅") survives. The deterministic
   panel is correct (partial), but the prose can still *look* done.
4. **`viewport_layout` — PARTIAL** (no deterministic closure suggestion) and
   **`project.scope.no_parent_changes` — PARTIAL** (a diff/changed-paths verifier would be
   needed; today it's routed to constraints so it doesn't hang). Both are honest today — low
   priority.

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

**Batch C — final-report ✅ scrub (closes gap 3).**
- When `completion_status != confirmed`, neutralise model ✅/✓ rows that assert a
  verifier/interaction/criterion passed (append the runtime correction / strip the mark),
  so the deterministic panel is the only source of "done".

**Batch D (optional, low priority).** `viewport_layout` closure hint; a `changed_paths`
scope verifier for `no_parent_changes`.

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
