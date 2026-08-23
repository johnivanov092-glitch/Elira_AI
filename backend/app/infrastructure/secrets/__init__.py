"""Portable application-owned secret vault infrastructure.

The active backend is the versioned encrypted file owned by ``vault.py``. The
model sees only opaque ``secret_ref`` values; plaintext exists only in runtime
memory during secure intake or tool dispatch. ``wincred.py`` is a read-only
legacy migration adapter and is not a runtime dependency.
"""
