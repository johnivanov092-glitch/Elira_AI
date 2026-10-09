# Coverage migration (authorized 2026-10-09)

Original HEAD files are preserved beside this document as .py.txt.

| Original contract | New coverage / explicit change |
| --- | --- |
| evidence_router publication, BOM, QA, missing artifact | All existing assertions retained in test_evidence_router.py. |
| Four automatic Web-escalation helpers | Retired together with removed core-forced Web policy; positive automatic escalation is no longer claimed. Local/ordinary/error prose cannot manufacture builtin Web calls: parameterized real-loop cases in test_unrequested_web_confirmation.py. |
| Current-events access and shell execution | Actual child script execution through real loop/executor, accept_edits, streaming and nonstreaming. Model selection itself is covered by separate live tasks, not by a fake model unit test. |
| Global ru/en, regional exception, reminders, bounded rejections | Actual mutable tool_web_search calls with deterministic search fixtures. Missing policy remains failed, without skip/xfail or SKILL.md string assertions. Core role placement is retired because the policy no longer belongs to the core. |
| HTTP transport failure, 2xx, 5xx, loopback | Actual skill HTTP entry point with mocked transport. Status/body/error retained. No browser verifier is claimed by HTTP; core run_server registry coupling is intentionally removed. |
| Browser empty URL and generic runtime error formatting | Original tests retained unchanged. |

Golden criteria, model/permission product defaults and production code are not modified by this migration. It does not resolve the known language/source-count failures or prove recovered model behavior after an arbitrary first denial.

Additional registration migration: original test_code_agent_web_tools.py archived. Builtin schema/dispatch presence is replaced by absence checks plus actual CLI parser/dispatch, required and repeated query/URL arguments, audience and defaults. Existing behavioral tests for batch=5, top_k<=10, max_chars<=50000, receipts and text budgets remain unchanged.
