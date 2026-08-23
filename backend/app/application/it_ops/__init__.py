"""IT Operations application layer behind Workflow runtime control.

Assets and profiles are operational metadata, not authorization scopes. Workflow
permission owns approval; arbitrary local/SSH execution reuses the existing tools.
Secret values live in the portable encrypted vault, never in the metadata DB and
never depend on Windows Credential Manager or DPAPI.
"""
