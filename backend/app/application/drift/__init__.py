"""Server drift detector.

Probes authoritative "live facts" (active model file, context window) from the
running llama-server over HTTP, records them, and raises an alert when a fact
drifts from what was last seen — so a stale doc/memory value can't be cited as
current without notice. See runtime.reconcile / start_drift_scheduler.
"""
