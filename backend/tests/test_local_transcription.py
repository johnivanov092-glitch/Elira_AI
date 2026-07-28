"""R3 — real local GPU/CPU transcription runtime behind resource_process.

Behavior tests only (dependency-injected fakes — NO real GPU, no faster-whisper,
no model download): capability honesty (detected AND wired AND CUDA-visible),
lazy + thread-safe + bounded single-flight model cache, strict local_gpu never
touches the server, auto fallback after a runtime failure, one workload for all
containers, server-owned config allowlist (no model-controlled path/device/
compute/URL/argv), stable leak-free errors, bounded output, and the preserved
R1/R2 timeouts.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import types
import unittest
import unittest.mock as mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.media import execution as ex  # noqa: E402
from app.application.media import local_transcription as lt  # noqa: E402
from app.application.media import processing, resource_store as rs  # noqa: E402

_OGG = b"OggS\x00\x02 fake-ogg-audio"


def _rec(name="v.ogg", ctype="audio/ogg", data=_OGG):
    return rs.register_resource(original_name=name, content_type=ctype,
                                owner_session="s1", data=data)


class _Seg:
    def __init__(self, text):
        self.text = text


class _FakeModel:
    def __init__(self, text="привет мир", segments=None):
        self._text = text
        self._segments = segments

    def transcribe(self, path, **kw):
        segs = self._segments if self._segments is not None else [_Seg(self._text)]
        return (iter(segs), {"language": "ru"})


def _local_adapter(target, *, detected=True, device="test-gpu", transcribe_fn=None):
    return ex._LocalTranscribeAdapter(
        target, detect_fn=lambda: (detected, device, ("faster-whisper",) if detected else ()),
        transcribe_fn=transcribe_fn)


def _adapters(*, gpu=None, server=None, cpu=None):
    gpu = gpu if gpu is not None else _local_adapter("local_gpu", detected=False)
    cpu = cpu if cpu is not None else _local_adapter("local_cpu", detected=False)
    server = server if server is not None else ex.ServerGpuTranscribeAdapter(
        health_fn=lambda: True, transcribe_fn=lambda *a, **k: "server-text")
    return ex.AdapterSet((gpu, server, cpu))


class ProbeHonestyTest(unittest.TestCase):
    def setUp(self):
        ex.clear_capability_cache()
        with lt._CUDA_STATE_LOCK:
            lt._CUDA_DIRECTORY_HANDLES.clear()
            lt._CUDA_DIRECTORY_PATHS.clear()
            lt._CUDA_LIBRARY_HANDLES.clear()

    def tearDown(self):
        ex.clear_capability_cache()
        sys.modules.pop("ctranslate2", None)
        sys.modules.pop("faster_whisper", None)
        with lt._CUDA_STATE_LOCK:
            lt._CUDA_DIRECTORY_HANDLES.clear()
            lt._CUDA_DIRECTORY_PATHS.clear()
            lt._CUDA_LIBRARY_HANDLES.clear()

    def _fake_ct2(self, cuda_count):
        mod = types.ModuleType("ctranslate2")
        mod.get_cuda_device_count = lambda: cuda_count
        mod.get_supported_compute_types = lambda device: {
            "cuda": {"int8_float16", "float16", "int8"},
            "cpu": {"int8", "int8_float32", "float32"},
        }[device]
        sys.modules["ctranslate2"] = mod
        sys.modules["faster_whisper"] = types.ModuleType("faster_whisper")
        return mod

    def test_gpu_capability_false_without_runtime(self):
        with mock.patch.object(lt, "_has_module", return_value=False), \
                mock.patch.object(ex, "_detect_nvidia_gpu", return_value="FakeGPU"):
            self.assertFalse(lt.gpu_runtime_ready()[0])

    def test_gpu_capability_false_when_ctranslate2_sees_no_cuda(self):
        self._fake_ct2(0)
        with mock.patch.object(lt, "_has_module", return_value=True), \
                mock.patch.object(lt, "_ensure_cuda_dll_path"), \
                mock.patch.object(lt, "_cuda_runtime_libraries_ready", return_value=True), \
                mock.patch.object(lt, "_cache_dir_usable", return_value=True), \
                mock.patch.object(ex, "_detect_nvidia_gpu", return_value="FakeGPU"):
            self.assertFalse(lt.gpu_runtime_ready()[0])

    def test_gpu_capability_false_when_cuda_libraries_are_missing(self):
        self._fake_ct2(1)
        with mock.patch.object(lt, "_has_module", return_value=True), \
                mock.patch.object(lt, "_ensure_cuda_dll_path"), \
                mock.patch.object(lt, "_cuda_runtime_libraries_ready", return_value=False), \
                mock.patch.object(lt, "_cache_dir_usable", return_value=True), \
                mock.patch.object(ex, "_detect_nvidia_gpu", return_value="FakeGPU"):
            self.assertFalse(lt.gpu_runtime_ready()[0])

    def test_gpu_capability_false_when_compute_type_is_unsupported(self):
        runtime = self._fake_ct2(1)
        runtime.get_supported_compute_types = lambda device: {"float32"}
        with mock.patch.object(lt, "_has_module", return_value=True), \
                mock.patch.object(lt, "_ensure_cuda_dll_path"), \
                mock.patch.object(lt, "_cuda_runtime_libraries_ready", return_value=True), \
                mock.patch.object(lt, "_cache_dir_usable", return_value=True), \
                mock.patch.object(ex, "_detect_nvidia_gpu", return_value="FakeGPU"):
            self.assertFalse(lt.gpu_runtime_ready()[0])

    def test_gpu_capability_true_when_prerequisites_are_ready(self):
        self._fake_ct2(1)
        with mock.patch.object(lt, "_has_module", return_value=True), \
                mock.patch.object(lt, "_ensure_cuda_dll_path"), \
                mock.patch.object(lt, "_cuda_runtime_libraries_ready", return_value=True), \
                mock.patch.object(lt, "_cache_dir_usable", return_value=True), \
                mock.patch.object(ex, "_detect_nvidia_gpu", return_value="FakeGPU"):
            ready, device, runtimes = lt.gpu_runtime_ready()
        self.assertTrue(ready)
        self.assertEqual(device, "FakeGPU")
        self.assertIn("ctranslate2", runtimes)

    def test_detected_but_unwired_is_unavailable(self):
        # runtime detected but no transcribe_fn wired → adapter unavailable
        adapter = _local_adapter("local_gpu", detected=True, transcribe_fn=None)
        cap = adapter.capability()
        self.assertFalse(cap.available)
        self.assertIn("not yet enabled", " ".join(cap.limitations))

    def test_available_only_when_detected_and_wired(self):
        adapter = _local_adapter("local_gpu", detected=True, transcribe_fn=lambda *a, **k: "x")
        self.assertTrue(adapter.capability().available)

    def test_probe_does_not_load_a_model(self):
        self._fake_ct2(1)
        factory = mock.Mock()
        lt.set_model_factory(factory)
        try:
            with mock.patch.object(lt, "_has_module", return_value=True), \
                    mock.patch.object(lt, "_ensure_cuda_dll_path"), \
                    mock.patch.object(lt, "_cuda_runtime_libraries_ready", return_value=True), \
                    mock.patch.object(lt, "_cache_dir_usable", return_value=True), \
                    mock.patch.object(ex, "_detect_nvidia_gpu", return_value="FakeGPU"):
                lt.gpu_runtime_ready()
        finally:
            lt.set_model_factory(None)
        factory.assert_not_called()

    def test_loaded_cuda_library_handles_are_retained(self):
        handles = [object() for _ in range(9)]
        with mock.patch.object(lt, "_load_cuda_library", side_effect=handles) as load:
            self.assertTrue(lt._cuda_runtime_libraries_ready())
            self.assertEqual(
                [call.args[0] for call in load.call_args_list],
                (
                    [
                        "cublas64_12.dll",
                        "cudnn64_9.dll",
                        "cudnn_ops64_9.dll",
                        "cudnn_graph64_9.dll",
                        "cudnn_engines_precompiled64_9.dll",
                        "cudnn_engines_runtime_compiled64_9.dll",
                        "cudnn_heuristic64_9.dll",
                        "cudnn_adv64_9.dll",
                        "cudnn_cnn64_9.dll",
                    ]
                    if os.name == "nt"
                    else [
                        "libcublas.so.12",
                        "libcudnn.so.9",
                        "libcudnn_ops.so.9",
                        "libcudnn_graph.so.9",
                        "libcudnn_engines_precompiled.so.9",
                        "libcudnn_engines_runtime_compiled.so.9",
                        "libcudnn_heuristic.so.9",
                        "libcudnn_adv.so.9",
                        "libcudnn_cnn.so.9",
                    ]
                ),
            )
        with lt._CUDA_STATE_LOCK:
            self.assertEqual(list(lt._CUDA_LIBRARY_HANDLES.values()), handles)

    @unittest.skipUnless(os.name == "nt", "Windows DLL search-path behavior")
    def test_cuda_dll_directory_handle_is_retained(self):
        with tempfile.TemporaryDirectory() as root:
            package_dir = Path(root) / "cublas"
            (package_dir / "bin").mkdir(parents=True)
            spec = types.SimpleNamespace(submodule_search_locations=[str(package_dir)])
            handle = object()
            with mock.patch("importlib.util.find_spec", return_value=spec), \
                    mock.patch.object(lt.os, "add_dll_directory", return_value=handle):
                lt._ensure_cuda_dll_path()
        with lt._CUDA_STATE_LOCK:
            self.assertEqual(lt._CUDA_DIRECTORY_HANDLES, [handle])


class ModelCacheTest(unittest.TestCase):
    def setUp(self):
        lt.clear_model_cache()

    def tearDown(self):
        lt.set_model_factory(None)
        lt.clear_model_cache()

    def test_model_loaded_lazily_only_on_transcribe(self):
        loads = []
        lt.set_model_factory(lambda m, d, c, cache: (loads.append((m, d, c)), _FakeModel())[1])
        self.assertEqual(loads, [])                       # nothing loaded yet
        lt.transcribe_local("/x/a.ogg", target="local_cpu")
        self.assertEqual(len(loads), 1)

    def test_repeat_call_reuses_bounded_cache(self):
        loads = []
        lt.set_model_factory(lambda m, d, c, cache: (loads.append(1), _FakeModel())[1])
        lt.transcribe_local("/x/a.ogg", target="local_cpu")
        lt.transcribe_local("/x/b.ogg", target="local_cpu")
        self.assertEqual(len(loads), 1)                   # one instance reused

    def test_concurrent_load_creates_single_instance(self):
        loads = []
        barrier = threading.Barrier(6)

        def factory(m, d, c, cache):
            loads.append(1)
            time.sleep(0.05)                              # widen the race window
            return _FakeModel()
        lt.set_model_factory(factory)

        def worker():
            barrier.wait()
            lt.transcribe_local("/x/a.ogg", target="local_gpu")
        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(loads), 1)                   # single-flight

    def test_bounded_cache_evicts(self):
        cache_sizes_at_load = []

        def factory(m, d, c, cache):
            with lt._MODEL_CACHE_LOCK:
                cache_sizes_at_load.append(len(lt._MODELS))
            return _FakeModel()

        lt.set_model_factory(factory)
        import os
        with mock.patch.dict(os.environ, {}, clear=False):
            for model in ("tiny", "base", "small"):
                os.environ["ELIRA_LOCAL_STT_MODEL"] = model
                lt.transcribe_local("/x/a.ogg", target="local_cpu")
            os.environ.pop("ELIRA_LOCAL_STT_MODEL", None)
        with lt._MODEL_CACHE_LOCK:
            self.assertLessEqual(len(lt._MODELS), lt._MAX_MODELS)
        self.assertEqual(cache_sizes_at_load, [0, 1, 1])

    def test_load_failure_is_stable_and_uncached(self):
        lt.set_model_factory(mock.Mock(side_effect=RuntimeError("SECRET C:/models/x cuda oom")))
        with self.assertRaises(lt.LocalTranscriptionError) as c:
            lt.transcribe_local("/x/a.ogg", target="local_gpu")
        self.assertEqual(c.exception.code, "model_load_failed")
        self.assertNotIn("SECRET", str(c.exception))
        self.assertNotIn("C:/models", str(c.exception))
        with lt._MODEL_CACHE_LOCK:
            self.assertEqual(len(lt._MODELS), 0)          # broken model not cached

    def test_output_is_bounded(self):
        long_segments = [_Seg("абв " * 20) for _ in range(5000)]
        lt.set_model_factory(lambda m, d, c, cache: _FakeModel(segments=long_segments))
        text = lt.transcribe_local("/x/a.ogg", target="local_cpu")
        self.assertLessEqual(len(text), 20000)

    def test_output_iteration_stops_at_the_public_cap(self):
        consumed = 0

        def segments():
            nonlocal consumed
            while True:
                consumed += 1
                yield _Seg("x" * 100)

        self.assertEqual(len(lt._extract_text(segments())), lt._MAX_TEXT_CHARS)
        self.assertEqual(consumed, lt._MAX_TEXT_CHARS // 100)

    def test_different_model_loads_are_serialized_and_cache_stays_bounded(self):
        active = 0
        peak = 0
        state_lock = threading.Lock()
        start = threading.Barrier(4)

        def factory(model, device, compute, cache):
            nonlocal active, peak
            with state_lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.02)
            with state_lock:
                active -= 1
            return _FakeModel(model)

        lt.set_model_factory(factory)

        def worker(model):
            start.wait()
            lt._get_model(model, "cpu", "int8", "C:/cache")

        threads = [threading.Thread(target=worker, args=(model,))
                   for model in ("tiny", "base", "small", "medium")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(peak, 1)
        with lt._MODEL_CACHE_LOCK:
            self.assertLessEqual(len(lt._MODELS), lt._MAX_MODELS)

    def test_busy_runtime_fails_with_stable_error(self):
        self.assertTrue(lt._RUNTIME_LOCK.acquire(timeout=0))
        try:
            with mock.patch.object(lt, "_RUNTIME_WAIT_SECONDS", 0):
                with self.assertRaises(lt.LocalTranscriptionError) as caught:
                    lt.transcribe_local("/x/a.ogg", target="local_gpu")
            self.assertEqual(caught.exception.code, "local_runtime_busy")
        finally:
            lt._RUNTIME_LOCK.release()


class ConfigOwnershipTest(unittest.TestCase):
    def tearDown(self):
        import os
        for key in (lt._MODEL_ENV, lt._COMPUTE_ENV, lt._CACHE_DIR_ENV):
            os.environ.pop(key, None)

    def test_model_allowlist_fail_closed(self):
        import os
        os.environ[lt._MODEL_ENV] = "../evil/repo"
        self.assertEqual(lt._resolve_model(), "large-v3")
        os.environ[lt._MODEL_ENV] = "malicious/HF-repo"
        self.assertEqual(lt._resolve_model(), "large-v3")
        os.environ[lt._MODEL_ENV] = "medium"
        self.assertEqual(lt._resolve_model(), "medium")

    def test_compute_type_allowlist_per_device(self):
        import os
        os.environ[lt._COMPUTE_ENV] = "float64"
        self.assertEqual(lt._resolve_compute_type("cuda"), "int8_float16")
        self.assertEqual(lt._resolve_compute_type("cpu"), "int8")
        os.environ[lt._COMPUTE_ENV] = "float16"
        self.assertEqual(lt._resolve_compute_type("cuda"), "float16")
        self.assertEqual(lt._resolve_compute_type("cpu"), "int8")   # float16 not a CPU option

    def test_device_is_server_owned_not_model_supplied(self):
        # the ONLY inputs are path + target(enum); device derives from target
        seen = {}
        lt.set_model_factory(lambda m, d, c, cache: (seen.update(device=d, compute=c), _FakeModel())[1])
        try:
            lt.transcribe_local("/x/a.ogg", target="local_gpu")
            self.assertEqual(seen["device"], "cuda")
            lt.transcribe_local("/x/a.ogg", target="local_cpu")
            self.assertEqual(seen["device"], "cpu")
            with self.assertRaises(lt.LocalTranscriptionError):
                lt.transcribe_local("/x/a.ogg", target="server_gpu")   # not a local target
        finally:
            lt.set_model_factory(None)
            lt.clear_model_cache()

    def test_cache_dir_rejects_relative_override(self):
        import os
        os.environ[lt._CACHE_DIR_ENV] = "relative/evil"
        resolved = lt._resolve_cache_dir()
        self.assertNotIn("relative", resolved)             # fell back to server default
        self.assertTrue(resolved.endswith("model_cache"))


class RoutingTest(unittest.TestCase):
    """Strict/auto routing with the local runtime wired (via injected adapters)."""

    def tearDown(self):
        lt.clear_model_cache()

    def test_strict_local_gpu_never_calls_server(self):
        server_health = mock.Mock(return_value=True)
        server_tx = mock.Mock(return_value="server-text")
        adapters = _adapters(
            gpu=_local_adapter("local_gpu", detected=True, transcribe_fn=lambda *a, **k: "local-gpu-text"),
            server=ex.ServerGpuTranscribeAdapter(health_fn=server_health, transcribe_fn=server_tx))
        out = processing.process_resource(_rec(), "transcribe", "local_gpu", adapters)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["selected_target"], "local_gpu")
        self.assertEqual(out["text"], "local-gpu-text")
        server_health.assert_not_called()
        server_tx.assert_not_called()

    def test_strict_local_gpu_failure_no_fallback(self):
        server_tx = mock.Mock(return_value="server-text")
        def boom(*a, **k):
            raise RuntimeError("cuda oom /root/model")
        adapters = _adapters(
            gpu=_local_adapter("local_gpu", detected=True, transcribe_fn=boom),
            server=ex.ServerGpuTranscribeAdapter(health_fn=lambda: True, transcribe_fn=server_tx))
        out = processing.process_resource(_rec(), "transcribe", "local_gpu", adapters)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "transcription_failed")
        self.assertEqual(out["selected_target"], "local_gpu")
        server_tx.assert_not_called()
        self.assertNotIn("/root/model", str(out))

    def test_auto_local_gpu_success_does_not_touch_server(self):
        server_health = mock.Mock(return_value=True)
        server_tx = mock.Mock(return_value="server")
        adapters = _adapters(
            gpu=_local_adapter("local_gpu", detected=True, transcribe_fn=lambda *a, **k: "local"),
            server=ex.ServerGpuTranscribeAdapter(health_fn=server_health, transcribe_fn=server_tx))
        out = processing.process_resource(_rec(), "transcribe", "auto", adapters)
        self.assertTrue(out["ok"])
        self.assertEqual(out["selected_target"], "local_gpu")
        server_health.assert_not_called()
        server_tx.assert_not_called()

    def test_auto_local_failure_falls_to_server(self):
        def boom(*a, **k):
            raise RuntimeError("gpu died")
        adapters = _adapters(
            gpu=_local_adapter("local_gpu", detected=True, transcribe_fn=boom),
            server=ex.ServerGpuTranscribeAdapter(health_fn=lambda: True,
                                                 transcribe_fn=lambda *a, **k: "server-text"))
        out = processing.process_resource(_rec(), "transcribe", "auto", adapters)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["selected_target"], "server_gpu")
        steps = {step["target"] for step in out.get("fallback_chain", [])}
        self.assertIn("local_gpu", steps)                  # honest fallback record

    def test_auto_server_failure_falls_to_local_cpu(self):
        adapters = _adapters(
            gpu=_local_adapter("local_gpu", detected=False),
            server=ex.ServerGpuTranscribeAdapter(
                health_fn=lambda: True,
                transcribe_fn=mock.Mock(side_effect=RuntimeError("stt down"))),
            cpu=_local_adapter("local_cpu", detected=True, transcribe_fn=lambda *a, **k: "cpu-text"))
        out = processing.process_resource(_rec(), "transcribe", "auto", adapters)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["selected_target"], "local_cpu")
        self.assertEqual(out["text"], "cpu-text")

    def test_one_adapter_handles_all_containers(self):
        seen_paths = []
        adapters = _adapters(
            gpu=_local_adapter("local_gpu", detected=True,
                               transcribe_fn=lambda path, filename=None: (seen_paths.append(path), "ok")[1]))
        for name, ct in [("a.mp4", "video/mp4"), ("b.ogg", "audio/ogg"),
                         ("c.m4a", "audio/mp4"), ("d.wav", "audio/wav")]:
            rec = _rec(name, ct)
            out = processing.process_resource(rec, "transcribe", "local_gpu", adapters)
            self.assertTrue(out["ok"], out)
            self.assertEqual(seen_paths[-1], rec.storage_path)   # same adapter, internal path
        self.assertEqual(len(seen_paths), 4)

    def test_result_carries_targets_and_no_path(self):
        adapters = _adapters(
            gpu=_local_adapter("local_gpu", detected=True, transcribe_fn=lambda *a, **k: "t"))
        rec = _rec()
        out = processing.process_resource(rec, "transcribe", "local_gpu", adapters)
        self.assertEqual(out["requested_target"], "local_gpu")
        self.assertEqual(out["selected_target"], "local_gpu")
        self.assertEqual(out["backend"], "faster-whisper")
        self.assertNotIn(rec.storage_path, str(out))       # internal path never leaks


class ContractInvariantsTest(unittest.TestCase):
    def test_server_timeout_still_3600(self):
        self.assertEqual(ex._STT_TIMEOUT_SECONDS, 3600)

    def test_tool_timeout_at_least_3630(self):
        from app.application.tool_registry.builtins import build_builtin_tools
        spec = next(s for s in build_builtin_tools() if s["name"] == "resource_process")
        self.assertGreaterEqual(spec["timeout_seconds"], 3630)

    def test_media_import_does_not_eager_import_faster_whisper(self):
        # importing the media package / runtime must not pull the heavy STT deps
        import importlib
        importlib.import_module("app.application.media.local_transcription")
        self.assertNotIn("faster_whisper", sys.modules)    # lazy only

    def test_no_bom_lf_only(self):
        raw = Path(lt.__file__).read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r\n", raw)


if __name__ == "__main__":
    unittest.main()
