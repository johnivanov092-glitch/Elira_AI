"""IT Operations durable store (infrastructure layer).

ONE SQLite file `data/it_ops.sqlite3` via the shared `connect_sqlite` adapter —
no second DB layer, no second connect helper. Holds assets / connection_profiles
/ operation_scopes / snapshots / evidence / change_runs and secret_ref STATE.

Durable records → migrations are FORWARD-ONLY and idempotent (create-if-missing /
add-column-if-missing), NEVER drop-on-change (that is only for the web_corpus
cache). The secret VALUE is never stored here — it lives in Windows Credential
Manager; this store holds only the opaque secret_ref + metadata/lifecycle.
"""
