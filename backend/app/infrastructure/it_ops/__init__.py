"""IT Operations durable store (infrastructure layer).

ONE SQLite file `data/it_ops.sqlite3` via the shared `connect_sqlite` adapter —
no second DB layer, no second connect helper. Holds assets / connection_profiles
/ snapshots / evidence / change_runs and secret_ref STATE. Older databases may
still contain unused legacy tables; runtime authorization never reads them.

Durable records → migrations are FORWARD-ONLY and idempotent (create-if-missing /
add-column-if-missing), NEVER drop-on-change (that is only for the web_corpus
cache). Secret values live only in the application-owned portable encrypted
vault; this store holds opaque secret_ref metadata/lifecycle.
"""
