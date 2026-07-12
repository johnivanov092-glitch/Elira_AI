# Change Executor — Provisioning DoD (v1: `systemctl restart netdata.service`)

The change-executor code is **frozen and accepted** (commits `8cf8c18` + `6f9a3d9`). This
runbook is the *provisioning* Definition of Done: the out-of-band host/OS work that must be
done before the executor may run for real, and which `preflight` then **verifies fail-closed**
at every startup. Nothing here modifies the frozen code.

Guiding principle (John): *provisioning must be **verified**, not merely documented.* Every
item below is tagged with how it is checked — `[preflight]` (the executor refuses to start
otherwise) or `[out-of-band]` (only you can guarantee it; preflight cannot see it).

Legend: **[you]** = a human/host action (account, ACL, key, bot, remote sudoers — several are
prohibited for the agent to perform). **[tool]** = `scripts/provision_change_executor.py`
does it for you.

---

## 0. The isolation contract (why this exists)

`run_bash` runs as the **same OS principal** as the main Elira backend, so no transport can
exclude it. The guarantee "the model cannot change a host without a human Telegram approval"
therefore rests entirely on the executor being a **separate, more-privileged OS principal
that the main user cannot write to or impersonate**. Provisioning is what makes that true;
preflight is what proves it each boot.

Trust boundary: the executor account (and the remote `elira-change` sudo rule) are the TCB.
The **main Elira runtime must be non-elevated** — an elevated/admin main runtime defeats the
DACL boundary, and the only strong form then is to run the executor on a **separate host/VM**.

---

## 1. Layout of the protected executor root  `[tool]` + `[you: ACLs]`

Pick a root **outside the repository** (a run from `D:\AIWork\Elira_AI` MUST fail preflight —
that is expected protection, not a bug). Example: `C:\elira-change-exec`.

```
<ROOT>\                         ELIRA_CHANGE_EXECUTOR_ROOT
  venv\                         dedicated venv (python -m venv); sys.executable lives here
    Scripts\python.exe
  change_executor\              frozen package copied verbatim from backend/app/change_executor
    service.py, engine.py, store.py, preflight.py, ...
  config\
    registry.json               ELIRA_CHANGE_REGISTRY_PATH (executor-owned target registry)
    known_hosts                 pinned host key(s)  [you: ssh-keyscan + out-of-band verify]
    id_elira_change             private change key  [you: ssh-keygen; readable only by exec]
    id_elira_change.pub
  change.sqlite3                ELIRA_CHANGE_STORE_PATH (private store; created at first boot)
  ipc.token                     ELIRA_CHANGE_IPC_TOKEN_FILE (per-boot bearer; minted at boot)
```

Run the deployer (creates `venv\`, copies `change_executor\`, writes a `registry.json`
template):

```powershell
python scripts\provision_change_executor.py deploy `
  --root C:\elira-change-exec `
  --target-host 192.168.88.15 --remote-user elira-change --unit netdata.service `
  --base-python "C:\path\to\protected\python.exe"
```

Every sensitive path is placed **inside `<ROOT>`** on purpose: preflight requires containment
so the single recursive root ACL walk covers each file's whole ancestor chain. `[preflight]`

---

## 2. The executor OS principal  `[you]`  `[out-of-band]`

Create a dedicated, **non-elevated** local account that owns and runs the executor:

```powershell
New-LocalUser -Name elira-change-exec -Description "Elira change executor (isolated TCB)" `
  -Password (Read-Host -AsSecureString)
# Do NOT add it to Administrators. Confirm:
Get-LocalGroupMember Administrators   # elira-change-exec MUST NOT appear
```

preflight cannot tell that this account is *different from the main Elira user* or that the
main runtime is non-elevated — that is the core assumption you own.

---

## 3. ACL-lock the root, key, token, and base Python  `[you]`  `[preflight: writability]`

Remove inheritance and grant write to **only** the executor account and SYSTEM. The main
Elira user must have **no write** anywhere under the root, and **no read** on the secrets
(private key, IPC token, bot-token store):

```powershell
icacls C:\elira-change-exec /inheritance:r
icacls C:\elira-change-exec /grant:r "elira-change-exec:(OI)(CI)F" "SYSTEM:(OI)(CI)F"
# Secrets: read only by the executor account (no main-user read):
icacls C:\elira-change-exec\config\id_elira_change /inheritance:r /grant:r "elira-change-exec:F" "SYSTEM:F"
# The venv's BASE Python shares the stdlib — protect it too (or place it under <ROOT>):
icacls "C:\path\to\protected\python-base" /inheritance:r /grant:r "elira-change-exec:(OI)(CI)RX" "SYSTEM:(OI)(CI)F"
```

- preflight flags any non-owner-writable path across the root tree + parents, the venv, the
  interpreter, `sys.path` (incl. zip/egg files and escaping dir-symlinks), and every sensitive
  file. `[preflight]`
- preflight checks non-**writability**, not non-**readability**. That the main user cannot
  *read* the key / token / bot-token is on you. `[out-of-band]`

---

## 4. The change key + known_hosts pin  `[you]`  `[preflight: files exist + in-root + non-writable]`

As `elira-change-exec`:

```powershell
ssh-keygen -t ed25519 -f C:\elira-change-exec\config\id_elira_change -N ""
ssh-keyscan -p 22 192.168.88.15 > C:\elira-change-exec\config\known_hosts
```

Then **verify the fingerprint out-of-band** against the ai-server console before trusting it —
`ssh-keyscan` is TOFU and is not proof. The pinned `known_hosts` content is hashed into the
immutable planned binding, so a later swap is caught before any SSH. `[out-of-band verify]`

---

## 5. Separate Telegram bot + approver allowlist  `[you]`  `[preflight: token set + allowlist well-formed]`

Create a **new** bot with @BotFather — it must NOT be the token the main Elira process polls
(two `getUpdates` pollers on one token fight). Then find your Telegram numeric user id and the
chat id you will approve from.

Set for the executor service environment (readable only by `elira-change-exec`):

```
ELIRA_CHANGE_BOT_TOKEN=<new bot token>
ITOPS_CHANGE_APPROVER_USER_IDS=<your telegram user id>[,<id>...]
ITOPS_CHANGE_APPROVER_CHAT_IDS=<approving chat id>[,<id>...]
```

preflight checks the token is set and the allowlists are non-empty and well-formed (a
malformed allowlist denies all). It cannot verify the bot is *distinct* from the main bot —
that is on you. `[out-of-band]` The executor host also needs **outbound HTTPS to
`api.telegram.org`** and **outbound SSH to the target host**. `[out-of-band: firewall]`

---

## 6. Remote target: `elira-change` user + narrow sudoers  `[you]`  `[out-of-band]`

On the ai-server (192.168.88.15):

```bash
sudo adduser --disabled-password --gecos "" elira-change
sudo -u elira-change mkdir -p /home/elira-change/.ssh
# install id_elira_change.pub into /home/elira-change/.ssh/authorized_keys (mode 600)

sudo visudo -f /etc/sudoers.d/elira-change      # EXACTLY one rule, no wildcards:
#   elira-change ALL=(root) NOPASSWD: /usr/bin/systemctl restart netdata.service
sudo visudo -c                                   # validate syntax
```

The apply argv is the fixed absolute `sudo -n /usr/bin/systemctl restart netdata.service`;
the sudoers rule must match it exactly and grant nothing wider. Invisible to preflight. `[out-of-band]`

---

## 7. Read path + old-identity revocation  `[you]`  `[out-of-band]`

- Re-point the read-only adapters (healthcheck / inventory / systemd inspect) to a separate
  **`elira-ro`** remote identity that **cannot sudo**. Confirm `sudo -n true` fails for it.
- **Revoke** the old sudo-capable identity used during earlier canaries (remove its sudoers /
  disable its key), so no non-executor path retains privileged access.

---

## 8. Environment for the executor service  `[you]`

Run the executor as `elira-change-exec` (Windows service or scheduled task), with:

```
ELIRA_CHANGE_EXECUTOR_ROOT=C:\elira-change-exec
ELIRA_CHANGE_REGISTRY_PATH=C:\elira-change-exec\config\registry.json
ELIRA_CHANGE_STORE_PATH=C:\elira-change-exec\change.sqlite3
ELIRA_CHANGE_IPC_TOKEN_FILE=C:\elira-change-exec\ipc.token
ELIRA_CHANGE_BOT_TOKEN=...           ITOPS_CHANGE_APPROVER_USER_IDS=...   ITOPS_CHANGE_APPROVER_CHAT_IDS=...
ELIRA_CHANGE_IPC_PORT=8790           (loopback only; the main backend reads the same token file)
```

Launch: `<ROOT>\venv\Scripts\python.exe -m change_executor.service` with `PYTHONPATH=<ROOT>`.

The **main** backend needs the matching `ELIRA_CHANGE_IPC_TOKEN_FILE` and `ELIRA_CHANGE_IPC_PORT`
so its client can reach the loopback IPC (it never holds keys, and forwards only a `target_id`).

---

## 9. Verify — preflight from the deployed root  `[tool]` + `[you: run]`

```powershell
python scripts\provision_change_executor.py verify --root C:\elira-change-exec
```

This runs the **deployed** `change_executor.preflight` via the **deployed** venv (so
`__file__`/`sys.executable` are inside the root). Green = zero problems. It will legitimately
report the ACL / key / known_hosts / account items above until you have completed them — that
is the verification loop working. Running preflight from the repo instead **must fail**
(git-working-tree + package-not-inside-root) — expected protection.

`[preflight]` coverage recap: required envs; approver allowlist well-formed; package + interpreter
inside root; dedicated venv; not a git tree; non-owner-writable checks over root+parents+venv+
interpreter+sys.path(incl. zip/egg + escaping dir-symlink); sensitive paths inside root + non-writable;
registry valid + target files present.

---

## 10. The two live smokes — after a green preflight  `[you: trigger]`

Only two host-touching runs are authorized, both human-triggered (the model/UI can never
approve or apply):

1. **Reject smoke.** UI → plan a netdata restart → Telegram **Reject**. Expect ChangeRun
   `rejected`; the host is **untouched**; `GET /change/{id}/status` shows the capped status.
2. **Restart smoke.** UI → plan again → Telegram **Approve**. Expect `applied`; on the host,
   `netdata.service` is `active/running` with a **changed MainPID**; the evidence shows the
   pre/post inspect.

Failure / unknown / drift paths are **test-only** (exercised over the fake transport, never
against the real host). After the smokes, return `itops` to OFF unless you are deliberately
keeping the feature enabled.
