"""Local user client for the protected Elira Windows service."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
import uuid
import winreg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from foundation_windows import current_user_token, pipe_request


def installed(service_name: str = "EliraFoundation") -> Path:
    if service_name not in {"EliraFoundation", "EliraFoundationProof"}:
        raise ValueError("Unknown Foundation service")
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Elira\{service_name}",
                        0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
        value, kind = winreg.QueryValueEx(key, "InstallRoot")
    if kind != winreg.REG_SZ or not Path(value).is_absolute():
        raise ValueError("Invalid protected Foundation registration")
    return Path(value)


def call(request: dict, *, service_name: str = "EliraFoundation") -> dict:
    installed(service_name)  # Missing service never falls back to a local supervisor.
    # Launch from an elevated terminal uses a linked limited token or a
    # validated restricted token for built-in Administrator without a link.
    # Application children never inherit this bootstrap's elevated token.
    with current_user_token(limited=True) as token:
        result = pipe_request("\\\\.\\pipe\\" + service_name + ".v1", request, service_name=service_name,
                              token=token, timeout=30)
    if not isinstance(result, dict):
        raise ValueError("Invalid Foundation response")
    if result.get("ok") is False:
        raise RuntimeError(str(result.get("error", "Foundation request failed")))
    if result.get("ok") is not True or not isinstance(result.get("result"), dict):
        raise ValueError("Invalid Foundation response envelope")
    return result["result"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service", default="EliraFoundation", choices=("EliraFoundation", "EliraFoundationProof"))
    parser.add_argument("command", choices=("status", "open", "close", "register", "prepare", "verify", "request", "rollback", "operation_status"))
    parser.add_argument("identifier", nargs="?")
    parser.add_argument("--request-id", default=None)
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--timeout", type=float, default=3600)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    request = {"version": 1, "operation": args.command}
    if args.command not in {"status", "operation_status"}:
        request["request_id"] = args.request_id or uuid.uuid4().hex
    if args.command in {"prepare", "verify", "request", "operation_status"}:
        if not args.identifier:
            parser.error("This operation requires an identifier")
        request["operation_id" if args.command == "operation_status" else "release_id"] = args.identifier
    elif args.identifier:
        parser.error("This operation has no identifier argument")
    result = call(request, service_name=args.service)
    deadline = time.monotonic() + args.timeout
    if args.wait and "operation_id" in result:
        operation_id = result["operation_id"]
        print(f"Foundation operation_id={operation_id}", file=sys.stderr, flush=True)
        while result.get("status") in {"queued", "running"}:
            if time.monotonic() >= deadline:
                print(json.dumps(result, ensure_ascii=False, indent=2))
                print("Operation continues in Foundation. Query operation_status with this operation_id.", file=sys.stderr)
                return 2
            time.sleep(0.5)
            result = call({"version": 1, "operation": "operation_status", "operation_id": operation_id}, service_name=args.service)
        if args.command == "open" and result.get("status") == "completed":
            while time.monotonic() < deadline:
                status = call({"version": 1, "operation": "status"}, service_name=args.service)
                if status.get("running"):
                    result["application"] = status
                    break
                if status.get("last_error"):
                    raise RuntimeError(status["last_error"])
                time.sleep(0.5)
            else:
                raise TimeoutError("Foundation accepted open but application startup is not confirmed")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get("status") in {"failed", "interrupted"} else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, TimeoutError) as error:
        print(f"Foundation: {error}", file=sys.stderr)
        raise SystemExit(1)
