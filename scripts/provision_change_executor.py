#!/usr/bin/env python3
"""Provision / verify the isolated change executor (see docs/CHANGE_EXECUTOR_PROVISIONING.md).

Two subcommands, both stdlib-only:

  deploy  — copy the FROZEN `change_executor` package + a dedicated venv + a registry template
            into a protected root OUTSIDE this repo. Must be run AS the executor account
            (`--account`) so the files are OWNED by it. Does NOT create OS accounts, set ACLs,
            generate the SSH key, populate known_hosts, or create the Telegram bot — those are
            human/host steps (some prohibited for the agent) and are printed as next steps.

  verify  — run the DEPLOYED preflight via the DEPLOYED venv (so __file__ / sys.executable are
            inside the root, exactly as at real startup) and print the fail-closed problem list.
            A green result (exit 0) is the provisioning gate. Preflight itself enforces that the
            executor is OWNED by and RUNNING AS `--account` (SID-based) — a run from the repo, or
            by the wrong principal, fails by design.

This script is provisioning tooling; it never modifies the frozen executor code. Destructive
`--force` is guarded: no drive/filesystem root, and it refuses to overwrite a directory that
is not a marked executor install.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

def _find_repo() -> Path | None:
    """The git repo this script lives in, by walking up to a `.git` — NOT `parents[1]`, which is
    wrong for the copy bundled inside an artifact (deploy runs that copy from OUTSIDE any repo)."""
    p = Path(__file__).resolve()
    for d in [p.parent, *p.parents]:
        if (d / ".git").exists():
            return d
    return None


REPO = _find_repo()                          # None when running the bundled copy (no repo to avoid)
PKG_SRC = (REPO / "backend" / "app" / "change_executor") if REPO else None
_MARKER = ".elira-change-exec-install"      # written on deploy; required before any --force wipe
_MANIFEST = "MANIFEST.sha256"               # per-file hashes inside a release artifact
_PROVISIONER = "provision_change_executor.py"   # this tool, bundled + hashed into the artifact


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _fail(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(2)


def _venv_python(root: Path) -> Path:
    return root / ("venv/Scripts/python.exe" if os.name == "nt" else "venv/bin/python")


# --- identity (guard-rail; preflight is the authoritative, SID-based check) -----------------
def _principal_name_and_sid() -> tuple[str, str | None]:
    """(lowercased `domain\\user`, upper SID | None) of the current principal. `whoami /fo csv`
    is locale-independent (no labels to parse)."""
    if os.name == "nt":
        try:
            r = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"],
                               capture_output=True, text=True)
            row = next(csv.reader(io.StringIO(r.stdout.strip())))
            return row[0].strip().lower(), row[1].strip().upper()
        except Exception:  # noqa: BLE001
            return "", None
    import getpass
    return getpass.getuser().lower(), None


def _account_value(a: argparse.Namespace) -> str:
    account = (getattr(a, "account", None) or os.environ.get("ELIRA_CHANGE_EXECUTOR_ACCOUNT", "")).strip()
    if not account:
        _fail("--account (or ELIRA_CHANGE_EXECUTOR_ACCOUNT) is required: the executor's dedicated "
              "OS account, e.g. HOSTNAME\\elira-change-exec (or a raw SID).")
    return account


def _assert_running_as(account: str) -> None:
    name, sid = _principal_name_and_sid()
    acc = account.strip()
    ok = (sid == acc.upper()) if acc.upper().startswith("S-1-") else (name == acc.lower())
    if not ok:
        _fail(f"must run AS the executor account {account!r}, but the current principal is "
              f"{name or '?'}{(' / ' + sid) if sid else ''}. Create the account first, then run "
              f"deploy AS it (runas / a scheduled task as that account, or an elevated installer "
              f"that also sets owner to it). Files must be OWNED by the executor account.")


def _validate_base_python(base: str) -> str:
    if not base:
        _fail("--base-python is required: a dedicated, protected Python runtime - NOT the main "
              "backend's interpreter/venv (sharing it would couple the TCB to the main env).")
    bp = Path(base).resolve()
    if not bp.exists():
        _fail(f"--base-python not found: {bp}")
    if REPO is not None:
        try:
            bp.relative_to(REPO)
            _fail(f"--base-python {bp} is inside the repo - use a separate protected runtime.")
        except ValueError:
            pass
    return str(bp)


def _refuse_if_in_repo(root: Path) -> None:
    rp = root.resolve()
    if rp == rp.parent:      # a drive / filesystem root — a --force here would be catastrophic
        _fail(f"root {rp} is a drive/filesystem root - refuse.")
    if REPO is None:
        return               # running the bundled copy: there is no writable repo to be inside of
    try:
        rp.relative_to(REPO)
    except ValueError:
        return               # good: outside the repo
    _fail(f"root {rp} is inside the repository {REPO}. The executor MUST live outside the repo "
          f"(a run from a git working tree fails preflight by design). Choose e.g. C:\\elira-change-exec.")


def _require_marker(root: Path) -> None:
    if not (root / _MARKER).exists():
        _fail(f"refusing --force: {root} has no {_MARKER} marker, so it is not a known executor "
              f"install - this guards against wiping an unrelated directory. Deploy without "
              f"--force to a fresh root, or place the marker only if you are certain.")


def _manifest_bytes(hashes: dict) -> bytes:
    # exact bytes (LF, no BOM) so an EXTERNAL `Get-FileHash MANIFEST.sha256` equals the digest
    return "".join(f"{hashes[k]}  {k}\n" for k in sorted(hashes)).encode("utf-8")


def _manifest_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _verify_artifact(artifact: Path, expected_digest: str) -> dict:
    """Verify a trusted release artifact and return its {filename: sha256}. Deploy accepts code
    ONLY from such an artifact (built once from a known-good repo, then kept OUTSIDE any
    model/main-user-writable checkout) — never from the live repo, which the model could rewrite
    before deploy. Fail-closed on: artifact inside the repo, missing/tampered manifest, a
    manifest digest that does not match the out-of-band pin, or any file hash / set mismatch."""
    _refuse_if_in_repo(artifact)      # a trusted artifact must live outside the writable repo
    man = artifact / _MANIFEST
    if not man.is_file():
        _fail(f"no {_MANIFEST} in artifact {artifact} - build it with `build --out <dir>`.")
    man_bytes = man.read_bytes()      # hash the exact file bytes (matches an external Get-FileHash)
    text = man_bytes.decode("utf-8")
    digest = _manifest_digest(man_bytes)
    if digest.lower() != expected_digest.strip().lower():
        _fail(f"artifact manifest digest {digest} != pinned --manifest-sha256 "
              f"({expected_digest.strip()}) - tampered artifact or wrong pin. Refusing.")
    hashes: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        h, _, name = line.partition("  ")
        if not h or not name.strip():
            _fail(f"malformed manifest line: {line!r}")
        hashes[name.strip()] = h.strip().lower()
    present = {p.name for p in artifact.iterdir() if p.is_file() and p.suffix == ".py"}
    if present != set(hashes):
        _fail(f"artifact .py set {sorted(present)} != manifest {sorted(hashes)}")
    for name, want in hashes.items():
        got = _sha256_file(artifact / name).lower()
        if got != want:
            _fail(f"artifact file {name}: sha256 {got} != manifest {want} (tampered). Refusing.")
    return hashes


def _copy_from_artifact(root: Path, artifact: Path, hashes: dict, force: bool) -> Path:
    dst = root / "change_executor"
    pkg = {k: v for k, v in hashes.items() if k != _PROVISIONER}   # the provisioner is the tool, not deployed
    if dst.exists():
        # NEVER silently skip: a pre-existing change_executor/ (e.g. pre-planted by the main user
        # into the published root path, then laundered to executor-ownership by `icacls /setowner`)
        # would otherwise survive un-deployed while deploy reports success. Fail closed unless the
        # operator explicitly --force-replaces it.
        if not force:
            _fail(f"{dst} already exists - refusing to deploy over pre-existing code (it could be "
                  f"pre-planted). Deploy into a fresh root, or re-run with --force to replace it.")
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    for name in sorted(pkg):
        shutil.copy2(artifact / name, dst / name)
    # Re-verify the DEPLOYED bytes against the manifest, so "deployed" == "verified" (this also
    # closes any check-to-use gap between _verify_artifact and the copy).
    present = {p.name for p in dst.iterdir() if p.is_file() and p.suffix == ".py"}
    if present != set(pkg):
        _fail(f"deployed .py set {sorted(present)} != manifest {sorted(pkg)} after copy")
    for name, want in pkg.items():
        got = _sha256_file(dst / name).lower()
        if got != want:
            _fail(f"deployed {name} sha256 {got} != manifest {want} after copy - refusing.")
    print(f"  copied + re-verified {len(pkg)} module(s) from artifact -> {dst}")
    return dst


def _make_venv(root: Path, base_python: str, force: bool) -> Path:
    venv_dir = root / "venv"
    py = _venv_python(root)
    if py.exists() and not force:
        print(f"  venv already present (use --force to recreate): {venv_dir}")
        return venv_dir
    if venv_dir.exists() and not force:
        # never `venv --clear` (which wipes the dir) on a non-force run — only --force, which is
        # marker-guarded, may recreate an existing venv dir.
        _fail(f"{venv_dir} exists but is not a usable venv; refusing to recreate it without "
              f"--force (marker-guarded). Remove it manually if that is intended.")
    print(f"  creating dedicated venv with {base_python} ...")
    args = [base_python, "-m", "venv"] + (["--clear"] if force else []) + [str(venv_dir)]
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0 or not py.exists():
        _fail(f"venv creation failed: {r.stderr.strip() or r.stdout.strip()}")
    print(f"  venv python -> {py}")
    return venv_dir


def _write_registry_template(root: Path, host: str, port: int, user: str, unit: str,
                             force: bool) -> Path:
    cfg = root / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    reg = cfg / "registry.json"
    if reg.exists() and not force:
        print(f"  registry.json already present (left untouched): {reg}")
        return reg
    doc = {"targets": {"ai-server-netdata": {
        "host": host, "port": port, "remote_user": user,
        "known_hosts": str(cfg / "known_hosts"),
        "identity_file": str(cfg / "id_elira_change"),
        "unit": unit, "operation": "restart"}}}
    reg.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    print(f"  wrote registry template -> {reg}")
    return reg


def _next_steps(root: Path, account: str) -> None:
    cfg = root / "config"
    print("\nRemaining HOST steps (yours - see docs/CHANGE_EXECUTOR_PROVISIONING.md):")
    print(f"  * icacls {root} /setowner {account} /T   (own the whole tree as the executor)")
    print(f"  * icacls {root} /inheritance:r /grant:r \"{account}:(OI)(CI)F\" \"SYSTEM:(OI)(CI)F\"")
    print(f"  * key/bot-token: executor-only. ipc.token: give the MAIN user INHERITABLE read on")
    print(f"    the {root / 'ipc'} dir so the boot-minted token is main-readable, + traverse on root:")
    print(f"      icacls {root / 'ipc'} /inheritance:r /grant:r \"{account}:(OI)(CI)F\" \"SYSTEM:(OI)(CI)F\" \"MAINUSER:(OI)(R)\"")
    print(f"      icacls {root} /grant:r \"MAINUSER:(RX)\"")
    print(f"  * ssh-keygen -t ed25519 -f {cfg / 'id_elira_change'} -N \"\"")
    print(f"  * ssh-keyscan -p 22 <host> > {cfg / 'known_hosts'}   (then verify fp out-of-band)")
    print(f"  * SEPARATE Telegram bot (@BotFather) -> ELIRA_CHANGE_BOT_TOKEN + APPROVER_*_IDS")
    print(f"  * remote: adduser elira-change + narrow sudoers; re-point reads to elira-ro; revoke old identity")
    print(f"  * code came from the VERIFIED artifact (not the live repo); rebuild + re-pin the")
    print(f"    manifest digest for any update, always from a clean checkout.")
    print(f"  * then, AS {account}, FROM the artifact copy (hash-checked first):")
    print(f"      python \"{Path(__file__).resolve()}\" verify --root {root} --account {account}")


def cmd_build(a: argparse.Namespace) -> int:
    """Build a trusted release artifact from the (known-good) repo: copy the frozen package +
    write MANIFEST.sha256, and print the manifest digest to pin OUT-OF-BAND. Run this ONCE from a
    verified-clean checkout; thereafter deploy consumes only the artifact, not the live repo."""
    out = Path(a.out)
    if PKG_SRC is None or not PKG_SRC.is_dir():
        _fail("`build` must run from the repo checkout (the frozen package source was not found).")
    _refuse_if_in_repo(out)                # the artifact must live outside the writable repo
    out.mkdir(parents=True, exist_ok=True)
    hashes: dict = {}
    for src in sorted(PKG_SRC.iterdir()):
        if src.is_file() and src.suffix == ".py":       # only source; skip __pycache__/.pyc
            shutil.copy2(src, out / src.name)
            hashes[src.name] = _sha256_file(out / src.name)
    if not hashes:
        _fail(f"no .py modules found in {PKG_SRC}")
    # Bundle the provisioner ITSELF into the artifact and hash it, so deploy can be run from a
    # verified, trusted copy (not the writable repo) and the operator can hash-check it out-of-band
    # BEFORE executing it — a tampered repo-local provisioner would otherwise fake its own checks.
    shutil.copy2(Path(__file__), out / _PROVISIONER)
    hashes[_PROVISIONER] = _sha256_file(out / _PROVISIONER)
    man_bytes = _manifest_bytes(hashes)
    (out / _MANIFEST).write_bytes(man_bytes)
    digest = _manifest_digest(man_bytes)
    print(f"Built release artifact -> {out.resolve()}  ({len(hashes)} files incl. the provisioner)")
    print(f"  {_PROVISIONER} sha256 : {hashes[_PROVISIONER]}")
    print(f"  MANIFEST sha256          : {digest}")
    print("\nNEXT: build from a verified-clean repo only; move the artifact outside any writable repo;")
    print("record the MANIFEST sha256 out-of-band. Before deploy, hash-check the provisioner with an")
    print("EXTERNAL tool (Get-FileHash) against the manifest, then run deploy FROM the artifact copy.")
    return 0


def _require_run_from_artifact(cmd: str) -> None:
    # deploy/verify carry executor rights + reach the protected root/key/bot-token; they must run
    # from the VERIFIED, immutable artifact copy (REPO is None there), never the writable repo copy
    # (which could be swapped). `build` is the only subcommand that runs from the repo.
    if REPO is not None:
        _fail(f"`{cmd}` must run from the VERIFIED artifact copy "
              f"(<artifact>\\{_PROVISIONER}), not the repo checkout at {REPO}. Build first, "
              f"hash-check the artifact out-of-band, then run this from it.")


def cmd_deploy(a: argparse.Namespace) -> int:
    _require_run_from_artifact("deploy")
    root = Path(a.root)
    _refuse_if_in_repo(root)
    account = _account_value(a)
    _assert_running_as(account)            # files must be OWNED by the executor account
    base = _validate_base_python(a.base_python)
    artifact = Path(a.artifact).resolve()
    hashes = _verify_artifact(artifact, a.manifest_sha256)   # trusted code only; refuses the live repo
    if a.force and root.exists():
        _require_marker(root)              # never --force-wipe a directory that isn't our install
    root.mkdir(parents=True, exist_ok=True)
    (root / "ipc").mkdir(exist_ok=True)    # dedicated token dir: main-read is inherited here (Step 3)
    print(f"Deploying isolated change executor -> {root.resolve()}")
    _copy_from_artifact(root, artifact, hashes, a.force)
    _make_venv(root, base, a.force)
    _write_registry_template(root, a.target_host, a.target_port, a.remote_user, a.unit, a.force)
    (root / _MARKER).write_text("elira change executor install root\n", encoding="utf-8")   # mark a COMPLETED install (last)
    _next_steps(root, account)
    return 0


def cmd_verify(a: argparse.Namespace) -> int:
    _require_run_from_artifact("verify")
    root = Path(a.root).resolve()
    account = _account_value(a)            # required; preflight authoritatively checks running-as/owner
    py = _venv_python(root)
    if not py.exists():
        _fail(f"deployed venv python not found at {py} - run `deploy` first.")
    cfg = root / "config"
    env = dict(os.environ)     # inherit PATH etc. (preflight shells out to icacls on Windows)
    env.update({
        "ELIRA_CHANGE_EXECUTOR_ROOT": str(root),
        "ELIRA_CHANGE_EXECUTOR_ACCOUNT": account,
        "ELIRA_CHANGE_REGISTRY_PATH": str(cfg / "registry.json"),
        "ELIRA_CHANGE_STORE_PATH": str(root / "change.sqlite3"),
        "ELIRA_CHANGE_IPC_TOKEN_FILE": str(root / "ipc" / "ipc.token"),
        "PYTHONPATH": str(root),
    })
    if a.fill_placeholders:
        # structural dry run: satisfy the "is set / well-formed" checks so the ACL/owner results
        # are not drowned out. NEVER the real gate — set the true bot/approver values instead.
        env.setdefault("ELIRA_CHANGE_BOT_TOKEN", "PLACEHOLDER-not-a-real-bot-token")
        env.setdefault("ITOPS_CHANGE_APPROVER_USER_IDS", "1")
        env.setdefault("ITOPS_CHANGE_APPROVER_CHAT_IDS", "1")
    snippet = ("import json,sys\n"
               "from change_executor import preflight\n"
               "p=preflight.verify()\n"
               "print(json.dumps(p, indent=2))\n"
               "sys.exit(1 if p else 0)\n")
    print(f"Running DEPLOYED preflight via {py}\n(cwd={root}, package inside root)\n")
    r = subprocess.run([str(py), "-c", snippet], cwd=str(root), env=env,
                       capture_output=True, text=True)
    if r.stdout.strip():
        print(r.stdout.rstrip())
    if r.stderr.strip():
        print(r.stderr.rstrip(), file=sys.stderr)
    if r.returncode == 0:
        print("\nPREFLIGHT GREEN - zero isolation problems. Provisioning gate satisfied.")
    else:
        print("\nPREFLIGHT NOT GREEN - the problems above must be resolved before the executor "
              "may run (this is the fail-closed verification loop, not an error in the tool).")
    return r.returncode


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Provision / verify the isolated change executor.")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="build a trusted, hash-manifested release artifact from a clean repo")
    b.add_argument("--out", required=True, help="artifact output dir OUTSIDE this repo")
    b.set_defaults(func=cmd_build)

    d = sub.add_parser("deploy", help="deploy a VERIFIED release artifact + venv + registry into a protected root")
    d.add_argument("--root", required=True, help="protected executor root OUTSIDE this repo")
    d.add_argument("--account", default=None,
                   help="executor OS account (HOSTNAME\\elira-change-exec or a SID); deploy must run AS it")
    d.add_argument("--artifact", required=True,
                   help="trusted release artifact dir (from `build`), OUTSIDE the repo")
    d.add_argument("--manifest-sha256", required=True,
                   help="expected MANIFEST.sha256 digest, pinned out-of-band (deploy refuses a mismatch)")
    d.add_argument("--base-python", required=True,
                   help="a dedicated, protected base python to build the venv from (NOT the main backend's)")
    d.add_argument("--target-host", default="192.168.88.15", help="change target host (default ai-server)")
    d.add_argument("--target-port", type=int, default=22)
    d.add_argument("--remote-user", default="elira-change")
    d.add_argument("--unit", default="netdata.service")
    d.add_argument("--force", action="store_true",
                   help="overwrite an existing install (requires the install marker; never a drive root)")
    d.set_defaults(func=cmd_deploy)

    v = sub.add_parser("verify", help="run the deployed preflight from the deployed venv")
    v.add_argument("--root", required=True)
    v.add_argument("--account", default=None,
                   help="executor OS account; passed to preflight, which checks running-as/owner")
    v.add_argument("--fill-placeholders", action="store_true",
                   help="structural dry run: inject dummy bot/approver env (NOT the real gate)")
    v.set_defaults(func=cmd_verify)

    a = p.parse_args(argv)
    return int(a.func(a))


if __name__ == "__main__":
    raise SystemExit(main())
