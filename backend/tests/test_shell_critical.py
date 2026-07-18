from __future__ import annotations

import unittest

from app.application.code_agent.tools._shell import (
    _blocked_shell_fragment,
    is_shell_critical,
    is_shell_safe,
)
from app.application.code_agent.loop_helpers import (
    _call_auto_approves,
    _is_critical_call,
    _mode_auto_approves,
)


class ShellCriticalTest(unittest.TestCase):
    """Destructive-but-legitimate commands must ALWAYS ask (even in bypass);
    catastrophic ones stay hard-blocked; read-only ones stay auto."""

    def test_destructive_commands_are_critical(self) -> None:
        for cmd in (
            "rm file.txt",
            "rm -f build/out",
            "del old.log",
            "git reset --hard",
            "git clean -fd",
            "git checkout -- src/app.py",
            "git push --force origin main",
            "git push -f",
            "git branch -d feature",
            "git stash drop",
            "docker rm mycontainer",
            "docker system prune -f",
            "docker compose down",
            "kill 1234",
            "pkill python",
            "taskkill /PID 1234 /F",
            "pip uninstall requests",
            "DROP TABLE users",
            "delete from orders",
            "sudo systemctl restart ssh.service",
            "ufw disable",
            "iptables -F",
            "netsh advfirewall set allprofiles state off",
            "ip route flush table main",
            "alembic upgrade head",
            "python manage.py migrate",
            "echo hi && rm important.txt",
        ):
            self.assertTrue(is_shell_critical(cmd), f"should be critical: {cmd!r}")

    def test_read_only_and_benign_not_critical(self) -> None:
        for cmd in ("git status", "ls", "cat README.md", "grep foo .", "echo hi",
                    "pytest", "npm run build", "python -c \"print(1)\"", ""):
            self.assertFalse(is_shell_critical(cmd), f"should NOT be critical: {cmd!r}")

    def test_catastrophic_still_hard_blocked(self) -> None:
        # These stay in the blocklist — never run, even with approval.
        for cmd in ("rm -rf /", "mkfs.ext4 /dev/sda", "shutdown now", ":(){:|:&};:"):
            self.assertIsNotNone(_blocked_shell_fragment(cmd), f"should be blocked: {cmd!r}")

    def test_git_reset_moved_from_block_to_critical(self) -> None:
        # git reset --hard is no longer hard-blocked (so it CAN run with approval)
        # but IS critical (must be confirmed, even in bypass).
        self.assertIsNone(_blocked_shell_fragment("git reset --hard"))
        self.assertTrue(is_shell_critical("git reset --hard"))

    def test_is_critical_call_routing(self) -> None:
        self.assertTrue(_is_critical_call("run_bash", {"command": "rm x"}))
        self.assertFalse(_is_critical_call("run_bash", {"command": "ls"}))
        self.assertFalse(_is_critical_call("write_file", {"path": "a", "content": "b"}))
        self.assertFalse(_is_critical_call("run_bash", None))

    def test_bypass_would_auto_but_critical_blocks_it(self) -> None:
        # The exact-call policy incorporates the critical veto itself.
        name, args = "run_bash", {"command": "rm secret.txt"}
        self.assertTrue(_mode_auto_approves("bypass", name))
        self.assertTrue(_is_critical_call(name, args))
        self.assertFalse(_call_auto_approves("bypass", name, args))
        # benign command in bypass: auto, not critical
        benign = {"command": "npm install"}
        self.assertTrue(_mode_auto_approves("bypass", name))
        self.assertFalse(_is_critical_call(name, benign))
        self.assertTrue(_call_auto_approves("bypass", name, benign))


if __name__ == "__main__":
    unittest.main()
