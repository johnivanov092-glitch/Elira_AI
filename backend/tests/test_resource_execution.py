"""R2 — execution targets + workload adapters for resource_process.

Covers: the general capability catalog (honest availability, safe device labels,
no secrets/paths), the deterministic routing/selector semantics per target
(inspect/extract_text local_cpu only; transcribe strict targets with NO fallback,
auto local_gpu→server_gpu→local_cpu), the resource_process execution_target enum,
and that a strict local_gpu request never silently reaches the server.
"""
from __future__ import annotations

import sys
import threading
import unittest
import unittest.mock as mock
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.tools import reset_current_run_id, set_current_run_id  # noqa: E402
from app.application.code_agent.tools._resources import tool_resource_process  # noqa: E402
from app.application.media import execution as ex  # noqa: E402
from app.application.media import processing, resource_store as rs, run_binding  # noqa: E402

_OGG = b"OggS\x00\x02 fake-ogg-audio"
_PDF = b"%PDF-1.4 fake bytes\n"


def _reg(*, local_gpu=False, local_cpu=False, server=True,
         gpu_fn=None, cpu_fn=None, server_fn=None) -> ex.AdapterSet:
    """A registry with adapter availability forced for deterministic routing tests.
    A local target is "available" only when BOTH detected AND wired, so a True flag
    auto-wires a dummy transcribe_fn (matching the real detected-AND-wired rule)."""
    if local_gpu and gpu_fn is None:
        gpu_fn = lambda path, filename: "gpu-text"  # noqa: E731
    if local_cpu and cpu_fn is None:
        cpu_fn = lambda path, filename: "cpu-text"  # noqa: E731
    return ex.AdapterSet((
        ex._LocalTranscribeAdapter(
        ex.ExecutionTarget.LOCAL_GPU.value,
        detect_fn=lambda: (local_gpu, "test-gpu", ("faster-whisper",) if local_gpu else ()),
        transcribe_fn=gpu_fn),
        ex.ServerGpuTranscribeAdapter(
        health_fn=lambda: server, transcribe_fn=server_fn),
        ex._LocalTranscribeAdapter(
        ex.ExecutionTarget.LOCAL_CPU.value,
        detect_fn=lambda: (local_cpu, "cpu", ("faster-whisper",) if local_cpu else ()),
        transcribe_fn=cpu_fn),
    ))


class CapabilityCatalogTest(unittest.TestCase):
    def tearDown(self):
        ex.clear_capability_cache()

    def test_catalog_is_honest_and_safe(self):
        cat = {c["target"]: c for c in ex.capability_catalog(_reg(local_gpu=False, local_cpu=False))}
        self.assertEqual(set(cat), {"local_gpu", "server_gpu", "local_cpu"})
        # local GPU with no runtime is unavailable, never faked as available
        self.assertFalse(cat["local_gpu"]["available"])
        # local_cpu is always available for the pure-local ops
        self.assertTrue(cat["local_cpu"]["available"])
        self.assertIn("inspect", cat["local_cpu"]["operations"])
        self.assertIn("extract_text", cat["local_cpu"]["operations"])
        self.assertTrue(cat["server_gpu"]["available"])
        # no secrets/paths/urls anywhere in the projection
        blob = repr(cat)
        for leak in ("http://", "https://", ":\\", "ELIRA_STT_URL", "/root", "token"):
            self.assertNotIn(leak, blob)

    def test_local_gpu_available_when_runtime_present(self):
        cat = {c["target"]: c for c in ex.capability_catalog(_reg(local_gpu=True))}
        self.assertTrue(cat["local_gpu"]["available"])
        self.assertEqual(cat["local_gpu"]["operations"], ["transcribe"])

    def test_detected_but_unwired_local_gpu_is_unavailable_and_auto_falls_through(self):
        # The R2 default registry has NO local transcribe_fn wired. Even if the GPU
        # + faster-whisper are DETECTED, local_gpu must be reported UNAVAILABLE
        # (honest: capability uses the same detected-AND-wired predicate as run), and
        # auto must fall through to server_gpu — never dead-end on a local target.
        reg = ex.AdapterSet((
            ex._LocalTranscribeAdapter(
                "local_gpu", detect_fn=lambda: (True, "gpu", ("faster-whisper",)), transcribe_fn=None),
            ex.ServerGpuTranscribeAdapter(
                health_fn=lambda: True, transcribe_fn=lambda *a, **k: "srv"),
            ex._LocalTranscribeAdapter(
                "local_cpu", detect_fn=lambda: (False, "cpu", ()), transcribe_fn=None),
        ))
        cat = {c["target"]: c for c in ex.capability_catalog(reg)}
        self.assertFalse(cat["local_gpu"]["available"])       # honest despite detection
        self.assertIn("not yet enabled", " ".join(cat["local_gpu"]["limitations"]))
        self.assertEqual(ex.select("transcribe", "auto", reg).target, "server_gpu")
        # a strict local_gpu request is still an honest local_gpu_unavailable
        self.assertEqual(ex.select("transcribe", "local_gpu", reg).error, "local_gpu_unavailable")

    def test_server_capability_requires_live_health(self):
        down = ex.ServerGpuTranscribeAdapter(health_fn=lambda: False)
        self.assertFalse(down.capability().available)
        broken = ex.ServerGpuTranscribeAdapter(
            health_fn=mock.Mock(side_effect=RuntimeError("SECRET endpoint")))
        self.assertFalse(broken.capability().available)

    def test_default_adapter_set_local_targets_unavailable_in_r2(self):
        # Environment-independent: R2 ships no wired local runtime, so the default
        # registry's local targets are unavailable regardless of host GPU/whisper.
        with mock.patch("app.application.media.execution._server_stt_available",
                        return_value=True):
            cat = {c["target"]: c for c in ex.capability_catalog()}
        self.assertFalse(cat["local_gpu"]["available"])
        self.assertFalse(bool(set(cat["local_cpu"]["operations"]) & {"transcribe"}))


class SelectorSemanticsTest(unittest.TestCase):
    def test_inspect_and_extract_text_are_local_cpu_only(self):
        for op in ("inspect", "extract_text"):
            self.assertTrue(ex.select(op, "auto").local)
            self.assertEqual(ex.select(op, "auto").target, "local_cpu")
            self.assertTrue(ex.select(op, "local_cpu").local)
            for bad in ("local_gpu", "server_gpu"):
                sel = ex.select(op, bad)
                self.assertEqual(sel.error, "target_not_supported_for_operation")
                self.assertEqual(sel.available_targets, ("local_cpu",))

    def test_transcribe_auto_order_local_gpu_first(self):
        # all available → auto picks local_gpu (first in order)
        sel = ex.select("transcribe", "auto", _reg(local_gpu=True, local_cpu=True, server=True))
        self.assertEqual(sel.target, "local_gpu")
        # local_gpu down → auto picks server_gpu (second)
        sel = ex.select("transcribe", "auto", _reg(local_gpu=False, server=True))
        self.assertEqual(sel.target, "server_gpu")
        self.assertEqual(sel.fallback_chain,
                         ({"target": "local_gpu", "reason": "local_gpu_unavailable"},))
        # local_gpu + server down → auto picks local_cpu (third)
        sel = ex.select("transcribe", "auto", _reg(local_gpu=False, server=False, local_cpu=True))
        self.assertEqual(sel.target, "local_cpu")
        self.assertEqual([item["target"] for item in sel.fallback_chain],
                         ["local_gpu", "server_gpu"])
        # nothing available → stable error, not a silent pick
        sel = ex.select("transcribe", "auto", _reg(local_gpu=False, server=False, local_cpu=False))
        self.assertEqual(sel.error, "no_execution_target_available")
        self.assertIsNone(sel.adapter)
        self.assertEqual([item["target"] for item in sel.fallback_chain],
                         ["local_gpu", "server_gpu", "local_cpu"])

    def test_auto_does_not_probe_server_when_local_gpu_is_ready(self):
        server_health = mock.Mock(return_value=True)
        adapters = ex.AdapterSet((
            ex._LocalTranscribeAdapter(
                "local_gpu", detect_fn=lambda: (True, "gpu", ("faster-whisper",)),
                transcribe_fn=lambda path, filename: "gpu"),
            ex.ServerGpuTranscribeAdapter(
                health_fn=server_health, transcribe_fn=lambda *a, **k: "server"),
            ex._LocalTranscribeAdapter(
                "local_cpu", detect_fn=lambda: (True, "cpu", ("faster-whisper",)),
                transcribe_fn=lambda path, filename: "cpu"),
        ))

        sel = ex.select("transcribe", "auto", adapters)

        self.assertEqual(sel.target, "local_gpu")
        server_health.assert_not_called()

    def test_strict_targets_never_fall_back(self):
        # local_gpu strict, unavailable → local_gpu_unavailable, no server pick
        sel = ex.select("transcribe", "local_gpu", _reg(local_gpu=False, server=True))
        self.assertEqual(sel.error, "local_gpu_unavailable")
        self.assertIsNone(sel.adapter)
        # local_cpu strict, unavailable → local_cpu_unavailable, no server pick
        sel = ex.select("transcribe", "local_cpu", _reg(local_cpu=False, server=True))
        self.assertEqual(sel.error, "local_cpu_unavailable")
        # server_gpu strict, available → server_gpu adapter
        sel = ex.select("transcribe", "server_gpu", _reg(server=True))
        self.assertEqual(sel.target, "server_gpu")
        self.assertIsNotNone(sel.adapter)

    def test_invalid_target(self):
        self.assertEqual(ex.select("transcribe", "cloud").error, "invalid_execution_target")
        self.assertFalse(ex.valid_execution_target("gpu"))
        self.assertTrue(all(ex.valid_execution_target(t) for t in
                            ("auto", "local_gpu", "local_cpu", "server_gpu")))


class EndToEndRoutingTest(unittest.TestCase):
    def setUp(self):
        self.rid_run = "run-r2-e2e"
        run_binding.clear_run(self.rid_run)
        self._tok = set_current_run_id(self.rid_run)
        ex.clear_capability_cache()

    def tearDown(self):
        reset_current_run_id(self._tok)
        run_binding.clear_run(self.rid_run)
        ex.clear_capability_cache()

    def _bound(self, name, data, ctype):
        rec = rs.register_resource(original_name=name, content_type=ctype,
                                   owner_session="s1", data=data)
        run_binding.bind_resources(self.rid_run, [rec.resource_id])
        return rec

    def test_local_gpu_strict_unavailable_never_hits_server(self):
        rec = self._bound("v.ogg", _OGG, "audio/ogg")
        server = mock.Mock()
        server_health = mock.Mock(return_value=True)
        with mock.patch("app.application.media.execution._server_stt_available", server_health), \
                mock.patch("app.application.voice.runtime.transcribe", server):
            out = tool_resource_process(resource_id=rec.resource_id, operation="transcribe",
                                        execution_target="local_gpu")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "local_gpu_unavailable")
        self.assertEqual(out["requested_target"], "local_gpu")
        self.assertIsNone(out["selected_target"])
        self.assertIsNone(out["backend"])
        self.assertNotIn("fallback_chain", out)
        server_health.assert_not_called()     # strict local means no server probe either
        server.assert_not_called()          # the file NEVER went to the server

    def test_auto_transcribe_uses_server_and_reports_target(self):
        rec = self._bound("v.mp4", b"\x00\x00\x00\x18ftypmp42" + b"\xff" * 20, "video/mp4")
        with mock.patch("app.application.media.execution._server_stt_available", return_value=True), \
                mock.patch("app.application.voice.runtime.transcribe", return_value="привет") as stt:
            out = tool_resource_process(resource_id=rec.resource_id, operation="transcribe",
                                        execution_target="auto")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["execution_target"], "server_gpu")
        self.assertEqual(out["requested_target"], "auto")
        self.assertEqual(out["selected_target"], "server_gpu")
        self.assertEqual(out["backend"], "server-stt")
        self.assertEqual(out["fallback_chain"],
                         [{"target": "local_gpu", "reason": "local_gpu_unavailable"}])
        self.assertEqual(out["text"], "привет")
        self.assertEqual(stt.call_args.kwargs.get("timeout"), 3600)

    def test_auto_falls_through_runtime_failure_in_order(self):
        rec = self._bound("v.ogg", _OGG, "audio/ogg")
        gpu = mock.Mock(side_effect=RuntimeError("gpu failed"))
        server = mock.Mock(return_value="server recovered")
        cpu = mock.Mock(return_value="cpu should not run")
        reg = _reg(local_gpu=True, local_cpu=True, server=True,
                   gpu_fn=gpu, server_fn=server, cpu_fn=cpu)

        out = processing.process_resource(rec, "transcribe", "auto", adapters=reg)

        self.assertTrue(out["ok"], out)
        self.assertEqual(out["selected_target"], "server_gpu")
        self.assertEqual(out["backend"], "server-stt")
        self.assertEqual(out["fallback_chain"], [{
            "target": "local_gpu",
            "backend": "faster-whisper",
            "reason": "transcription_failed",
        }])
        gpu.assert_called_once()
        server.assert_called_once()
        cpu.assert_not_called()

    def test_auto_reaches_local_cpu_after_unavailable_gpu_and_server_failure(self):
        rec = self._bound("v.ogg", _OGG, "audio/ogg")
        server = mock.Mock(side_effect=RuntimeError("server failed"))
        cpu = mock.Mock(return_value="cpu recovered")
        reg = _reg(local_gpu=False, local_cpu=True, server=True,
                   server_fn=server, cpu_fn=cpu)

        out = processing.process_resource(rec, "transcribe", "auto", adapters=reg)

        self.assertTrue(out["ok"], out)
        self.assertEqual(out["selected_target"], "local_cpu")
        self.assertEqual([item["target"] for item in out["fallback_chain"]],
                         ["local_gpu", "server_gpu"])
        self.assertEqual(out["fallback_chain"][1]["reason"], "transcription_failed")
        server.assert_called_once()
        cpu.assert_called_once()

    def test_strict_runtime_failure_does_not_fall_back(self):
        rec = self._bound("v.ogg", _OGG, "audio/ogg")
        gpu = mock.Mock(side_effect=RuntimeError("gpu failed"))
        server = mock.Mock(return_value="must not run")
        reg = _reg(local_gpu=True, server=True, gpu_fn=gpu, server_fn=server)

        out = processing.process_resource(rec, "transcribe", "local_gpu", adapters=reg)

        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "transcription_failed")
        self.assertEqual(out["selected_target"], "local_gpu")
        self.assertNotIn("fallback_chain", out)
        server.assert_not_called()

    def test_local_gpu_runs_via_injected_registry(self):
        # Prove the framework routes to and runs a local adapter when available.
        rec = self._bound("v.ogg", _OGG, "audio/ogg")
        reg = _reg(local_gpu=True, gpu_fn=lambda path, filename: "локальная расшифровка")
        out = processing.process_resource(rec, "transcribe", "local_gpu", adapters=reg)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["execution_target"], "local_gpu")
        self.assertEqual(out["requested_target"], "local_gpu")
        self.assertEqual(out["selected_target"], "local_gpu")
        self.assertEqual(out["backend"], "faster-whisper")
        self.assertNotIn("fallback_chain", out)
        self.assertEqual(out["text"], "локальная расшифровка")

    def test_local_gpu_adapter_never_receives_a_url_or_argv(self):
        # The adapter is handed the internal path only (positional), never a URL,
        # hostname, argv, or model path from the model.
        rec = self._bound("v.ogg", _OGG, "audio/ogg")
        seen = {}

        def fake_gpu(path, filename):
            seen["path"] = path
            seen["filename"] = filename
            return "ok"
        reg = _reg(local_gpu=True, gpu_fn=fake_gpu)
        processing.process_resource(rec, "transcribe", "local_gpu", adapters=reg)
        self.assertEqual(seen["path"], rec.storage_path)     # internal path, runtime-only
        self.assertEqual(seen["filename"], rec.original_name)

    def test_non_audio_is_unsupported_before_routing(self):
        rec = self._bound("doc.pdf", _PDF, "application/pdf")
        out = tool_resource_process(resource_id=rec.resource_id, operation="transcribe",
                                    execution_target="local_gpu")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "unsupported_for_kind")

    def test_inspect_reports_local_cpu_and_rejects_gpu_target(self):
        rec = self._bound("doc.pdf", _PDF, "application/pdf")
        ok = tool_resource_process(resource_id=rec.resource_id, operation="inspect",
                                   execution_target="auto")
        self.assertTrue(ok["ok"])
        self.assertEqual(ok["execution_target"], "local_cpu")
        self.assertEqual(ok["requested_target"], "auto")
        self.assertEqual(ok["selected_target"], "local_cpu")
        self.assertEqual(ok["backend"], "resource-inspect")
        self.assertEqual(ok["fallback_chain"], [])
        bad = tool_resource_process(resource_id=rec.resource_id, operation="inspect",
                                    execution_target="local_gpu")
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["error"], "target_not_supported_for_operation")
        self.assertEqual(bad["requested_target"], "local_gpu")
        self.assertIsNone(bad["selected_target"])

    def test_invalid_execution_target_rejected_at_tool(self):
        rec = self._bound("v.ogg", _OGG, "audio/ogg")
        out = tool_resource_process(resource_id=rec.resource_id, operation="transcribe",
                                    execution_target="cloud")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "invalid_execution_target")
        self.assertIsNone(out["requested_target"])
        self.assertIsNone(out["selected_target"])
        self.assertIsNone(out["backend"])
        self.assertNotIn("cloud", str(out))

    def test_extra_model_execution_arguments_are_rejected_without_echo(self):
        rec = self._bound("v.ogg", _OGG, "audio/ogg")
        out = tool_resource_process(
            resource_id=rec.resource_id,
            operation="transcribe",
            execution_target="local_gpu",
            path="C:/SECRET/audio.ogg",
            url="http://attacker.invalid",
            argv=["--model", "SECRET"],
        )
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "unsupported_arguments")
        self.assertNotIn("SECRET", str(out))
        self.assertNotIn("attacker", str(out))


class AdapterHonestyTest(unittest.TestCase):
    def tearDown(self):
        ex.clear_capability_cache()

    def test_server_adapter_failure_is_stable_and_leakfree(self):
        rec = rs.register_resource(original_name="v.ogg", content_type="audio/ogg",
                                   owner_session="s1", data=_OGG)
        adapter = ex.ServerGpuTranscribeAdapter(
            health_fn=lambda: True,
            transcribe_fn=mock.Mock(side_effect=RuntimeError("SECRET http://10.0.0.1:8006 boom")))
        out = adapter.run(rec)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "transcription_failed")
        self.assertNotIn("SECRET", str(out))
        self.assertNotIn("10.0.0.1", str(out))

    def test_local_adapter_unavailable_is_stable(self):
        rec = rs.register_resource(original_name="v.ogg", content_type="audio/ogg",
                                   owner_session="s1", data=_OGG)
        adapter = ex._LocalTranscribeAdapter(
            "local_gpu", detect_fn=lambda: (False, "gpu", ()), transcribe_fn=None)
        out = adapter.run(rec)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "local_gpu_unavailable")

    def test_probe_helpers_never_raise(self):
        # detection helpers must be best-effort and bounded even if the tools are absent
        self.assertIn(ex._detect_local_gpu_transcribe()[0], (True, False))
        self.assertIn(ex._detect_local_cpu_transcribe()[0], (True, False))
        ex.clear_capability_cache()
        with mock.patch("app.application.voice.runtime.stt_status",
                        side_effect=RuntimeError("SECRET endpoint")):
            self.assertFalse(ex._server_stt_available())

    def test_generic_main_binary_does_not_advertise_whisper_cpp(self):
        ex.clear_capability_cache()
        with mock.patch("app.application.media.execution._has_module", return_value=False), \
                mock.patch("app.application.media.execution.shutil.which",
                           side_effect=lambda name: "C:/bin/main.exe" if name == "main" else None):
            available, _, runtimes = ex._detect_local_cpu_transcribe()
        self.assertFalse(available)
        self.assertEqual(runtimes, ())

    def test_local_runtime_probe_is_ttl_cached(self):
        ex.clear_capability_cache()
        module_probe = mock.Mock(return_value=False)
        binary_probe = mock.Mock(return_value=None)
        with mock.patch("app.application.media.execution._has_module", module_probe), \
                mock.patch("app.application.media.execution.shutil.which", binary_probe):
            ex._detect_local_cpu_transcribe()
            ex._detect_local_cpu_transcribe()
        self.assertEqual(module_probe.call_count, 1)
        self.assertEqual(binary_probe.call_count, 2)  # two fixed names, one producer run

    def test_nvidia_probe_reads_only_bounded_output(self):
        ex.clear_capability_cache()

        def fake_run(*args, **kwargs):
            kwargs["stdout"].write((b"A" * (ex._MAX_PROBE_BYTES * 4)) + b"\nSECRET")
            kwargs["stdout"].flush()
            return SimpleNamespace(returncode=0)

        with mock.patch("app.application.media.execution.shutil.which",
                        return_value="C:/fixed/nvidia-smi.exe"), \
                mock.patch("app.application.media.execution.subprocess.run",
                           side_effect=fake_run):
            label = ex._detect_nvidia_gpu()
        self.assertEqual(len(label or ""), ex._MAX_DEVICE_LABEL)
        self.assertNotIn("SECRET", label or "")

    def test_capability_cache_is_single_flight_and_ttl_bounded(self):
        calls = 0
        calls_lock = threading.Lock()

        def producer():
            nonlocal calls
            with calls_lock:
                calls += 1
            return "ready"

        results: list[str] = []
        threads = [threading.Thread(target=lambda: results.append(ex._cached("probe", producer)))
                   for _ in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(results, ["ready"] * 12)
        self.assertEqual(calls, 1)

        with mock.patch.object(ex, "_CACHE_TTL_SECONDS", 0):
            self.assertEqual(ex._cached("probe", producer), "ready")
        self.assertEqual(calls, 2)


class ToolSurfaceTest(unittest.TestCase):
    def test_schema_declares_execution_target_enum(self):
        from app.application.code_agent.tool_schemas import build_tool_schemas
        spec = next(s for s in build_tool_schemas()
                    if s["function"]["name"] == "resource_process")
        props = spec["function"]["parameters"]["properties"]
        self.assertIn("execution_target", props)
        self.assertEqual(set(props["execution_target"]["enum"]),
                         {"auto", "local_gpu", "local_cpu", "server_gpu"})
        # execution_target stays OPTIONAL (default auto) — only id+operation required
        self.assertEqual(set(spec["function"]["parameters"]["required"]),
                          {"resource_id", "operation"})
        self.assertIs(spec["function"]["parameters"]["additionalProperties"], False)

    def test_tool_search_finds_local_gpu_phrasing(self):
        from app.application.tool_registry.runtime import search_tool_specs, seed_builtin_tools
        seed_builtin_tools()
        # token-OR substring search: use base forms that appear in the RU/EN terms.
        for query in ("local gpu transcribe", "локальное железо",
                      "вычислительная цель", "обработать локально"):
            names = [m.get("name") for m in search_tool_specs(query, limit=20)]
            self.assertIn("resource_process", names, f"query miss: {query}")


if __name__ == "__main__":
    unittest.main()
