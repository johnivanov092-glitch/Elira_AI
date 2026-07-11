"""Phase 3 — Network Inventory (read-only), the bounded TCP-connect scanner.

Read-only network inventory: TCP connect() to hosts in a bound CIDR on a fixed,
server-owned port profile, within HARD runtime caps. Nothing here is model-driven —
the CIDR, ports, and caps come from the operation scope, never from the model.

Explicitly NOT done (forbidden by the program): UDP, raw sockets, brute-force,
banner exploitation, OS fingerprinting, CVE conclusions, "scan everything". A
connect either succeeds (open) or fails (refused/timeout/unreachable/local_error);
a non-open result is NEVER called "closed" and NEVER "host absent".

Authorization (v1): a requested CIDR is allowed ONLY if it is a strict subnet of an
entry in the ITOPS_NETWORK_ALLOWED_CIDRS env allowlist. Local interface routes are a
UI hint only — a VPN/client route is a local route too, so they are never trusted.
"""
from __future__ import annotations

import concurrent.futures
import ipaddress
import os
import socket
import time
from dataclasses import dataclass, field

VANTAGE = "elira-local"     # v1: the scan runs from the Elira host; not a client param.


class CidrError(ValueError):
    """A rejected CIDR. `http_status` maps to the route response (400/403)."""

    def __init__(self, reason: str, http_status: int = 400):
        super().__init__(reason)
        self.reason = reason
        self.http_status = http_status


@dataclass(frozen=True)
class PortProfile:
    name: str
    ports: tuple[int, ...]
    rate_limit: int            # max connects started per second
    total_timeout: float       # whole-scan wall-clock budget (s)
    per_connect_timeout: float # per TCP connect (s)
    in_flight: int             # max concurrent connects
    max_hosts: int             # hard host cap
    min_prefix: int            # smallest allowed prefix length (>= => fewer hosts)


# The ONE server-owned profile for v1. Ports are a small common-service set (<=8).
# Sized so 254 hosts x 8 ports = 2032 attempts fits 50/s x 60s = 3000 with margin.
PROFILE_COMMON_V1 = PortProfile(
    name="common-v1",
    ports=(22, 80, 443, 445, 139, 3389, 3306, 5432),
    rate_limit=50,
    total_timeout=60.0,
    per_connect_timeout=1.0,
    in_flight=64,
    max_hosts=254,
    min_prefix=24,
)


def validate_profile(profile: PortProfile, host_count: int) -> None:
    """Refuse a profile whose planned work cannot finish inside the budget, so a
    future profile can never silently exceed the caps. Raises CidrError(400)."""
    if len(profile.ports) > 8:
        raise CidrError("profile_too_many_ports", 400)
    planned = host_count * len(profile.ports)
    budget = profile.rate_limit * profile.total_timeout   # attempts the rate allows
    if planned > int(budget * 0.9):                       # 10% margin for timeouts
        raise CidrError("profile_exceeds_budget", 400)


# ── CIDR parsing + authorization (v1: IPv4, strict, private, >= /24) ──────────

def parse_cidr_v1(cidr: str) -> list[str]:
    """Parse a v1-eligible CIDR → the list of usable host IPs. Raises CidrError with
    an http_status: IPv6 → 400, non-canonical → 400, prefix < 24 / too many hosts →
    400, public → 403."""
    text = str(cidr or "").strip()
    try:
        net = ipaddress.ip_network(text, strict=True)     # strict → host bits set is an error
    except ValueError:
        # distinguish "not canonical" from "not a network at all" is not worth it here
        raise CidrError("cidr_not_canonical_or_invalid", 400)
    if net.version != 4:
        raise CidrError("ipv6_not_supported", 400)
    if net.prefixlen < 24:
        raise CidrError("cidr_too_large", 400)
    if not net.is_private:
        raise CidrError("public_cidr", 403)
    hosts = [str(h) for h in net.hosts()]
    if len(hosts) > PROFILE_COMMON_V1.max_hosts:
        raise CidrError("cidr_too_large", 400)
    return hosts


def allowed_cidrs() -> list[ipaddress.IPv4Network]:
    """The explicit v1 allowlist from ITOPS_NETWORK_ALLOWED_CIDRS (comma-separated).
    Empty/unset ⇒ nothing authorized (default-deny). Non-IPv4 entries are ignored."""
    raw = os.environ.get("ITOPS_NETWORK_ALLOWED_CIDRS", "")
    out: list[ipaddress.IPv4Network] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            n = ipaddress.ip_network(part, strict=False)
            if n.version == 4:
                out.append(n)
        except ValueError:
            continue
    return out


def cidr_authorized(cidr: str) -> bool:
    """True iff *cidr* is a strict subnet of an ITOPS_NETWORK_ALLOWED_CIDRS entry.
    Local interface subnets are NOT auto-authorized."""
    try:
        req = ipaddress.ip_network(str(cidr).strip(), strict=False)
    except ValueError:
        return False
    if req.version != 4:
        return False
    return any(req.subnet_of(a) for a in allowed_cidrs())


def local_source_ip(dest: str) -> str:
    """Best-effort local source IP the kernel would use to reach *dest* — recorded in
    the summary alongside VANTAGE so evidence names the RIGHT egress interface even on a
    multi-NIC / VPN host. *dest* must be an IP in the bound CIDR (a UDP `connect` sends
    no packet; it only makes the kernel resolve the route + source address). Never
    raises; empty string when dest is empty or the route cannot be determined."""
    d = str(dest or "").strip()
    if not d:
        return ""
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((d, 9))   # no packet is sent for a UDP connect; route/source only
        return s.getsockname()[0]
    except OSError:
        return ""
    finally:
        if s is not None:
            try:
                s.close()
            except OSError:
                pass


# ── the bounded scan ─────────────────────────────────────────────────────────

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
    counts: dict = field(default_factory=dict)       # per-state counts
    opens: list = field(default_factory=list)        # [{host, port}] confirmed open
    stop_reason: str = "complete"                    # complete | budget_exhausted | timed_out
    status: str = "complete"                         # complete | partial | timed_out


def _tcp_connect(host: str, port: int, timeout: float) -> str:
    """One TCP connect → a state (never raises). open/refused/timeout/unreachable/
    local_error. A non-open result is NOT 'closed'."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return "open"
    except ConnectionRefusedError:
        return "refused"
    except socket.timeout:
        return "timeout"
    except OSError as exc:
        import errno
        if exc.errno in (errno.EHOSTUNREACH, errno.ENETUNREACH, errno.EHOSTDOWN):
            return "unreachable"
        return "local_error"


def run_scan(cidr: str, hosts: list[str], profile: PortProfile, *,
             connect_fn=None, monotonic=None, sleep=None) -> ScanResult:
    """Bounded TCP-connect scan of hosts x profile.ports. Stops on the total_timeout
    deadline; remaining attempts are left un-attempted (status=timed_out). connect_fn,
    monotonic and sleep are injectable for tests."""
    connect_fn = connect_fn or _tcp_connect
    monotonic = monotonic or time.monotonic
    sleep = sleep or time.sleep
    targets = [(h, p) for h in hosts for p in profile.ports]
    planned = len(targets)
    counts = {s: 0 for s in _STATES}
    opens: list[dict] = []
    started = monotonic()
    # Source IP is resolved toward the bound subnet (first host), never a fixed sentinel,
    # so it reflects the interface that actually reaches the scanned CIDR.
    source_ip = local_source_ip(hosts[0] if hosts else "")
    attempted = 0
    completed = 0
    stop_reason = "complete"
    min_interval = 1.0 / profile.rate_limit if profile.rate_limit > 0 else 0.0
    last_started = [started - min_interval]

    def _remaining() -> float:
        return profile.total_timeout - (monotonic() - started)

    def _rate_gate() -> float:
        """Throttle to rate_limit WITHOUT sleeping past the deadline, and return the
        remaining budget the caller should submit against (<= 0 → drop this target, do
        not submit). The throttle sleep is itself capped at the remaining budget, so the
        tool never blocks past total_timeout. `last_started` is advanced only when a
        submit will actually proceed (remaining > 0)."""
        if min_interval > 0:
            wait = last_started[0] + min_interval - monotonic()
            if wait > 0:
                rem = _remaining()
                if rem <= 0:                       # deadline already gone — never sleep
                    return rem
                sleep(min(wait, rem))              # never sleep past the deadline
        rem = _remaining()
        if rem > 0:
            last_started[0] = monotonic()          # advance the throttle only on a real submit
        return rem

    finished = started
    timed_out = False
    with concurrent.futures.ThreadPoolExecutor(max_workers=profile.in_flight) as ex:
        pending: set = set()
        it = iter(targets)
        exhausted = False
        while True:
            if _remaining() <= 0:
                timed_out, finished = True, monotonic()
                break
            # Top up the in-flight window, honouring the deadline PER TARGET — the check
            # lives inside this loop (not only at the outer top) so a full window is
            # never submitted past the deadline. The rate-gate itself is deadline-aware
            # (it never sleeps past the budget) and returns the remaining budget to
            # submit against.
            while not exhausted and len(pending) < profile.in_flight:
                if _remaining() <= 0:
                    timed_out, finished = True, monotonic()
                    break
                try:
                    host, port = next(it)
                except StopIteration:
                    exhausted = True
                    break
                rem = _rate_gate()
                if rem <= 0:
                    # The deadline arrived at/within the rate-gate: drop this target
                    # un-attempted rather than launch a connect past the deadline.
                    timed_out, finished = True, monotonic()
                    break
                attempted += 1
                # Cap the connect timeout at the remaining budget so an in-flight
                # socket can never outlive the overall total_timeout.
                fut = ex.submit(connect_fn, host, port, min(profile.per_connect_timeout, rem))
                fut._t = (host, port)  # type: ignore[attr-defined]
                pending.add(fut)
            if timed_out:
                break
            if not pending:
                finished = monotonic()
                break
            # Poll for completions, but never block past the deadline: cap the wait at
            # the remaining budget (<=0 → non-blocking poll, then the outer check breaks).
            done, pending = concurrent.futures.wait(
                pending, timeout=min(0.2, max(0.0, _remaining())),
                return_when=concurrent.futures.FIRST_COMPLETED)
            for fut in done:
                host, port = fut._t  # type: ignore[attr-defined]
                try:
                    state = fut.result()
                except Exception:  # noqa: BLE001
                    state = "local_error"
                completed += 1
                counts[state] = counts.get(state, 0) + 1
                if state == "open":
                    opens.append({"host": host, "port": port})
        # `finished` is stamped at the moment we STOP scheduling, so finished_at honours
        # the hard cap. Any still-in-flight connects then drain on with-exit
        # (shutdown(wait=True)) — each capped at min(per_connect_timeout, remaining) at
        # submit, so none outlives the deadline — and that drain is deliberately excluded
        # from finished_at, not counted as attempted/completed.
    if timed_out:
        stop_reason = "timed_out"
    status = "complete" if (stop_reason == "complete" and attempted == planned and completed == attempted) else \
        ("timed_out" if stop_reason == "timed_out" else "partial")
    return ScanResult(cidr=cidr, profile=profile.name, vantage=VANTAGE, source_ip=source_ip,
                      started_at=started, finished_at=finished, planned=planned,
                      attempted=attempted, completed=completed, counts=counts,
                      opens=opens, stop_reason=stop_reason, status=status)
