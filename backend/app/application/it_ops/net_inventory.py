"""Generic Workflow-owned TCP inventory runtime.

The scanner accepts any canonical IPv4 CIDR and any valid TCP ports. It has no
product host cap, fixed authorization subnet, rate budget, or wall-clock stop.
Per-connect timeout is transport liveness; Workflow Stop cancels scheduling.
"""
from __future__ import annotations

import concurrent.futures
import errno
import ipaddress
import socket
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable

VANTAGE = "elira-local"


class CidrError(ValueError):
    def __init__(self, reason: str, http_status: int = 400):
        super().__init__(reason)
        self.reason = reason
        self.http_status = http_status


@dataclass(frozen=True)
class PortProfile:
    name: str
    ports: tuple[int, ...]
    per_connect_timeout: float = 1.0
    in_flight: int = 64


PROFILE_COMMON_V1 = PortProfile(
    name="common",
    ports=(22, 80, 443, 445, 139, 3389, 3306, 5432),
)


def build_profile(
    ports: Iterable[int] | None = None,
    *,
    per_connect_timeout: float = 1.0,
    in_flight: int = 64,
) -> PortProfile:
    raw_ports = tuple(ports) if ports is not None else PROFILE_COMMON_V1.ports
    normalized: list[int] = []
    seen: set[int] = set()
    for raw in raw_ports:
        if isinstance(raw, bool):
            raise CidrError("invalid_port")
        try:
            port = int(raw)
        except (TypeError, ValueError) as exc:
            raise CidrError("invalid_port") from exc
        if not 1 <= port <= 65535:
            raise CidrError("invalid_port")
        if port not in seen:
            seen.add(port)
            normalized.append(port)
    if not normalized:
        raise CidrError("ports_empty")
    try:
        timeout = float(per_connect_timeout)
        workers = int(in_flight)
    except (TypeError, ValueError) as exc:
        raise CidrError("invalid_scan_options") from exc
    if timeout <= 0 or workers <= 0:
        raise CidrError("invalid_scan_options")
    return PortProfile(
        name="custom" if tuple(normalized) != PROFILE_COMMON_V1.ports else "common",
        ports=tuple(normalized),
        per_connect_timeout=timeout,
        in_flight=workers,
    )


def validate_profile(profile: PortProfile, host_count: int = 0) -> None:
    """Compatibility validator for value integrity, never work authorization."""
    del host_count
    build_profile(
        profile.ports,
        per_connect_timeout=profile.per_connect_timeout,
        in_flight=profile.in_flight,
    )


def parse_cidr_v1(cidr: str) -> ipaddress.IPv4Network:
    text = str(cidr or "").strip()
    try:
        network = ipaddress.ip_network(text, strict=True)
    except ValueError as exc:
        raise CidrError("cidr_not_canonical_or_invalid") from exc
    if network.version != 4:
        raise CidrError("ipv6_not_supported")
    return network


def usable_host_count(network: ipaddress.IPv4Network) -> int:
    if network.prefixlen >= 31:
        return network.num_addresses
    return max(0, network.num_addresses - 2)


def allowed_cidrs() -> list[ipaddress.IPv4Network]:
    """Compatibility surface: no authorization subnet list exists."""
    return []


def cidr_authorized(cidr: str) -> bool:
    try:
        return ipaddress.ip_network(str(cidr).strip(), strict=False).version == 4
    except ValueError:
        return False


def local_source_ip(dest: str) -> str:
    if not dest:
        return ""
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect((dest, 9))
        return str(sock.getsockname()[0])
    except OSError:
        return ""
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


_STATES = ("open", "refused", "timeout", "unreachable", "local_error")


@dataclass
class ScanResult:
    cidr: str
    profile: str
    vantage: str
    source_ip: str
    started_at: float
    finished_at: float
    planned: int
    attempted: int
    completed: int
    counts: dict[str, int] = field(default_factory=dict)
    opens: list[dict[str, int | str]] = field(default_factory=list)
    stop_reason: str = "complete"
    status: str = "complete"


def _tcp_connect(host: str, port: int, timeout: float) -> str:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return "open"
    except ConnectionRefusedError:
        return "refused"
    except socket.timeout:
        return "timeout"
    except OSError as exc:
        if exc.errno in (errno.EHOSTUNREACH, errno.ENETUNREACH, errno.EHOSTDOWN):
            return "unreachable"
        return "local_error"


def run_scan(
    cidr: str,
    hosts: Iterable[str],
    profile: PortProfile,
    *,
    host_count: int | None = None,
    connect_fn: Callable[[str, int, float], str] | None = None,
    monotonic: Callable[[], float] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> ScanResult:
    connect_fn = connect_fn or _tcp_connect
    monotonic = monotonic or time.monotonic
    should_stop = should_stop or (lambda: False)
    validate_profile(profile)
    planned = int(host_count) * len(profile.ports) if host_count is not None else 0
    counts = {state: 0 for state in _STATES}
    opens: list[dict[str, int | str]] = []
    started = monotonic()
    attempted = 0
    completed = 0
    stopped = False
    target_iter = ((str(host), port) for host in hosts for port in profile.ports)
    source_ip = ""

    with concurrent.futures.ThreadPoolExecutor(max_workers=profile.in_flight) as pool:
        pending: dict[concurrent.futures.Future[str], tuple[str, int]] = {}
        exhausted = False
        while pending or not exhausted:
            if should_stop():
                stopped = True
                for future in pending:
                    future.cancel()
                break
            while not exhausted and len(pending) < profile.in_flight:
                try:
                    host, port = next(target_iter)
                except StopIteration:
                    exhausted = True
                    break
                if not source_ip:
                    source_ip = local_source_ip(host)
                future = pool.submit(
                    connect_fn,
                    host,
                    port,
                    profile.per_connect_timeout,
                )
                pending[future] = (host, port)
                attempted += 1
            if not pending:
                continue
            done, _ = concurrent.futures.wait(
                pending,
                timeout=0.2,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            for future in done:
                host, port = pending.pop(future)
                try:
                    state = future.result()
                except Exception:
                    state = "local_error"
                completed += 1
                counts[state] = counts.get(state, 0) + 1
                if state == "open":
                    opens.append({"host": host, "port": port})

    finished = monotonic()
    if planned == 0:
        planned = attempted if stopped else completed
    return ScanResult(
        cidr=cidr,
        profile=profile.name,
        vantage=VANTAGE,
        source_ip=source_ip,
        started_at=started,
        finished_at=finished,
        planned=planned,
        attempted=attempted,
        completed=completed,
        counts=counts,
        opens=opens,
        stop_reason="stopped" if stopped else "complete",
        status="partial" if stopped else "complete",
    )
