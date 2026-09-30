"""Local user client for the protected Elira Windows service."""
from __future__ import annotations

import argparse
import json
import math
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
        try:
            value, kind = winreg.QueryValueEx(key, "InstallRoot")
        except FileNotFoundError as exc:
            raise ValueError("Foundation registration is missing InstallRoot") from exc
    if kind != winreg.REG_SZ or not Path(value).is_absolute():
        raise ValueError("Invalid protected Foundation registration")
    return Path(value)


def installed_token_mode(service_name: str) -> str:
    installed(service_name)
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Elira\{service_name}",
                        0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
        try:
            value, kind = winreg.QueryValueEx(key, "ApplicationTokenMode")
        except FileNotFoundError:
            return "limited"  # Existing installations retain their original contract.
    if kind != winreg.REG_SZ or value not in {"limited", "administrator"}:
        raise ValueError("Invalid protected application token mode")
    return value


def installation_paths(service_name: str = "EliraFoundation") -> dict:
    """Read non-secret path bindings without service IPC or private state access."""
    root = installed(service_name)
    result = {"install_root": str(root), "python": str(root / "python/python.exe"),
              "host": str(root / "host")}
    names = {"platform": "Platform", "store": "StateRoot", "candidates": "Candidates",
             "published": "Published", "data": "Data", "journals": "Journals"}
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Elira\{service_name}",
                            0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            for field, name in names.items():
                value, kind = winreg.QueryValueEx(key, name)
                if kind != winreg.REG_SZ or not Path(value).is_absolute():
                    raise ValueError(f"Invalid protected Foundation {name} binding")
                result[field] = str(Path(value))
            port, kind = winreg.QueryValueEx(key, "Port")
            if kind != winreg.REG_DWORD or not 1024 <= port <= 65535:
                raise ValueError("Invalid protected Foundation port binding")
            result["port"] = port
    except FileNotFoundError as exc:
        raise ValueError("Foundation registration is missing its path bindings") from exc
    result["application_token_mode"] = installed_token_mode(service_name)
    return result


def call(request: dict, *, service_name: str = "EliraFoundation") -> dict:
    token_mode = installed_token_mode(service_name)
    # Only the protected installation selects the mode. Administrator mode
    # accepts the existing elevated user; it never requests a new elevation.
    with current_user_token(token_mode=token_mode) as token:
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
    parser.add_argument("command", choices=("status", "open", "close", "register", "prepare", "verify", "request", "confirm", "rollback", "operation_status"))
    parser.add_argument("identifier", nargs="?")
    parser.add_argument("--request-id", default=None)
    parser.add_argument("--expected-active")
    parser.add_argument("--expected-previous")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--timeout", type=float, default=0, help="Observer wait limit; 0 waits without a time limit")
    args = parser.parse_args()
    if args.timeout < 0 or not math.isfinite(args.timeout):
        parser.error("--timeout must be finite and nonnegative")
    request = {"version": 1, "operation": args.command}
    if args.command not in {"status", "operation_status"}:
        request["request_id"] = args.request_id or uuid.uuid4().hex
    if args.command in {"prepare", "verify", "request", "confirm", "operation_status"}:
        if not args.identifier:
            parser.error("This operation requires an identifier")
        field = {"operation_status": "operation_id", "confirm": "confirmation_id"}.get(args.command, "release_id")
        request[field] = args.identifier
    elif args.identifier:
        parser.error("This operation has no identifier argument")
    if args.expected_active or args.expected_previous or args.confirm:
        if args.command != "rollback" or not args.expected_active or not args.expected_previous or not args.confirm:
            parser.error("Confirmed rollback requires --expected-active, --expected-previous and --confirm")
        request.update(expected_active=args.expected_active, expected_previous=args.expected_previous, confirm=True)
    result = call(request, service_name=args.service)
    deadline = time.monotonic() + args.timeout if args.timeout else None
    if args.wait and "operation_id" in result:
        operation_id = result["operation_id"]
        print(f"Foundation operation_id={operation_id}", file=sys.stderr, flush=True)
        while result.get("status") in {"queued", "running"}:
            if deadline is not None and time.monotonic() >= deadline:
                print(json.dumps(result, ensure_ascii=False, indent=2))
                print("Operation continues in Foundation. Query operation_status with this operation_id.", file=sys.stderr)
                return 2
            time.sleep(0.5)
            result = call({"version": 1, "operation": "operation_status", "operation_id": operation_id}, service_name=args.service)
        if args.command == "open" and result.get("status") == "completed":
            while deadline is None or time.monotonic() < deadline:
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
