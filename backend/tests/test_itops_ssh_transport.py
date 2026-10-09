from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.skill_services import itops as itops_provider


def test_itops_batch_ssh_closes_remote_stdin() -> None:
    argv = itops_provider._ssh_argv("server.example", ["hostname"])

    assert argv[0] == "ssh"
    assert "-n" in argv
    assert "BatchMode=yes" in argv
    assert "ConnectTimeout=10" in argv
    assert argv[-2:] == ["server.example", "hostname"]
