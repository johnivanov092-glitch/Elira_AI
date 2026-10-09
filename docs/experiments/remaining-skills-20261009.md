# Remaining skill contours — 2026-10-09

## Implementation boundary

| Contour | Mutable implementation | Core integration retained |
|---|---|---|
| SSH | linux-admin/ssh_runtime.py, admin.py | Process ownership, Stop, stored aliases |
| IT Ops | linux-admin + network-dns-tls scenarios | Existing inventory/evidence DB and vault |
| CSV | data-analysis | Generic shell execution |
| Telegram | telegram | Settings/routes, shared store/vault, bot lifecycle adapter |
| Vision | vision | Attachment and UI consumers use the same mutable module |
| Document QA/preview | document-create/qa.py, check.py | Publication bytes/hash, download routes |

Factory skills seed data/skills. Application consumers resolve installed modules
through skill_services; no independent implementation, executor or tool provider
is retained. Modules are cached for the application process lifetime; changing a
skill used by application integrations requires restarting the application.
CLI processes import current skill bytes on every execution.

Windows, containers and diagnostics reuse linux-admin scenarios and one SSH
transport. Existing skill names and user instructions are preserved. There is no
new itops/ssh package or separate permission mechanism. All model-initiated
scenarios run through run_bash and the existing Workflow permission mode.
Generic child-job handoff checks the durable run/workspace owner and reconnects
the existing process runtime; Stop reaches children created by skill scripts.

## Cleanup and debt

All six native registrations/providers and their schemas were removed. Existing
app-owned registry rows are retired at startup; user plugin rows remain.
SSH-specific prompt injection and failed-command hints were moved to linux-admin;
the latter never blocked execution. Original niche-rule tests are retained in
docs/retired-tests; current tests cover mutable instructions and unchanged base
prompt behavior.

The 24 initial Atlas findings were addressed by removing the stale /api/extra
asset classification, specifying UTF-8 in release process identity, narrowing
untrusted dict inputs, correcting the Workflow emitter protocol, separating
trusted file-read context from model arguments, and documenting the existing
run-history/agent-registry databases and temporary snapshot files. Atlas's
documented-store allowlist was synchronized, with its prior file backed up in
.scratch/remaining-skill-contours/atlas-rules-before.json.

39 of the 42 initial duplicate notices were consolidated: pure receipt/quote/
redaction/text/number/SQLite/URL/hash utilities now have one shared implementation;
retired routes, identical news normalization and resource-refusal formatting are
unified. Three similarity notices describe different operations and are retained:

- search versus deep_search: different underlying calls and pages_to_read semantics.
- BOM number versus parse_decimal: BOM rejects ambiguous mixed separators through
  _plain_number; finance retains its existing input convention.
- get_connection_profile versus secret_ref_state: different tables, keys and row
  projections; the secret function returns lifecycle metadata, never credentials.

The shared library contains pure support code, no search/browser execution.
BeautifulSoup is removed from backend requirements; pandas/openpyxl are declared
in data-analysis/bom-check skills. DOCX/PDF QA libraries remain available to the
application because UI preview calls the mutable QA integration in-process.

## Acceptance

Technical evidence is recorded under .scratch/remaining-skill-contours. Coverage
includes transported CLI JSON/errors, configured Vision HTTP transport using a
local fixture, registry/log scenarios, installed module resolution, publication
hash mismatch, trusted runtime metadata and child-job ownership/Stop. Existing
SSH/IT Ops, Telegram, document QA and source-receipt checks exercise moved code.
Model choice, answer behavior and unchanged golden runs are explicitly deferred
until the new candidate is installed, per the user's instruction.
