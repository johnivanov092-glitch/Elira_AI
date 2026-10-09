# Web skills: acceptance, 2026-10-09

Worktree: C:/Users/Root/.codex/worktrees/ui-artifacts-skills-acceptance/Elira_AI, branch codex/web-skill-experiment, base 405ae64. The following sections retain historical acceptance evidence. Current release closure is appended below.

## Original golden

Batch 20261009-164051: 19/29, including 2 passing only on retry. Source tasks were not edited (SHA-256 c5c16be97829f1eab0b746d395099afd0fb67d868b1686749253e06f0a278677). Most remaining failures stopped on approval for shell-based skills. This is below the user's permitted 26–27/29 release exception.

See golden-20261009-164051.txt. The later receipt length fix is not covered by this golden run.

## Separate functional run, explicitly authorized

The same ten previously blocked tasks were run with accept_edits and medium reasoning. Original task text, fixtures and checks were retained. Results are separate from the Atlas golden database.

All ten ended with an answer; 3/10 pass every unchanged check (DOCX, unavailable URL, weather). This is not a 29-task golden score. All six model-judge checks passed, but this is bounded evidence, not independent certification of every factual claim.

| Task | Steps | Remaining unchanged-check failures |
| --- | ---: | --- |
| SearXNG | 4 | Old builtin tool names |
| NGINX | 4 | Old builtin tool names |
| Kazakhstan news | 7 | Repeat detector compares truncated shell arguments |
| Qwen | 6 | Old tool names; real absence of Russian queries |
| GitHub/Laya | 3 | Old tool names; source receipt defect found separately |
| DOCX | 3 | None; 3,450,000 KZT and 31 December 2026 correct |
| Site check | 3 | Old builtin tool names |
| Unavailable URL | 3 | None |
| Weather | 3 | None; all seven table rows match the saved output |
| WWZ | 5 | Real absence of Russian queries; 2 read sources instead of 3 |

Full-argument comparisons found no identical repeated tool calls (maximum count 1 in every task). Different retrievals are not collapsed into one shell-prefix bucket in the supplemental evidence. The original check remains unchanged and retains its failure.

Evidence: functional-manifest.json, functional-results.json, functional-exact-evidence.json. evaluate_functional.py reuses Atlas checks without writing its database. An initial judge network failure is preserved in functional-results-judge-endpoint-error.json; the final evaluation used the existing local model with authorized network access.

## Real UI, sandbox http://127.0.0.1:18100

- Web-PDF repeat 8c10a920b55446889d1bc9968b3dfafd: 16.5 seconds, five steps, two instructions and two successful script calls. Correct quote, URL/hash, one presented source. No path errors or repeated reads. The skill checks fetched bytes/extraction; the application checks receipt integrity.
- Saved weather 95f1bf4dfbed4518b924c61d2da82b88: all seven requested rows correct, including weekdays and km/h; model bypassed weather skill and used read_file plus calc. Adding saved forecast.json to the skill catalogue description did not change selection; that trial was reverted.
- Web dates 1aa4c4bbfb784be591bfcbb2224a5e96: event 2025-10-02, publication 2026-10-01, update 2026-10-09 correct. Model used Invoke-WebRequest directly, so skill selection and a skill receipt were not demonstrated.
- GitHub repeat after the receipt fix 229378f04bc3405c876f8c62fac283f0: UI shows four verified source fragments; an unnecessary resources capability load still preceded selecting web-research. This verifies transport, not all statements in the generated answer.
- Browser cancellation: seven owned child processes terminated, no survivors or late actions, 0.375 seconds; browser-stop/proof.json.

## Confirmed defect and fix

The GitHub operation generated two source quotes of 1505 characters. Redaction expanded text after its initial 1500-character truncation. Both skill and core validators rejected them; the core correctly rejected the complete receipt.

skills/web-research/webskill/application/web_evidence/receipts.py now bounds safe_quote after redaction. Validation limits remain unchanged. Altered quotes retain quote_verified=false; valid unaffected fragments can reach UI. No historical receipts were rewritten.

The regression test first failed with 1509 > 1500. After the fix, 26 tests passed across test_skill_result_transport.py, test_web_skill_source.py and test_web_query_contract.py. Fresh Atlas check reports zero new/red issues. No full pytest was run while collection and contract questions remain open.

## Final migration and deferred issues

User approved coverage-preserving migration of the four remaining modules. Originals and a coverage map are saved in docs/experiments/retired-web-orchestration. All import/collection errors are resolved (2717 collected before the last four added edge cases and registration consolidation).

Fresh checks: 109 focused tests plus six subtests passed; 369 standalone web skill component tests passed. Atlas explicitly ran six migrated/receipt modules: 92 passed. Atlas check: zero new/red problems. Full backend suite is not green: the bounded per-module audit retains failures/timeouts in remaining-test-results.json. Core-forced web orchestration tests still call deleted builtin paths; source lifecycle/recovery/numbered-citation and in-process cancellation expectations need further contract migration. They are not skipped or marked passed.

The skill now owns the existing RU/EN policy, including regional exceptions and a two-refusal limit persisted across separate CLI processes. Corrupt state and no-cache mode degrade to an advisory to prevent endless rejection. Actual repeated Qwen ad31eb043d8d45ca8eeede4d85ce67da and WWZ 9d9a762cc4654e2da9f1006270e858c3 corrected EN-only searches after one refusal. Qwen also invented browser --find; the instruction now explicitly restricts --find to fetch.

Final fresh-workspace WWZ e47d1a7bdb16414687b50a78865de253 used both languages but read only two pages, then incorrectly counted a search snippet as a third read guide. The quota-only instruction experiment was reverted; that behavior is not accepted. Failed setup run 9b2bb1adcb41405992598e3d865617eb had no model steps because its workspace directory did not exist and is excluded from model results. No new broad golden run is claimed.

Deferred: incomplete legacy runtime test migration; WWZ source-count/honesty; occasional bypass of skills for saved weather and direct URL reads; unnecessary resources loading; invented CLI arguments. Original golden remains 19/29, with permission-blocked cases documented separately. These are release notes, not a passing acceptance claim.

User explicitly asked to stop further investigation, release the candidate with remaining issues, merge all agreed changes, and install it. Rejected prompt-english and skill-action-scope experiments remain excluded. Release/installation outcome must be reported from Foundation evidence separately.

## Release closure: full Web boundary

The user explicitly authorized retirement of obsolete native Web contracts and installation.
Removed dead core Web guidance, history projection, retry flags, query-history state and
Web-specific budget ownership. Research/source fidelity rules and their assertions now live
with the mutable web-research package. Generic command execution, source receipt transport,
context restoration and UI remain application services. Source instruction coverage is
preserved in test_thinking_web_guidance; citation checks invoke the real skill verify entry point.

That migration exposed a real skill defect: a fetched-only receipt with quote_verified=true
could be counted as read. verify now requires a nonempty verified excerpt. The regression test
failed before the fix and passed afterward. The common script-to-skill reminder was also restored.

The complete audit run had 2575 passed, 2 skipped, 73 subtests passed and five failures in
remaining native-tool expectations. Those expectations were corrected; 135 focused tests and
six subtests now pass. Fresh Atlas targeted run: 23 passed; Atlas check: zero new/red issues.
The final full-suite gate will run inside Foundation on the exact candidate. No green full-suite
result or installed acceptance is claimed by this pre-publication record.

Original native tests are archived under retired-web-orchestration; no golden criteria were changed.
Selection/WWZ/model semantic debts listed above remain deferred by the user. Release installation
must additionally update the changed mutable skills with backups; seeding alone does not overwrite
existing data/skills packages.
