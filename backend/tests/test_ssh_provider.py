"""Current SSH provider contract.

Saved hosts are discovery aliases, never an authorization list. Real processes
are replaced by a module-level process runner in these unit tests.
"""
from __future__ import annotations

import base64
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _proc(
    returncode: int = 0,
    stdout: bytes = b"",
    stderr: bytes = b"",
) -> CompletedProcess[bytes]:
    return CompletedProcess(
        args=["ssh"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


class SshProviderTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["ELIRA_DATA_DIR"] = self._tmp.name

        from app.core import data_files

        importlib.reload(data_files)
        from app.application.tool_providers import ssh_acl

        importlib.reload(ssh_acl)
        from app.application.skill_services import ssh as ssh_provider

        self.ssh_acl = ssh_acl
        self.ssh = ssh_provider

    def tearDown(self) -> None:
        self._tmp.cleanup()
        os.environ.pop("ELIRA_DATA_DIR", None)


class SavedHostDiscoveryTest(SshProviderTestBase):
    def test_empty_favorites_do_not_disable_ssh(self) -> None:
        self.assertEqual(self.ssh_acl.get_allowed_hosts(), [])
        self.assertTrue(self.ssh_acl.is_ssh_enabled())

    def test_saved_alias_has_friendly_matching(self) -> None:
        self.ssh_acl.ACL_PATH.write_text('{"allowed_hosts": ["elira-ai-server"]}', encoding="utf-8")
        self.assertEqual(
            self.ssh_acl.resolve_allowed_host("Elira AI Server"),
            "elira-ai-server",
        )

    def test_unsaved_target_is_preserved(self) -> None:
        self.assertEqual(
            self.ssh_acl.resolve_allowed_host("192.168.88.42"),
            "192.168.88.42",
        )


class SshExecutionTest(SshProviderTestBase):
    def test_blocking_powershell_wait_is_redirected_before_ssh_starts(self) -> None:
        script = (
            "$installer = 'C:\\Temp\\jellyfin_setup.exe'; "
            "Start-Process $installer -ArgumentList '/S' -Wait; "
            "Get-Service Jellyfin"
        )

        with patch.object(self.ssh, "run_registered_process") as runner:
            result = self.ssh.tool_ssh_run_ps(
                host="media-server",
                script=script,
            )

        runner.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "needs_background")
        self.assertEqual(result["error"], "long_running_foreground")
        self.assertEqual(result["recommended_tool"], "run_server")
        self.assertEqual(
            result["recommended_arguments"],
            {"action": "start", "kind": "job"},
        )
        self.assertIn('"status": "needs_background"', result["text"])
        self.assertIn('"recommended_tool": "run_server"', result["text"])

    def test_wait_for_exit_is_redirected_from_raw_ssh_command(self) -> None:
        command = (
            "powershell -NoProfile -Command \"$p = Start-Process "
            "jellyfin_setup.exe -PassThru; $p.WaitForExit(300000)\""
        )

        with patch.object(self.ssh, "run_registered_process") as runner:
            result = self.ssh.tool_ssh_run(
                host="media-server",
                command=command,
            )

        runner.assert_not_called()
        self.assertEqual(result["status"], "needs_background")
        self.assertEqual(result["reason"], "powershell_wait_for_exit")

    def test_long_remote_sleep_is_redirected_before_ssh_starts(self) -> None:
        with patch.object(self.ssh, "run_registered_process") as runner:
            result = self.ssh.tool_ssh_run_ps(
                host="media-server",
                script="Start-Sleep -Seconds 45; Get-Service Jellyfin",
            )

        runner.assert_not_called()
        self.assertEqual(result["status"], "needs_background")
        self.assertEqual(result["reason"], "long_sleep")

    def test_service_wait_is_redirected_before_ssh_starts(self) -> None:
        with patch.object(self.ssh, "run_registered_process") as runner:
            result = self.ssh.tool_ssh_run_ps(
                host="media-server",
                script=(
                    "$service = Get-Service Jellyfin; "
                    "$service.WaitForStatus('Running', '00:01:00')"
                ),
            )

        runner.assert_not_called()
        self.assertEqual(result["status"], "needs_background")
        self.assertEqual(result["reason"], "powershell_wait_for_status")

    def test_start_service_is_redirected_before_ssh_starts(self) -> None:
        with patch.object(self.ssh, "run_registered_process") as runner:
            result = self.ssh.tool_ssh_run_ps(
                host="media-server",
                script="Start-Service Jellyfin; Get-Service Jellyfin",
            )

        runner.assert_not_called()
        self.assertEqual(result["status"], "needs_background")
        self.assertEqual(result["reason"], "powershell_service_control")

    def test_short_remote_sleep_remains_a_foreground_command(self) -> None:
        with patch.object(
            self.ssh,
            "run_registered_process",
            return_value=_proc(stdout=b"ready\n"),
        ) as runner:
            result = self.ssh.tool_ssh_run_ps(
                host="media-server",
                script="Start-Sleep -Seconds 2; Write-Output ready",
            )

        runner.assert_called_once()
        self.assertTrue(result["ok"])
        self.assertNotIn("status", result)

    def test_remote_windows_process_tree_cleanup_is_verified(self) -> None:
        with patch.object(
            self.ssh,
            "run_registered_process",
            return_value=_proc(
                stdout=b"SUCCESS\r\n__ELIRA_REMOTE_STOP__=stopped:7312\r\n"
            ),
        ) as runner:
            result = self.ssh.stop_remote_windows_process_tree(
                host="media-server",
                remote_pid=7312,
                remote_started_ticks=638602560000000000,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["remote_pid"], 7312)
        self.assertEqual(result["remote_cleanup_status"], "stopped")
        argv = runner.call_args.args[0]
        self.assertIn("media-server", argv)
        self.assertIn("-EncodedCommand", argv[-1])
        cleanup_payload = argv[-1].rsplit(" ", 1)[-1]
        cleanup_script = base64.b64decode(cleanup_payload).decode("utf-16-le")
        self.assertIn("Get-CimInstance Win32_Process", cleanup_script)
        self.assertIn("$survivors", cleanup_script)
        self.assertIn("$expectedStartTicks=638602560000000000", cleanup_script)

    def test_remote_windows_cleanup_requires_success_marker(self) -> None:
        with patch.object(
            self.ssh,
            "run_registered_process",
            return_value=_proc(stdout=b"taskkill returned without proof\r\n"),
        ):
            result = self.ssh.stop_remote_windows_process_tree(
                host="media-server",
                remote_pid=7312,
                remote_started_ticks=638602560000000000,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["remote_cleanup_status"], "failed")

    def test_remote_windows_cleanup_requires_process_identity(self) -> None:
        with patch.object(self.ssh, "run_registered_process") as runner:
            result = self.ssh.stop_remote_windows_process_tree(
                host="media-server",
                remote_pid=7312,
            )

        runner.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertEqual(result["remote_cleanup_status"], "invalid_identity")

    def test_remote_windows_cleanup_rejects_reused_pid_identity(self) -> None:
        with patch.object(
            self.ssh,
            "run_registered_process",
            return_value=_proc(
                returncode=3,
                stdout=b"__ELIRA_REMOTE_STOP__=identity_mismatch:123\r\n",
            ),
        ):
            result = self.ssh.stop_remote_windows_process_tree(
                host="media-server",
                remote_pid=7312,
                remote_started_ticks=638602560000000000,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["remote_cleanup_status"], "identity_mismatch")

    def test_remote_windows_cleanup_has_a_bounded_timeout(self) -> None:
        with patch.object(
            self.ssh,
            "run_registered_process",
            side_effect=TimeoutExpired(["ssh"], timeout=20),
        ) as runner:
            result = self.ssh.stop_remote_windows_process_tree(
                host="media-server",
                remote_pid=7312,
                remote_started_ticks=638602560000000000,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["remote_cleanup_status"], "timeout")
        self.assertEqual(runner.call_args.kwargs["timeout_seconds"], 20.0)

    def test_arbitrary_direct_target_executes_as_one_argv_item(self) -> None:
        with patch.object(
            self.ssh,
            "run_registered_process",
            return_value=_proc(stdout=b"hello\n"),
        ) as runner:
            result = self.ssh.tool_ssh_run(
                host="192.168.88.42",
                command="echo hello",
            )

        self.assertTrue(result["ok"])
        self.assertIn("hello", result["text"])
        argv = runner.call_args.args[0]
        self.assertEqual(argv[0], "ssh")
        self.assertIn("-n", argv)
        self.assertIn("BatchMode=yes", argv)
        self.assertIn("StrictHostKeyChecking=accept-new", argv)
        self.assertIn("192.168.88.42", argv)
        self.assertIn("echo hello", argv)

    def test_write_forwards_stdin_without_ssh_null_input_flag(self) -> None:
        with patch.object(
            self.ssh,
            "run_registered_process",
            return_value=_proc(),
        ) as runner:
            result = self.ssh.tool_ssh_write(
                host="192.168.88.42",
                path="/tmp/elira.txt",
                content="payload",
            )

        self.assertIn("Wrote", result["text"])
        argv = runner.call_args.args[0]
        self.assertNotIn("-n", argv)
        self.assertEqual(runner.call_args.kwargs["input"], b"payload")

    def test_saved_friendly_name_resolves_before_execution(self) -> None:
        self.ssh_acl.ACL_PATH.write_text('{"allowed_hosts": ["elira-ai-server"]}', encoding="utf-8")
        with patch.object(
            self.ssh,
            "run_registered_process",
            return_value=_proc(stdout=b"ok\n"),
        ) as runner:
            result = self.ssh.tool_ssh_run(
                host="Elira AI Server",
                command="hostname",
            )

        self.assertTrue(result["ok"])
        argv = runner.call_args.args[0]
        self.assertIn("elira-ai-server", argv)
        self.assertNotIn("Elira AI Server", argv)

    def test_only_invalid_destination_tokens_are_rejected(self) -> None:
        for host in ("", "   ", "-oProxyCommand=evil", "host\nother", "host\x00other"):
            with self.subTest(host=host), patch.object(
                self.ssh,
                "run_registered_process",
            ) as runner:
                result = self.ssh.tool_ssh_run(host=host, command="hostname")
                self.assertFalse(result["ok"])
                self.assertIn("ERROR", result["text"])
                runner.assert_not_called()

    def test_nonzero_remote_exit_is_reported(self) -> None:
        with patch.object(
            self.ssh,
            "run_registered_process",
            return_value=_proc(returncode=3, stderr=b"boom"),
        ):
            result = self.ssh.tool_ssh_run(
                host="unmanaged.example",
                command="false",
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["exit_code"], 3)
        self.assertIn("boom", result["text"])

    def test_timeout_argument_limits_only_connection_establishment(self) -> None:
        with patch.object(
            self.ssh,
            "run_registered_process",
            return_value=_proc(stdout=b"done"),
        ) as runner:
            result = self.ssh.tool_ssh_run(
                host="host",
                command="long-job",
                timeout=1,
            )

        self.assertTrue(result["ok"])
        runner.assert_called_once()
        self.assertIn("ConnectTimeout=1", runner.call_args.args[0])


class SshProviderIntegrationTest(SshProviderTestBase):
    def test_provider_auto_backgrounds_blocking_ssh_without_shell_quoting(self) -> None:
        from functools import partial
        dispatch_skill = partial(self.ssh.execute_ssh, Path(self._tmp.name))
        started = {
            "ok": True,
            "status": "running",
            "kind": "job",
            "pid": 4242,
            "remote_pid": 7312,
            "remote_cleanup_supported": True,
            "remote_process_identity_captured": True,
            "remote_cleanup_ready": True,
            "text": "Job started in background.",
        }

        with patch.object(self.ssh, "run_registered_process") as foreground, patch(
            "app.application.code_agent.tools._run.start_background_argv_job",
            return_value=started,
        ) as background:
            result = dispatch_skill(
                "ssh_run_ps",
                {
                    "host": "media-server",
                    "script": "Start-Process jellyfin_setup.exe -Wait",
                },
            )

        foreground.assert_not_called()
        call = background.call_args
        self.assertEqual(call.args[0], Path(self._tmp.name).resolve())
        self.assertEqual(call.kwargs["argv"][0], "ssh")
        self.assertIn("-EncodedCommand", call.kwargs["argv"][-1])
        wrapper_payload = call.kwargs["argv"][-1].rsplit(" ", 1)[-1]
        wrapper = base64.b64decode(wrapper_payload).decode("utf-16-le")
        self.assertIn("__ELIRA_REMOTE_PID__=", wrapper)
        self.assertIn("StartTime.ToUniversalTime().Ticks", wrapper)
        self.assertIn(
            base64.b64encode(
                "Start-Process jellyfin_setup.exe -Wait".encode("utf-16-le")
            ).decode("ascii"),
            wrapper,
        )
        self.assertIn("script:", call.kwargs["display_command"])
        self.assertNotIn("jellyfin_setup", call.kwargs["display_command"])
        self.assertNotIn(call.kwargs["argv"][-1], call.kwargs["display_command"])
        cleanup = call.kwargs["runtime_metadata"]["remote_cleanup"]
        self.assertEqual(cleanup["kind"], "ssh_windows_process_tree")
        self.assertEqual(cleanup["host"], "media-server")
        self.assertTrue(result["ok"])
        self.assertTrue(result["backgrounded"])
        self.assertEqual(result["redirected_from"], "ssh_run_ps")
        self.assertEqual(result["pid"], 4242)
        self.assertEqual(result["remote_pid"], 7312)
        self.assertTrue(result["remote_cleanup_supported"])
        self.assertTrue(result["remote_process_identity_captured"])
        self.assertTrue(result["remote_cleanup_ready"])
        self.assertIn(
            "run_server(action='logs', kind='job', pid=4242)",
            result["text"],
        )
        self.assertIn('"remote_pid": 7312', result["text"])
        self.assertIn('"remote_cleanup_supported": true', result["text"])
        self.assertIn('"remote_process_identity_captured": true', result["text"])
        self.assertIn('"remote_cleanup_ready": true', result["text"])
        self.assertIn('"action": "stop"', result["text"])

    def test_provider_auto_backgrounds_blocking_posix_ssh_without_cleanup(self) -> None:
        from functools import partial
        dispatch_skill = partial(self.ssh.execute_ssh, Path(self._tmp.name))
        with patch(
            "app.application.code_agent.tools._run.start_background_argv_job",
            return_value={
                "ok": True,
                "status": "running",
                "kind": "job",
                "pid": 4242,
                "text": "Job started in background.",
            },
        ) as background:
            result = dispatch_skill(
                "ssh_run",
                {
                    "host": "linux-server",
                    "command": "sleep 45; grep powershell app.log",
                },
            )

        self.assertTrue(result["backgrounded"])
        self.assertFalse(result["remote_cleanup_supported"])
        self.assertIsNone(background.call_args.kwargs["runtime_metadata"])
        self.assertEqual(
            background.call_args.kwargs["argv"][-1],
            "sleep 45; grep powershell app.log",
        )

    def test_raw_powershell_wait_redirects_qwen_to_typed_ssh_tool(self) -> None:
        from functools import partial
        dispatch_skill = partial(self.ssh.execute_ssh, Path(self._tmp.name))
        with patch(
            "app.application.code_agent.tools._run.start_background_argv_job",
        ) as background:
            result = dispatch_skill(
                "ssh_run",
                {
                    "host": "media-server",
                    "command": (
                        "powershell -Command \"$p=Start-Process setup.exe "
                        "-PassThru; $p.WaitForExit(300000)\""
                    ),
                },
            )

        background.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "needs_background")
        self.assertEqual(result["recommended_tool"], "ssh_run_ps")
        self.assertEqual(result["missing_required_arguments"], ["script"])
        self.assertIn('"recommended_tool": "ssh_run_ps"', result["text"])

    def test_scenario_documents_background_redirect_contract(self) -> None:
        text = (ROOT / "skills/linux-admin/scenarios.md").read_text(encoding="utf-8")
        self.assertIn("run_server", text)
        self.assertIn("ssh_run_ps", text)

    def test_skill_keeps_ssh_scenarios_without_native_provider(self) -> None:
        self.assertEqual(set(self.ssh._DISPATCH), {"ssh_run", "ssh_read", "ssh_write", "ssh_run_ps", "ssh_replace", "ssh_port_check", "ssh_list_hosts"})

    def test_registry_does_not_expose_retired_ssh_tools(self) -> None:
        from app.application.tool_providers import build_runtime_tool_registry
        registry = build_runtime_tool_registry(Path(self._tmp.name), mcp_server_ids=())
        self.assertNotIn("ssh_run", registry.known_tools())
        self.assertNotIn("ssh_list_hosts", registry.known_tools())

    def test_unknown_tool_returns_structured_error(self) -> None:
        result = self.ssh.execute_ssh(Path(self._tmp.name), "ssh_telekinesis", {})
        self.assertIn("ERROR", result["text"])


# stderr exactly as PowerShell 5.1 writes it when started over ssh without a
# console (captured 2026-11-10 from `powershell -EncodedCommand`).
_CLIXML_HEAD = '#< CLIXML\r\n<Objs Version="1.1.0.1" xmlns="http://schemas.microsoft.com/powershell/2004/04">'
_CLIXML_PROGRESS = (
    '<Obj S="progress" RefId="0"><TN RefId="0"><T>System.Management.Automation.PSCustomObject</T>'
    '<T>System.Object</T></TN><MS><I64 N="SourceId">1</I64><PR N="Record">'
    '<AV>Подготовка модулей к первому использованию.</AV><AI>0</AI><Nil /><PI>-1</PI><PC>-1</PC>'
    '<T>Completed</T><SR>-1</SR><SD> </SD></PR></MS></Obj>'
)
_CLIXML_ERROR = (
    '<S S="Error">Get-Item : Не удается найти путь "C:\\nope_zz", так как он не существует._x000D__x000A_</S>'
    '<S S="Error">строка:1 знак:1_x000D__x000A_</S>'
    "<S S=\"Error\">+ Get-Item C:\\nope_zz; 'done'_x000D__x000A_</S>"
    '<S S="Error">    + CategoryInfo          : ObjectNotFound: (C:\\nope_zz:String) [Get-Item], '
    'ItemNotFoundException_x000D__x000A_</S>'
    '<S S="Error">    + FullyQualifiedErrorId : PathNotFound,Microsoft.PowerShell.Commands.GetItemCommand'
    '_x000D__x000A_</S><S S="Error"> _x000D__x000A_</S>'
)


def _clixml(*records: str) -> bytes:
    return (_CLIXML_HEAD + "".join(records) + "</Objs>").encode("utf-8")


class RemotePowerShellErrorTest(SshProviderTestBase):
    def _run(self, **process) -> dict:
        with patch.object(self.ssh, "run_registered_process", return_value=_proc(**process)):
            return self.ssh.tool_ssh_run_ps(host="media-server", script="Get-Item C:\\nope_zz; 'done'")

    def test_error_record_with_exit_zero_is_a_failure_with_readable_text(self) -> None:
        result = self._run(stdout=b"done\n", stderr=_clixml(_CLIXML_PROGRESS, _CLIXML_ERROR))

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "powershell_error")
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("ОШИБКИ POWERSHELL:\nGet-Item : Не удается найти путь", result["text"])
        self.assertIn("FullyQualifiedErrorId : PathNotFound", result["text"])
        self.assertIn("exit=0, но в скрипте была ошибка PowerShell", result["text"])
        self.assertIn("STDOUT:\ndone", result["text"])
        for raw in ("CLIXML", "_x000D_", "<S S=", "Подготовка модулей"):
            self.assertNotIn(raw, result["text"])

    def test_progress_noise_alone_is_a_clean_success(self) -> None:
        result = self._run(stdout=b"quiet\n", stderr=_clixml(_CLIXML_PROGRESS))

        self.assertTrue(result["ok"])
        self.assertNotIn("error", result)
        for absent in ("CLIXML", "STDERR", "ОШИБКИ", "Подготовка модулей"):
            self.assertNotIn(absent, result["text"])

    def test_warning_is_shown_and_does_not_fail_the_call(self) -> None:
        result = self._run(stdout=b"ok\n", stderr=_clixml(_CLIXML_PROGRESS, '<S S="warning">careful</S>',
                                                          '<S S="verbose">v</S>'))

        self.assertTrue(result["ok"])
        self.assertIn("ПРЕДУПРЕЖДЕНИЯ POWERSHELL:\ncareful", result["text"])
        self.assertNotIn("ОШИБКИ", result["text"])

    def test_native_program_stderr_is_not_a_powershell_error(self) -> None:
        result = self._run(stdout=b"after native\n", stderr=b"ffmpeg version 7.1\r\n")

        self.assertTrue(result["ok"])
        self.assertIn("STDERR:\nffmpeg version 7.1", result["text"])
        self.assertNotIn("ОШИБКИ", result["text"])

    def test_nonzero_exit_keeps_its_own_failure_and_gets_decoded_text(self) -> None:
        result = self._run(returncode=1, stderr=_clixml(_CLIXML_ERROR))

        self.assertFalse(result["ok"])
        self.assertEqual(result["exit_code"], 1)
        self.assertNotIn("error", result)
        self.assertIn("ОШИБКИ POWERSHELL:\nGet-Item", result["text"])
        self.assertNotIn("exit=0, но", result["text"])

    def test_clixml_escapes_entities_and_a_cut_document_are_decoded(self) -> None:
        errors, warnings, other = self.ssh._powershell_streams(
            "plain line\r\n" + _CLIXML_HEAD
            + '<S S="Error">a &lt; b &amp; file_x005F_name_x000D__x000A_</S><S S="warning">w1</S><S S="warning">w2</S>'
        )

        self.assertEqual(errors, "a < b & file_name")
        self.assertEqual(warnings, "w1\nw2")
        self.assertEqual(other, "plain line")


if __name__ == "__main__":
    unittest.main()
