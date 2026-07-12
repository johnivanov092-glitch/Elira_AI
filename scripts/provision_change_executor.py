#!/usr/bin/env python3
"""Provision / verify the isolated change executor (see docs/CHANGE_EXECUTOR_PROVISIONING.md).

Two subcommands, both stdlib-only:

  deploy  — copy the FROZEN `change_executor` package + a dedicated venv + a registry template
            into a protected root OUTSIDE this repo. Does NOT create OS accounts, set ACLs,
            generate the SSH key, populate known_hosts, or create the Telegram bot — those are
            human/host steps (some are prohibited for the agent) and are printed as next steps.

  verify  — run the DEPLOYED preflight via the DEPLOYED venv (so __file__ / sys.executable are
            inside the root, exactly as at real startup) and print the fail-closed problem list.
            A green result (exit 0, no problems) is the provisioning gate. Running preflight from
            the repo instead MUST fail (git working tree + package-not-inside-root) — by design.

This script is provisioning tooling; it never modifies the frozen executor code.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PKG_SRC = REPO / "backend" / "app" / "change_executor"


def _fail(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(2)


def _venv_python(root: Path) -> Path:
    return root / ("venv/Scripts/python.exe" if os.name == "nt" else "venv/bin/python")


def _refuse_if_in_repo(root: Path) -> None:
    try:
        root.resolve().relative_to(REPO)
    except ValueError:
        return  # good: outside the repo
    _fail(f"root {root} is inside the repository {REPO}. The executor MUST live outside the "
          f"repo (a run from a git working tree fails preflight by design). Choose e.g. "
          f"C:\\elira-change-exec.")


def _copy_package(root: Path, force: bool) -> Path:
    if not PKG_SRC.is_dir():
        _fail(f"frozen package not found at {PKG_SRC}")
    dst = root / "change_executor"
    if dst.exists() and not force:
        print(f"  change_executor/ already present (use --force to overwrite): {dst}")
        return dst
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    n = 0
    for src in sorted(PKG_SRC.iterdir()):
        if src.is_file() and src.suffix == ".py":      # copy only source; skip __pycache__/.pyc
            shutil.copy2(src, dst / src.name)
            n += 1
    print(f"  copied {n} frozen module(s) -> {dst}")
    return dst


def _make_venv(root: Path, base_python: str, force: bool) -> Path:
    venv_dir = root / "venv"
    py = _venv_python(root)
    if py.exists() and not force:
        print(f"  venv already present (use --force to recreate): {venv_dir}")
        return venv_dir
    print(f"  creating dedicated venv with {base_python} ...")
    r = subprocess.run([base_python, "-m", "venv", "--clear", str(venv_dir)],
                       capture_output=True, text=True)
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


def _next_steps(root: Path) -> None:
    cfg = root / "config"
    print("\nRemaining HOST steps (yours - see docs/CHANGE_EXECUTOR_PROVISIONING.md):")
    print(f"  2. Create non-elevated OS account 'elira-change-exec' (NOT in Administrators).")
    print(f"  3. ACL-lock the root: remove inheritance; grant write only to elira-change-exec")
    print(f"     + SYSTEM; deny the main Elira user read on the key/token; protect the base Python.")
    print(f"  4. ssh-keygen -t ed25519 -f {cfg / 'id_elira_change'} -N \"\"")
    print(f"     ssh-keyscan -p 22 <host> > {cfg / 'known_hosts'}   (then verify fp out-of-band)")
    print(f"  5. Create a SEPARATE Telegram bot (@BotFather); set ELIRA_CHANGE_BOT_TOKEN and")
    print(f"     ITOPS_CHANGE_APPROVER_USER_IDS / _CHAT_IDS for the executor service.")
    print(f"  6. Remote: adduser elira-change + narrow sudoers (restart netdata only).")
    print(f"  7. Re-point reads to elira-ro (no sudo); revoke the old sudo-capable identity.")
    print(f"  9. Then: python scripts/provision_change_executor.py verify --root {root}")


def cmd_deploy(a: argparse.Namespace) -> int:
    root = Path(a.root)
    _refuse_if_in_repo(root)
    root.mkdir(parents=True, exist_ok=True)
    print(f"Deploying isolated change executor -> {root}")
    _copy_package(root, a.force)
    _make_venv(root, a.base_python, a.force)
    _write_registry_template(root, a.target_host, a.target_port, a.remote_user, a.unit, a.force)
    _next_steps(root)
    return 0


def cmd_verify(a: argparse.Namespace) -> int:
    root = Path(a.root).resolve()
    py = _venv_python(root)
    if not py.exists():
        _fail(f"deployed venv python not found at {py} - run `deploy` first.")
    cfg = root / "config"
    env = dict(os.environ)     # inherit PATH etc. (preflight shells out to icacls on Windows)
    env.update({
        "ELIRA_CHANGE_EXECUTOR_ROOT": str(root),
        "ELIRA_CHANGE_REGISTRY_PATH": str(cfg / "registry.json"),
        "ELIRA_CHANGE_STORE_PATH": str(root / "change.sqlite3"),
        "ELIRA_CHANGE_IPC_TOKEN_FILE": str(root / "ipc.token"),
        "PYTHONPATH": str(root),
    })
    if a.fill_placeholders:
        # structural dry run: satisfy the "is set / well-formed" checks so the ACL/containment
        # results are not drowned out. NEVER use for the real gate — set the true values instead.
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

    d = sub.add_parser("deploy", help="copy frozen package + venv + registry template into a protected root")
    d.add_argument("--root", required=True, help="protected executor root OUTSIDE this repo")
    d.add_argument("--target-host", default="192.168.88.15", help="change target host (default ai-server)")
    d.add_argument("--target-port", type=int, default=22)
    d.add_argument("--remote-user", default="elira-change")
    d.add_argument("--unit", default="netdata.service")
    d.add_argument("--base-python", default=sys.executable,
                   help="base python to build the venv from (protect it too)")
    d.add_argument("--force", action="store_true", help="overwrite an existing package/venv")
    d.set_defaults(func=cmd_deploy)

    v = sub.add_parser("verify", help="run the deployed preflight from the deployed venv")
    v.add_argument("--root", required=True)
    v.add_argument("--fill-placeholders", action="store_true",
                   help="structural dry run: inject dummy bot/approver env (NOT the real gate)")
    v.set_defaults(func=cmd_verify)

    a = p.parse_args(argv)
    return int(a.func(a))


if __name__ == "__main__":
    raise SystemExit(main())
