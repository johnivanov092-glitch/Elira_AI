from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.change_executor.policy import (  # noqa: E402
    ASK,
    AUTO,
    SafetyEvidence,
    decide_approval,
    evidence_for_tool_call,
    shell_command_is_high_impact,
)
from app.application.code_agent.tool_policy import SEARCH_ACTIVATABLE_SIDE_EFFECT  # noqa: E402


class ApprovalPolicyTest(unittest.TestCase):
    def test_ask_mode_requires_approval_for_every_change(self) -> None:
        evidence = SafetyEvidence.low_risk_reversible()
        self.assertEqual(decide_approval("ask", "local", evidence), ASK)

    def test_remote_change_always_requires_telegram_approval(self) -> None:
        evidence = SafetyEvidence(
            impact="high",
            reversibility="transactional",
            authoritative=True,
            postcheck=True,
        )
        self.assertEqual(decide_approval("bypass", "remote", evidence), ASK)

    def test_impact_mode_auto_approves_only_low_risk_reversible_work(self) -> None:
        self.assertEqual(
            decide_approval("accept_edits", "local", SafetyEvidence.low_risk_reversible()),
            AUTO,
        )
        self.assertEqual(
            decide_approval(
                "accept_edits",
                "local",
                SafetyEvidence(impact="material", reversibility="staged_rollback"),
            ),
            ASK,
        )

    def test_bypass_allows_normal_change_but_not_unknown_or_unprotected_high_risk(self) -> None:
        self.assertEqual(
            decide_approval("bypass", "local", SafetyEvidence(impact="material")),
            AUTO,
        )
        self.assertEqual(decide_approval("bypass", "local", SafetyEvidence()), ASK)
        self.assertEqual(
            decide_approval("bypass", "local", SafetyEvidence(impact="high")),
            ASK,
        )

    def test_bypass_high_risk_requires_runtime_owned_recovery_and_postcheck(self) -> None:
        staged = SafetyEvidence(
            impact="high",
            reversibility="staged_rollback",
            authoritative=True,
            rollback_verified=True,
            postcheck=True,
        )
        transactional = SafetyEvidence(
            impact="high",
            reversibility="transactional",
            authoritative=True,
            postcheck=True,
        )
        self.assertEqual(decide_approval("bypass", "local", staged), AUTO)
        self.assertEqual(decide_approval("bypass", "local", transactional), AUTO)
        self.assertEqual(
            decide_approval(
                "bypass",
                "local",
                SafetyEvidence(
                    impact="high",
                    reversibility="staged_rollback",
                    authoritative=True,
                    rollback_verified=True,
                    postcheck=False,
                ),
            ),
            ASK,
        )

    def test_backup_path_requires_checksum_restore_verification_and_postcheck(self) -> None:
        incomplete = SafetyEvidence(
            impact="high",
            reversibility="verified_backup",
            authoritative=True,
            backup_verified=True,
            postcheck=True,
        )
        complete = SafetyEvidence(
            impact="high",
            reversibility="verified_backup",
            authoritative=True,
            backup_verified=True,
            restore_verified=True,
            postcheck=True,
        )
        self.assertEqual(decide_approval("bypass", "local", incomplete), ASK)
        self.assertEqual(decide_approval("bypass", "local", complete), AUTO)

    def test_tool_classification_does_not_trust_model_claims(self) -> None:
        edit = evidence_for_tool_call("edit_file", {"path": "a", "content": "b"})
        delete = evidence_for_tool_call("run_bash", {"command": "rm important.db"})
        normal = evidence_for_tool_call("run_bash", {"command": "npm install"})
        forged = evidence_for_tool_call(
            "run_bash",
            {
                "command": "rm important.db",
                "backup_verified": True,
                "restore_verified": True,
            },
        )
        self.assertEqual(decide_approval("accept_edits", "local", edit), AUTO)
        self.assertEqual(decide_approval("bypass", "local", normal), AUTO)
        self.assertEqual(decide_approval("bypass", "local", delete), ASK)
        self.assertEqual(forged, delete)

    def test_it_admin_destructive_commands_are_high_impact(self) -> None:
        commands = (
            "sudo -n systemctl restart sshd",
            "Restart-Computer -Force",
            "wipefs -a /dev/sdb",
            "kubectl delete namespace production",
            "python manage.py migrate",
            "powershell -NoProfile -Command Remove-Item C:\\data -Recurse",
            "sed -i s/x/y/ /etc/ssh/sshd_config",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertTrue(shell_command_is_high_impact(command))

    def test_typed_itops_targets_have_server_owned_safety_profiles(self) -> None:
        restart = evidence_for_tool_call(
            "itops_change_apply", {"target_id": "ai-server-netdata"}
        )
        config = evidence_for_tool_call(
            "itops_change_apply", {"target_id": "ai-server-netdata-config"}
        )
        database = evidence_for_tool_call(
            "itops_change_apply", {"target_id": "phase6-sqlite-canary"}
        )
        unknown = evidence_for_tool_call(
            "itops_change_apply", {"target_id": "model-invented-target"}
        )
        self.assertEqual(decide_approval("bypass", "local", restart), AUTO)
        self.assertEqual(decide_approval("bypass", "local", config), AUTO)
        self.assertEqual(decide_approval("bypass", "local", database), AUTO)
        self.assertEqual(decide_approval("bypass", "local", unknown), ASK)

    def test_remote_change_tool_is_discoverable_before_approval(self) -> None:
        self.assertIn("itops_change_apply", SEARCH_ACTIVATABLE_SIDE_EFFECT)


if __name__ == "__main__":
    unittest.main()
