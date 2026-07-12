# Change Executor — Provisioning DoD (v1: `systemctl restart netdata.service`)

The change-executor code is **frozen and accepted** (`8cf8c18` + `6f9a3d9`; identity/owner
hardening in the correction batch). This runbook is the *provisioning* Definition of Done: the
out-of-band host/OS work that must precede the two live smokes, and which `preflight`
**verifies fail-closed** at every startup.

Guiding principle (John): *provisioning must be **verified**, not merely documented.* Every item
is tagged `[preflight]` (the executor refuses to start otherwise) or `[out-of-band]` (only you
can guarantee it; preflight cannot see it). **[you]** = a human/host action (several are
prohibited for the agent). **[tool]** = `scripts/provision_change_executor.py` does it.

**Order matters.** The executor account is created **first** (Step 1), because deploy and verify
must run **as that account** so every file is **owned** by it — Windows lets an owner rewrite any
DACL, so an install owned by the main user is not isolated even after ACLs are set. Preflight
enforces owner + running-as by SID.

---

## 0. The isolation contract (why this exists)

`run_bash` runs as the **same OS principal** as the main Elira backend, so no transport can
exclude it. The guarantee "the model cannot change a host without a human Telegram approval"
rests entirely on the executor being a **separate, more-privileged OS principal that the main
user cannot write to, own, or impersonate**. Provisioning makes that true; preflight proves it
each boot. The **main Elira runtime must be non-elevated** — an elevated main runtime defeats the
DACL boundary, and the only strong form then is a **separate host/VM**.

---

## 1. Create the executor OS principal  `[you]`  `[out-of-band]`

A dedicated, **non-elevated** local account that owns and runs the executor:

```powershell
New-LocalUser -Name elira-change-exec -Description "Elira change executor (isolated TCB)" `
  -Password (Read-Host -AsSecureString)
Get-LocalGroupMember Administrators   # elira-change-exec MUST NOT appear
```

Its full name (`HOSTNAME\elira-change-exec`) or SID is `ELIRA_CHANGE_EXECUTOR_ACCOUNT` below.
preflight cannot tell this account is *different from the main Elira user* or that the main
runtime is non-elevated — that is the core assumption you own. `[out-of-band]`

---

## 2. Build a trusted artifact, then deploy — **run as `elira-change-exec`**  `[tool]`

The privileged code must **not** be copied from the live repo checkout: the model (via
`run_bash`) shares the main user and could rewrite `change_executor\*.py` — or the provisioner
itself — before deploy, and preflight only protects the *already-copied* install. So deploy
consumes a **trusted, hash-manifested release artifact** built once from a **verified-clean**
repo and kept **outside** any writable checkout.

**2a. Build (once, from a clean/verified checkout):** the artifact bundles the frozen package
**and the provisioner itself**, all hashed into `MANIFEST.sha256`.

```powershell
python scripts\provision_change_executor.py build --out C:\elira-artifacts\change-exec-v1
#  -> prints "MANIFEST sha256: <digest>" and "provision_change_executor.py sha256: <phash>".
```

Verify the checkout was clean first (e.g. a signed tag / `git verify`). Record **both** the
`MANIFEST sha256` and the provisioner sha256 **out-of-band**, and move the artifact outside any
writable repo. The `MANIFEST.sha256` file bytes hash to exactly the printed digest, so you can
re-check it with an external OS tool (below) rather than trusting the script.

**2b. Lock the artifact directory AND its parent chain — REQUIRED, and BEFORE the hash-check.**
`Get-FileHash` and the later `python <art>\…` are two separate reads of a mutable path; if the main
user can write the artifact dir — **or its parent** — it can pass the hash-check and then swap
`provision_change_executor.py`, or **delete/rename the whole `$art` and substitute a new dir**
(a parent with write / delete-child is enough — the child's own ACL does not prevent that), and
the run would execute the swap **as `elira-change-exec`**. So the artifact must live under a
**dedicated protected parent** (e.g. `C:\elira-artifacts`), and the **entire chain from `$art` up
to a trusted root** must be owned by and writable only by `elira-change-exec`/SYSTEM — main
principal: **no write and no delete-child** — from the moment it is placed:

```powershell
$art = "C:\elira-artifacts\change-exec-v1"
foreach ($d in @("C:\elira-artifacts", $art)) {   # the dedicated parent AND the artifact
  # /setowner /T (and any icacls) can PARTIALLY fail (locked/in-use files) and print "Failed
  # processing N files" — check $LASTEXITCODE or a file could keep a main-user owner.
  icacls $d /setowner "HOSTNAME\elira-change-exec" /T ; if ($LASTEXITCODE) { throw "setowner failed on $d" }
  icacls $d /inheritance:r /grant:r "HOSTNAME\elira-change-exec:(OI)(CI)F" "SYSTEM:(OI)(CI)F" ; if ($LASTEXITCODE) { throw "grant failed on $d" }
}
# C:\ (or the chosen drive root) must already deny standard users write/delete-child — verify (2c).
```

**2c. External trusted bootstrap — verify the whole chain + every artifact file + the hashes
BEFORE running.** Refuse any non-{exec,SYSTEM,Administrators,TrustedInstaller} principal that
**owns** — or holds write / delete / delete-child / change-permissions / take-ownership on — **any
ancestor directory OR any file inside the artifact** (SID-based, locale-independent). The **owner**
check is essential on every node: an owner can rewrite a DACL regardless of its ACEs (implicit
`WRITE_DAC`), so a main user owning a parent *or a single file* (e.g. if `/setowner /T` partially
failed on `provision_change_executor.py`) could re-ACL and swap it after the hash-check. The **drive
root** `C:\` is a pre-declared trusted root: verify its **owner** is trusted but accept its default
DACL (which grants Authenticated Users create-subdir, but no delete-child over the locked dedicated
parent). Then hash-check with `Get-FileHash`:

```powershell
$pin = "<MANIFEST sha256 from 2a>"
$ti  = 'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464'   # TrustedInstaller
$trusted = @('S-1-5-18','S-1-5-32-544',$ti,
  (New-Object System.Security.Principal.NTAccount("HOSTNAME\elira-change-exec")).Translate([System.Security.Principal.SecurityIdentifier]).Value)
$wr = [System.Security.AccessControl.FileSystemRights]'Write,Delete,DeleteSubdirectoriesAndFiles,ChangePermissions,TakeOwnership'
function Assert-Trusted($path) {
  $acl  = Get-Acl -LiteralPath $path
  $osid = $acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value    # OWNER (implicit WRITE_DAC)
  if ($osid -notin $trusted) { throw "untrusted owner $osid on $path" }
  foreach ($ace in $acl.Access | Where-Object { $_.AccessControlType -eq 'Allow' -and ($_.FileSystemRights -band $wr) }) {
    $sid = try { $ace.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value } catch { $ace.IdentityReference.Value }
    if ($sid -notin $trusted) { throw "untrusted writer $sid on $path" }
  }
}
for ($p = Get-Item $art; $p; $p = $p.Parent) {
  if ($null -eq $p.Parent) {              # drive root = pre-declared trusted root: OWNER only (its
    $o = (Get-Acl -LiteralPath $p.FullName).GetOwner([System.Security.Principal.SecurityIdentifier]).Value  # default DACL grants
    if ($o -notin $trusted) { throw "untrusted owner $o on drive root $($p.FullName)" }               # Authenticated Users
  } else { Assert-Trusted $p.FullName }   # $art + the dedicated protected parent(s): full owner+DACL   # AppendData — accepted)
}
Get-ChildItem -LiteralPath $art -Recurse -Force | ForEach-Object { Assert-Trusted $_.FullName }  # every file/subdir INSIDE
if ((Get-FileHash "$art\MANIFEST.sha256" -Algorithm SHA256).Hash.ToLower() -ne $pin) { throw "MANIFEST tampered" }
$want = ((Select-String -Path "$art\MANIFEST.sha256" -Pattern 'provision_change_executor.py').Line -split '\s+')[0].ToLower()
if ((Get-FileHash "$art\provision_change_executor.py" -Algorithm SHA256).Hash.ToLower() -ne $want) { throw "provisioner tampered" }
```

**2d. Deploy** (as `elira-change-exec`), running the **verified provisioner from the artifact**
(not the repo — `deploy`/`verify` refuse to run from a repo checkout). That refusal is a
belt-and-suspenders guard against *accidental* repo-copy use (it keys on a `.git`, which a
repo-writing attacker could remove); the **authoritative** defenses are the Step 2b lock and the
Step 2c external hash-check — do not rely on `.git` detection alone. Run against the artifact +
pinned digest. Root **outside the repository** (a run from `D:\AIWork\Elira_AI` MUST fail
preflight); a **dedicated, protected base Python** — NOT the main backend's:

```powershell
# runas /user:elira-change-exec ...  (or a scheduled task / service running as that account)
# Launch with the LOCKED base Python (not ambient `python`), so the interpreter is trusted too:
& "C:\elira-base-python\python.exe" "$art\provision_change_executor.py" deploy `
  --root C:\elira-change-exec `
  --account "HOSTNAME\elira-change-exec" `
  --artifact $art --manifest-sha256 $pin `
  --base-python "C:\elira-base-python\python.exe" `
  --target-host 192.168.88.15 --remote-user elira-change --unit netdata.service
```

Deploy **verifies every artifact file against the manifest and the manifest against the pinned
digest** (refusing any tamper), refuses to run unless the current principal **is** `--account`
(so files are owned by it), refuses an artifact/root inside the repo or a drive root, refuses a
`--base-python` inside the repo, and (`--force`) refuses to overwrite a directory lacking its
install marker. Deploy into a **fresh** root: a pre-existing `change_executor\` is a **hard error**
(it could be pre-planted, then laundered to executor-ownership by Step 3) — deploy also re-hashes
the copied bytes against the manifest so *deployed == verified*. Updates = rebuild + re-pin from a
clean checkout, then re-run with `--force`; deploy accepts only the artifact.

```
C:\elira-change-exec\              ELIRA_CHANGE_EXECUTOR_ROOT
  venv\Scripts\python.exe          dedicated venv; sys.executable lives here
  change_executor\ *.py            frozen package copied verbatim
  config\
    registry.json                  ELIRA_CHANGE_REGISTRY_PATH
    known_hosts                    pinned host key(s)  [you: Step 4]
    id_elira_change[.pub]          private change key  [you: Step 4]
  change.sqlite3                   ELIRA_CHANGE_STORE_PATH (created at first boot)
  ipc\ipc.token                    ELIRA_CHANGE_IPC_TOKEN_FILE (per-boot bearer, minted at boot;
                                   dedicated dir carries an INHERITABLE main-READ ACE — Step 3)
```

Every sensitive path is **inside `<ROOT>`** on purpose: preflight requires containment so the
single recursive root ACL walk covers each file's whole ancestor chain. `[preflight]`

---

## 3. Own + ACL-lock the tree  `[you]`  `[preflight: owner SID + writability]`

Take ownership of the **whole tree** as the executor account, drop inheritance, and grant write
to **only** the executor account and SYSTEM:

```powershell
icacls C:\elira-change-exec /setowner "HOSTNAME\elira-change-exec" /T
icacls C:\elira-change-exec /inheritance:r
icacls C:\elira-change-exec /grant:r "HOSTNAME\elira-change-exec:(OI)(CI)F" "SYSTEM:(OI)(CI)F"
```

Then the **read** split — this is the key contract point:

| Path | executor | main Elira principal | others |
|------|----------|----------------------|--------|
| `config\id_elira_change` (SSH key) | read/write | **none** | none |
| Telegram bot-token store | read/write | **none** | none |
| `ipc\ipc.token` | write (minted each boot) | **read** (inherited from `ipc\`) + traverse on `<ROOT>` | none |

The main backend **must read the IPC token** — it is the loopback bearer the UI's plan request
uses. But the token is **minted at each boot** (it does not exist at this step), and the freshly
minted file **inherits its directory's ACL** — so you cannot grant on the file, you must make the
main-read **inheritable on its directory** `ipc\`. Give the main principal object-inheritable
read on `ipc\` and traverse on `<ROOT>`; the key in `config\` stays hidden (no grant there):

```powershell
icacls C:\elira-change-exec\ipc /inheritance:r `
  /grant:r "HOSTNAME\elira-change-exec:(OI)(CI)F" "SYSTEM:(OI)(CI)F" "MAINUSER:(OI)(R)"
icacls C:\elira-change-exec     /grant:r "MAINUSER:(RX)"      # traverse to reach ipc\ipc.token
```

- preflight verifies (by SID) that the executor is **running as** `ELIRA_CHANGE_EXECUTOR_ACCOUNT`
  and that **every file is owned by** it (or SYSTEM/Administrators/TrustedInstaller) — per-file
  across root+parents, venv, interpreter, sys.path, and the base runtime — because a Windows owner
  keeps implicit WRITE_DAC even with a clean DACL. It also flags any non-owner **writer** across
  the same tree (incl. zip/egg + escaping dir-symlinks) and every sensitive file. A grant of
  **read/traverse** (not write) does not trip it. `[preflight]`
- The **base Python** must be a dedicated, read-only, system/executor-owned install (not the main
  backend's). preflight verifies it **per-file — owner AND writability** — so a single `Lib\*.py`
  with a `MAINUSER:(W)` ACE (clean dir ACL, correct owner) is caught; own it and lock it:
  `icacls C:\elira-base-python /setowner "HOSTNAME\elira-change-exec" /T` then
  `/inheritance:r /grant:r "…exec:(OI)(CI)RX" "SYSTEM:(OI)(CI)F"`. Keep it a **dedicated, minimal**
  runtime — the per-file walk is a one-time startup cost. `[preflight]`
- preflight checks non-**writability** and **ownership**, not non-**readability**. That the main
  user cannot *read* the key / bot-token is on you. `[out-of-band]`

---

## 4. The change key + known_hosts pin  `[you]`  `[preflight: files exist + in-root + non-writable]`

As `elira-change-exec`:

```powershell
ssh-keygen -t ed25519 -f C:\elira-change-exec\config\id_elira_change -N ""
ssh-keyscan -p 22 192.168.88.15 > C:\elira-change-exec\config\known_hosts
```

Then **verify the fingerprint out-of-band** against the ai-server console before trusting it —
`ssh-keyscan` is TOFU, not proof. The pinned `known_hosts` content is hashed into the immutable
planned binding, so a later swap is caught before any SSH. `[out-of-band verify]`

---

## 5. Separate Telegram bot + approver allowlist  `[you]`  `[preflight: token set + allowlist well-formed]`

Create a **new** bot with @BotFather — NOT the token the main Elira process polls (two
`getUpdates` pollers on one token fight). Find your Telegram numeric user id and the approving
chat id. Store for the executor service (readable only by `elira-change-exec`):

```
ELIRA_CHANGE_BOT_TOKEN=<new bot token>
ITOPS_CHANGE_APPROVER_USER_IDS=<your telegram user id>[,...]
ITOPS_CHANGE_APPROVER_CHAT_IDS=<approving chat id>[,...]
```

preflight checks the token is set and the allowlists are non-empty/well-formed (malformed ⇒ deny
all). It cannot verify the bot is *distinct* — that is on you. The executor host needs **outbound
HTTPS to `api.telegram.org`** and **outbound SSH to the target**. `[out-of-band: firewall]`

---

## 6. Remote target: `elira-change` user + narrow sudoers  `[you]`  `[out-of-band]`

On the ai-server (192.168.88.15):

```bash
sudo adduser --disabled-password --gecos "" elira-change
# install id_elira_change.pub into /home/elira-change/.ssh/authorized_keys (mode 600)
sudo visudo -f /etc/sudoers.d/elira-change      # EXACTLY one rule, no wildcards:
#   elira-change ALL=(root) NOPASSWD: /usr/bin/systemctl restart netdata.service
sudo visudo -c
```

The apply argv is the fixed absolute `sudo -n /usr/bin/systemctl restart netdata.service`; the
rule must match exactly and grant nothing wider. Invisible to preflight. `[out-of-band]`

---

## 7. Read path + old-identity revocation  `[you]`  `[out-of-band]`

- Re-point the read-only adapters (healthcheck / inventory / systemd inspect) to a separate
  **`elira-ro`** remote identity that **cannot sudo** (`sudo -n true` must fail for it).
- **Revoke** the old sudo-capable identity used during earlier canaries (remove its sudoers /
  disable its key), so no non-executor path retains privileged access.

---

## 8. Environment for the executor service  `[you]`

Run the executor **as `elira-change-exec`** (Windows service or scheduled task), with:

```
ELIRA_CHANGE_EXECUTOR_ROOT=C:\elira-change-exec
ELIRA_CHANGE_EXECUTOR_ACCOUNT=HOSTNAME\elira-change-exec     (or its SID)
ELIRA_CHANGE_REGISTRY_PATH=C:\elira-change-exec\config\registry.json
ELIRA_CHANGE_STORE_PATH=C:\elira-change-exec\change.sqlite3
ELIRA_CHANGE_IPC_TOKEN_FILE=C:\elira-change-exec\ipc\ipc.token
ELIRA_CHANGE_BOT_TOKEN=...   ITOPS_CHANGE_APPROVER_USER_IDS=...   ITOPS_CHANGE_APPROVER_CHAT_IDS=...
ELIRA_CHANGE_IPC_PORT=8790   (loopback only)
```

Launch: `<ROOT>\venv\Scripts\python.exe -m change_executor.service` with `PYTHONPATH=<ROOT>`.

The **main** backend needs the matching `ELIRA_CHANGE_IPC_TOKEN_FILE` (which it **reads** — Step 3)
and `ELIRA_CHANGE_IPC_PORT` so its client can reach the loopback IPC; it never holds keys and
forwards only a `target_id`.

---

## 9. Verify — preflight from the deployed root  `[tool]` + `[you: run as the account]`

Run **as `elira-change-exec`**, from the **verified artifact copy** (not the repo — `verify`
refuses to run from a repo checkout, so a swapped repo script can't be used by mistake):

```powershell
& "C:\elira-base-python\python.exe" "$art\provision_change_executor.py" verify --root C:\elira-change-exec --account "HOSTNAME\elira-change-exec"
```

This runs the **deployed** `change_executor.preflight` via the **deployed** venv (so
`__file__`/`sys.executable` are inside the root, and the current SID is the executor account).
Green = zero problems. It will legitimately report ACL / owner / key / known_hosts / account items
until you have completed them — the verification loop working. A run **from the repo**, or by a
principal other than the account, **fails by design**.

`[preflight]` recap: required envs; approver allowlist well-formed; package + interpreter inside
root; dedicated venv; not a git tree; **running as `ELIRA_CHANGE_EXECUTOR_ACCOUNT` (SID) and every
file OWNED by it** (per-file across root+parents+venv+interpreter+sys.path+**base runtime** —
Windows owners keep implicit WRITE_DAC); non-owner-**writable** checks per-file over
root+parents+venv+interpreter+**base runtime**+sys.path (incl. zip/egg + escaping dir-symlink);
sensitive paths inside root + non-writable; registry valid + target files. Deploy itself accepts
code only from a **hash-verified release artifact** — bundling the provisioner — not the live repo.

---

## 10. The two live smokes — after a green preflight  `[you: trigger]`

Only two host-touching runs are authorized, both human-triggered (the model/UI can never approve
or apply):

1. **Reject smoke.** UI → plan a netdata restart → Telegram **Reject**. Expect ChangeRun
   `rejected`; the host **untouched**; `GET /change/{id}/status` shows the capped status.
2. **Restart smoke.** UI → plan again → Telegram **Approve**. Expect `applied`; on the host,
   `netdata.service` is `active/running` with a **changed MainPID**; the evidence shows the
   pre/post inspect.

Failure / unknown / drift paths are **test-only** (fake transport, never the real host). After the
smokes, return `itops` to OFF unless deliberately keeping the feature enabled.
