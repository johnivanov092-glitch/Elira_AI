"""R5C — remote OCR client (resource_remote_process).

Behavior tests for the Elira-side tool that sends a run-bound resource to the
env-configured remote OCR worker, verifies the result, registers the recognized
text as a NEW run-bound resource, and returns only a bounded projection. Covers
the binding gate, the size gate (zero read / zero HTTP), TLS/auth transport
rules, capability intersection, response validation, verify/cleanup, approval +
tool_search wiring, and non-regression of resource_process / deferred upload.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.tools import (  # noqa: E402
    reset_current_run_id, set_current_run_id,
)
from app.application.code_agent.tools._dispatch import build_tool_dispatch  # noqa: E402
from app.application.code_agent.tools._resources import (  # noqa: E402
    tool_resource_process, tool_resource_remote_process,
)
from app.application.media import (  # noqa: E402
    remote_execution, remote_worker_client as rwc, resource_store as rs, run_binding,
)


# ── fakes / helpers ───────────────────────────────────────────────────────────
def _caps(operations=("ocr",), max_upload=50 * 1024 * 1024,
          max_response=8 * 1024 * 1024, max_pages=100, response_timeout=600):
    return rwc.WorkerCapabilities(
        operations=tuple(operations), max_upload_bytes=max_upload,
        max_response_bytes=max_response, max_pages=max_pages,
        response_timeout_seconds=response_timeout,
    )


def _payload(text="Распознанный текст 123", *, digest=None, page_count=2,
             confidence=0.97, processing_ms=1234):
    return rwc.WorkerJobPayload(
        text=text,
        text_sha256=digest if digest is not None else hashlib.sha256(text.encode("utf-8")).hexdigest(),
        page_count=page_count, confidence=confidence, processing_ms=processing_ms,
    )


class FakeClient:
    def __init__(self, *, caps=None, caps_exc=None, job=None, job_exc=None,
                 timeout_seconds=660.0):
        self._caps, self._caps_exc = caps, caps_exc
        self._job, self._job_exc = job, job_exc
        self.capability_calls = 0
        self.job_calls = 0
        self.last_job_kwargs = None
        self.timeout_seconds = timeout_seconds

    def capabilities(self, **kw):
        self.capability_calls += 1
        if self._caps_exc:
            raise self._caps_exc
        return self._caps if self._caps is not None else _caps()

    def run_ocr_job(self, **kw):
        self.job_calls += 1
        self.last_job_kwargs = kw
        if self._job_exc:
            raise self._job_exc
        return self._job if self._job is not None else _payload()


class _Base(unittest.TestCase):
    def setUp(self):
        self.run_id = f"r5c-{os.getpid()}-{id(self)}"
        run_binding.clear_run(self.run_id)
        self._tok = set_current_run_id(self.run_id)
        self.owner = "sess-owner-r5c"
        self.data = b"%PDF-1.4 scanned document bytes " * 30
        self.rec = rs.register_resource(
            original_name="scan.pdf", content_type="application/pdf",
            owner_session=self.owner, data=self.data,
        )
        run_binding.bind_resources(self.run_id, [self.rec.resource_id])
        self._derived_ids: list[str] = []

    def tearDown(self):
        reset_current_run_id(self._tok)
        run_binding.clear_run(self.run_id)
        rs.discard(self.rec)
        for rid in set(self._derived_ids):
            rec = rs.get_record(rid)
            if rec is not None:
                rs.discard(rec)

    def _remember_derived(self, result):
        res = result.get("resource") if isinstance(result, dict) else None
        if res and res.get("resource_id"):
            self._derived_ids.append(res["resource_id"])
        return result


# ── 1. binding checked before get_record / read / network ─────────────────────
class BindingGateTest(_Base):
    def test_unbound_id_refused_before_get_record_and_network(self):
        run_binding.clear_run(self.run_id)                      # nothing bound now
        with mock.patch.object(rs, "get_record", side_effect=AssertionError("get_record called")), \
                mock.patch.object(remote_execution, "run_remote_ocr", side_effect=AssertionError("orchestration called")):
            out = tool_resource_remote_process(resource_id=self.rec.resource_id, operation="ocr")
        self.assertEqual(out["error"], "resource_not_bound")

    def test_bound_but_missing_record_refused_before_network(self):
        run_binding.bind_resources(self.run_id, ["0" * 32])     # bound but not in store
        with mock.patch.object(remote_execution, "run_remote_ocr", side_effect=AssertionError("orchestration called")):
            out = tool_resource_remote_process(resource_id="0" * 32, operation="ocr")
        self.assertEqual(out["error"], "resource_not_found")

    def test_no_run_context_refused(self):
        reset_current_run_id(self._tok)
        try:
            out = tool_resource_remote_process(resource_id=self.rec.resource_id, operation="ocr")
        finally:
            self._tok = set_current_run_id(self.run_id)
        self.assertEqual(out["error"], "no_run_context")


# ── 2. oversize: zero read, zero HTTP ─────────────────────────────────────────
class SizeGateTest(_Base):
    def test_oversize_refused_with_zero_read_and_zero_http(self):
        client = FakeClient()
        with mock.patch.dict(os.environ, {"ELIRA_REMOTE_MAX_INPUT_BYTES": str(len(self.data) - 1)}), \
                mock.patch.object(rs, "read_bytes", side_effect=AssertionError("read_bytes called")):
            out = remote_execution.run_remote_ocr(record=self.rec, run_id=self.run_id, client=client)
        self.assertEqual(out["error"], "remote_input_too_large")
        self.assertIn(str(len(self.data) - 1), out["text"])
        self.assertEqual(client.capability_calls, 0)
        self.assertEqual(client.job_calls, 0)

    def test_worker_advertised_cap_refused_before_read(self):
        client = FakeClient(caps=_caps(max_upload=len(self.data) - 1))
        with mock.patch.object(rs, "read_bytes", side_effect=AssertionError("read_bytes called")):
            out = remote_execution.run_remote_ocr(record=self.rec, run_id=self.run_id, client=client)
        self.assertEqual(out["error"], "remote_input_too_large")
        self.assertIn(str(len(self.data) - 1), out["text"])
        self.assertEqual(client.job_calls, 0)


# ── 3 / 4 / 21 / 22. approval + discoverability + wiring ──────────────────────
class PolicyWiringTest(unittest.TestCase):
    def test_builtin_permission_and_scopes(self):
        from app.application.tool_registry.builtins import build_builtin_tools
        spec = next(s for s in build_builtin_tools() if s["name"] == "resource_remote_process")
        self.assertEqual(spec["source"], "code_agent")
        self.assertEqual(spec["permission"], "require_approval")
        self.assertTrue(spec["side_effect"])
        self.assertFalse(spec["idempotent"])
        self.assertFalse(spec["parameters_schema"]["additionalProperties"])
        self.assertEqual(
            set(spec["parameters_schema"]["properties"]),
            {"resource_id", "operation"},
        )
        self.assertEqual(
            spec["parameters_schema"]["properties"]["resource_id"]["pattern"],
            "^[0-9a-f]{32}$",
        )
        # MANDATORY scope wiring — a missing entry would make the executor scope
        # gate pass vacuously (test 22). fs.write is declared because the tool
        # registers (and may unlink) a durable derived resource.
        self.assertEqual(set(spec["scopes"]), {"fs.read", "fs.write", "net.outbound"})

    def test_policy_sets(self):
        from app.application.code_agent.tool_policy import (
            BASE_TOOLS, CRITICAL_TOOLS, EDIT_ONLY_TOOLS, SEARCH_ACTIVATABLE_SIDE_EFFECT,
        )
        n = "resource_remote_process"
        self.assertIn(n, SEARCH_ACTIVATABLE_SIDE_EFFECT)   # tool_search may surface it
        self.assertNotIn(n, EDIT_ONLY_TOOLS)               # accept_edits still asks
        self.assertNotIn(n, CRITICAL_TOOLS)                # not forced under bypass
        self.assertNotIn(n, BASE_TOOLS)                    # no base-prompt tokens

    def test_mode_auto_approves(self):
        from app.application.code_agent.loop_helpers import _mode_auto_approves
        n = "resource_remote_process"
        self.assertFalse(_mode_auto_approves("ask", n))
        self.assertFalse(_mode_auto_approves("accept_edits", n))
        self.assertTrue(_mode_auto_approves("bypass", n))

    def test_schema_args_are_opaque_only(self):
        from app.application.code_agent.tool_schemas import build_tool_schemas
        spec = next(s for s in build_tool_schemas() if s["function"]["name"] == "resource_remote_process")
        params = spec["function"]["parameters"]
        self.assertFalse(params["additionalProperties"])
        self.assertEqual(set(params["properties"]), {"resource_id", "operation"})
        self.assertEqual(params["required"], ["resource_id", "operation"])
        self.assertEqual(params["properties"]["resource_id"]["pattern"], "^[0-9a-f]{32}$")
        self.assertEqual(params["properties"]["operation"]["enum"], ["ocr"])

    def test_handler_rejects_injected_arguments(self):
        for extra in ({"url": "https://x"}, {"path": "/etc"}, {"token": "t"},
                      {"ca": "/c"}, {"language": "ru"}, {"dpi": 300}, {"argv": "rm"}):
            out = tool_resource_remote_process(resource_id="a" * 32, operation="ocr", **extra)
            self.assertEqual(out["error"], "unsupported_arguments")

    def test_russian_tool_search_activates_without_base_tokens(self):
        from app.application.agent_kernel.deferred_tools import (
            clear_run, enable_deferred_tools, is_tool_active,
        )
        from app.application.code_agent.tools import tool_search
        from app.application.code_agent.tool_policy import BASE_TOOLS
        from app.application.tool_registry.runtime import seed_builtin_tools
        seed_builtin_tools()
        rid = f"r5c-search-{os.getpid()}"
        enable_deferred_tools(rid, ())
        try:
            tool_search(run_id=rid, query="удалённый OCR распознать текст", permission_mode="ask")
            self.assertTrue(is_tool_active(rid, "resource_remote_process"))
        finally:
            clear_run(rid)
        self.assertNotIn("resource_remote_process", BASE_TOOLS)


# ── 5 / 6 / 7 / 9 / 10 / 12. transport rules (real client, MockTransport) ─────
class TransportConfigTest(unittest.TestCase):
    def _env(self, **overrides):
        base = {
            "ELIRA_REMOTE_WORKER_URL": "",
            "ELIRA_REMOTE_WORKER_CA": "",
            "ELIRA_REMOTE_WORKER_TOKEN": "",
            "ELIRA_REMOTE_WORKER_ALLOW_INSECURE": "0",
        }
        base.update(overrides)
        return mock.patch.dict(os.environ, base, clear=False)

    def test_empty_url_or_token_unavailable(self):
        with self._env(ELIRA_REMOTE_WORKER_URL=""):
            self.assertRaises(rwc.RemoteUnavailable, rwc.resolve_config)
        with self._env(ELIRA_REMOTE_WORKER_URL="https://w:8443", ELIRA_REMOTE_WORKER_TOKEN=""):
            self.assertRaises(rwc.RemoteUnavailable, rwc.resolve_config)

    def test_https_requires_pinned_ca(self):
        with self._env(ELIRA_REMOTE_WORKER_URL="https://w:8443",
                       ELIRA_REMOTE_WORKER_TOKEN="tok", ELIRA_REMOTE_WORKER_CA=""):
            with self.assertRaises(rwc.RemoteUnavailable) as ctx:
                rwc.resolve_config()
            self.assertEqual(ctx.exception.reason, "tls_ca_missing")
        with self._env(ELIRA_REMOTE_WORKER_URL="https://w:8443",
                       ELIRA_REMOTE_WORKER_TOKEN="tok",
                       ELIRA_REMOTE_WORKER_CA=str(Path(tempfile.gettempdir()) / "does-not-exist-r5c.pem")):
            self.assertRaises(rwc.RemoteUnavailable, rwc.resolve_config)

    def test_malformed_port_is_controlled_unavailable(self):
        with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as fh:
            ca_path = fh.name
        try:
            with self._env(
                ELIRA_REMOTE_WORKER_URL="https://worker:not-a-port",
                ELIRA_REMOTE_WORKER_TOKEN="tok",
                ELIRA_REMOTE_WORKER_CA=ca_path,
            ):
                with self.assertRaises(rwc.RemoteUnavailable) as ctx:
                    rwc.resolve_config()
            self.assertEqual(ctx.exception.reason, "url_invalid")
        finally:
            os.unlink(ca_path)

    def test_https_with_ca_configures_verify(self):
        with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as fh:
            ca_path = fh.name
        try:
            with self._env(ELIRA_REMOTE_WORKER_URL="https://w:8443/",
                           ELIRA_REMOTE_WORKER_TOKEN="tok", ELIRA_REMOTE_WORKER_CA=ca_path):
                cfg = rwc.resolve_config()
            self.assertEqual(cfg.verify, ca_path)
            self.assertTrue(cfg.send_auth)
            self.assertEqual(cfg.base_url, "https://w:8443")     # trailing slash stripped
        finally:
            os.unlink(ca_path)

    def test_http_forbidden_without_allow_insecure(self):
        with self._env(ELIRA_REMOTE_WORKER_URL="http://w:8002",
                       ELIRA_REMOTE_WORKER_TOKEN="tok"):
            with self.assertRaises(rwc.RemoteUnavailable) as ctx:
                rwc.resolve_config()
            self.assertEqual(ctx.exception.reason, "insecure_http_forbidden")

    def test_http_insecure_does_not_send_authorization(self):
        with self._env(ELIRA_REMOTE_WORKER_URL="http://w:8002",
                       ELIRA_REMOTE_WORKER_TOKEN="tok",
                       ELIRA_REMOTE_WORKER_ALLOW_INSECURE="1"):
            cfg = rwc.resolve_config()
        self.assertFalse(cfg.send_auth)
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if request.url.path.endswith("/capabilities"):
                return httpx.Response(200, json={"operations": ["ocr"], "max_upload_bytes": 1024,
                                                 "max_response_bytes": 4096, "max_pages": 10,
                                                 "response_timeout_seconds": 600})
            text = "ok"
            return httpx.Response(200, json={"request_id": "r", "operation": "ocr", "text": text,
                                             "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                                             "language": "ru", "confidence": 0.5, "page_count": 1,
                                             "processing_ms": 5, "errors": []})

        client = rwc.WorkerClient(cfg, transport=httpx.MockTransport(handler))
        client.capabilities()
        client.run_ocr_job(filename="scan.pdf", content_type="application/pdf",
                           data=b"bytes", content_sha256=hashlib.sha256(b"bytes").hexdigest())
        for request in seen:
            self.assertNotIn("authorization", request.headers)

    def test_url_userinfo_is_stripped_no_basic_auth_leak(self):
        # httpx would turn http://user:pass@host into an Authorization: Basic
        # header. The base_url must be rebuilt without userinfo so no credential
        # reaches the wire (and the insecure mode still sends no Authorization).
        with self._env(ELIRA_REMOTE_WORKER_URL="http://user:secretpass@w:8002/",
                       ELIRA_REMOTE_WORKER_TOKEN="tok",
                       ELIRA_REMOTE_WORKER_ALLOW_INSECURE="1"):
            cfg = rwc.resolve_config()
        self.assertEqual(cfg.base_url, "http://w:8002")
        self.assertNotIn("secretpass", cfg.base_url)
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers)
            return httpx.Response(200, json={"operations": ["ocr"], "max_upload_bytes": 1024,
                                             "max_response_bytes": 4096, "max_pages": 10,
                                             "response_timeout_seconds": 600})

        rwc.WorkerClient(cfg, transport=httpx.MockTransport(handler)).capabilities()
        self.assertNotIn("authorization", seen[0])

    def _https_client(self, handler):
        cfg = rwc.WorkerConfig(base_url="https://w:8443", verify="/x", token="tok",
                               send_auth=True, timeout=30.0)
        return rwc.WorkerClient(cfg, transport=httpx.MockTransport(handler))

    def test_job_post_has_content_length_and_no_transfer_encoding(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["headers"] = request.headers
            text = "ok"
            return httpx.Response(200, json={"request_id": "r", "operation": "ocr", "text": text,
                                             "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                                             "language": "ru", "confidence": 0.5, "page_count": 1,
                                             "processing_ms": 5, "errors": []})

        client = self._https_client(handler)
        client.run_ocr_job(filename="scan.pdf", content_type="application/pdf",
                           data=b"payload-bytes", content_sha256=hashlib.sha256(b"payload-bytes").hexdigest())
        self.assertIn("content-length", captured["headers"])
        self.assertNotIn("transfer-encoding", captured["headers"])
        self.assertIn("x-content-sha256", captured["headers"])
        self.assertEqual(captured["headers"]["authorization"], "Bearer tok")

    def test_retry_zero_single_post_on_transport_error(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            raise httpx.ConnectError("boom", request=request)

        client = self._https_client(handler)
        with self.assertRaises(rwc.RemoteTransportError):
            client.run_ocr_job(filename="s", content_type="text/plain",
                               data=b"x", content_sha256=hashlib.sha256(b"x").hexdigest())
        self.assertEqual(calls["n"], 1)

    def test_non_200_status_is_transport_error_without_reading_body(self):
        secret = "SECRET-/opt/model.bin"

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": {"code": "internal_error", "message": secret}})

        client = self._https_client(handler)
        with self.assertRaises(rwc.RemoteTransportError) as ctx:
            client.capabilities()
        self.assertNotIn(secret, ctx.exception.reason)
        self.assertNotIn("/opt/model.bin", str(ctx.exception))

    def test_client_construction_failure_is_controlled_transport_error(self):
        client = self._https_client(lambda request: httpx.Response(200, json={}))
        with mock.patch.object(client, "_open", side_effect=OSError("secret CA path")):
            with self.assertRaises(rwc.RemoteTransportError) as ctx:
                client.capabilities()
        self.assertEqual(ctx.exception.reason, "transport")
        self.assertNotIn("secret CA path", str(ctx.exception))

    def test_capabilities_uses_short_timeout_and_response_cap_is_exact(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen[request.url.path] = request.extensions["timeout"]["read"]
            if request.url.path.endswith("/capabilities"):
                return httpx.Response(200, json={
                    "operations": ["ocr"], "max_upload_bytes": 1024,
                    "max_response_bytes": 4096, "max_pages": 10,
                    "response_timeout_seconds": 600,
                })
            text = "ok"
            return httpx.Response(200, json={
                "request_id": "r", "operation": "ocr", "text": text,
                "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "language": "ru", "confidence": 0.5, "page_count": 1,
                "processing_ms": 5, "errors": [],
            })

        client = self._https_client(handler)
        client.capabilities()
        client.run_ocr_job(
            filename="scan.pdf", content_type="application/pdf", data=b"x",
            content_sha256=hashlib.sha256(b"x").hexdigest(), response_cap=4096,
        )
        self.assertLessEqual(seen["/v1/capabilities"], rwc._CAPABILITIES_TIMEOUT_SECONDS)
        self.assertEqual(rwc._JOB_RESPONSE_READ_CAP, 8 * 1024 * 1024)

    def test_trickling_response_cannot_exceed_hard_deadline(self):
        class SlowStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                await asyncio.sleep(1.0)
                yield b"{}"

        client = self._https_client(
            lambda request: httpx.Response(200, stream=SlowStream())
        )
        # A blocked read is cancelled by the total deadline; it cannot wait for
        # the stream's one-second yield merely because no inactivity timeout fired.
        with mock.patch.object(rwc, "_MIN_TIMEOUT_SECONDS", 0.1):
            with self.assertRaises(rwc.RemoteTransportError) as ctx:
                client._read_bounded("GET", "/v1/capabilities", cap=1024, timeout_seconds=0.1)
        self.assertEqual(ctx.exception.reason, "deadline_exceeded")

    def test_oversized_response_rejected_before_parse(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"x" * 500)

        client = self._https_client(handler)
        with mock.patch.object(rwc, "_JOB_RESPONSE_READ_CAP", 100), \
                mock.patch.object(rwc, "_CAPABILITIES_READ_CAP", 100):
            with self.assertRaises(rwc.RemoteProtocolError) as ctx:
                client.capabilities()
        self.assertEqual(ctx.exception.reason, "response_too_large")

    def test_malformed_and_incomplete_responses_rejected(self):
        def bad_json(request):
            return httpx.Response(200, content=b"not json")

        def missing_field(request):
            return httpx.Response(200, json={"operation": "ocr", "text": "x"})   # no text_sha256

        self.assertRaises(rwc.RemoteProtocolError, self._https_client(bad_json).capabilities)
        with self.assertRaises(rwc.RemoteProtocolError):
            self._https_client(missing_field).run_ocr_job(
                filename="s", content_type="text/plain", data=b"x",
                content_sha256=hashlib.sha256(b"x").hexdigest())

    def test_huge_integer_confidence_is_controlled_not_overflow(self):
        # A 320-digit integer confidence would OverflowError on float() — it must
        # be caught and surfaced as a controlled protocol error, not crash.
        text = "x"
        huge = int("9" * 320)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"operation": "ocr", "text": text,
                                             "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                                             "language": "ru", "confidence": huge,
                                             "page_count": 0, "processing_ms": 0, "errors": []})

        with self.assertRaises(rwc.RemoteProtocolError):
            self._https_client(handler).run_ocr_job(
                filename="s", content_type="text/plain", data=b"x",
                content_sha256=hashlib.sha256(b"x").hexdigest())

    def test_out_of_range_and_bool_confidence_rejected(self):
        import json as _json
        for bad in (1.5, -0.1, float("inf"), float("nan"), True, "0.5", None):
            # json.dumps (allow_nan default) emits NaN/Infinity tokens, which the
            # client's json.loads accepts — httpx's json= encoder would reject them.
            body = _json.dumps({"operation": "ocr", "text": "x",
                                "text_sha256": hashlib.sha256(b"x").hexdigest(),
                                "language": "ru", "confidence": bad,
                                "page_count": 0, "processing_ms": 0, "errors": []}).encode("utf-8")

            def handler(request, _b=body):
                return httpx.Response(200, content=_b)

            with self.subTest(bad=bad), self.assertRaises(rwc.RemoteProtocolError):
                self._https_client(handler).run_ocr_job(
                    filename="s", content_type="text/plain", data=b"x",
                    content_sha256=hashlib.sha256(b"x").hexdigest())


# ── 8. capability allowlist intersection ──────────────────────────────────────
class CapabilityAllowlistTest(_Base):
    def test_unknown_operations_are_dropped(self):
        client = FakeClient(caps=_caps(operations=("shell", "exec")))
        out = remote_execution.run_remote_ocr(record=self.rec, run_id=self.run_id, client=client)
        self.assertEqual(out["error"], "remote_operation_unsupported")
        self.assertEqual(client.job_calls, 0)

    def test_ocr_present_among_others_proceeds(self):
        client = FakeClient(caps=_caps(operations=("ocr", "shell")))
        out = self._remember_derived(
            remote_execution.run_remote_ocr(record=self.rec, run_id=self.run_id, client=client))
        self.assertTrue(out["ok"])
        self.assertEqual(client.job_calls, 1)


# ── 11 / 13. verification + no leakage ────────────────────────────────────────
class VerificationTest(_Base):
    def test_text_sha256_mismatch_registers_nothing(self):
        client = FakeClient(job=_payload("real text", digest="0" * 64))   # wrong digest
        with mock.patch.object(rs, "register_resource", side_effect=AssertionError("registered")):
            out = remote_execution.run_remote_ocr(record=self.rec, run_id=self.run_id, client=client)
        self.assertEqual(out["error"], "remote_verify_failed")

    def test_transport_failure_maps_to_fixed_message(self):
        client = FakeClient(job_exc=rwc.RemoteTransportError("status_500"))
        out = remote_execution.run_remote_ocr(record=self.rec, run_id=self.run_id, client=client)
        self.assertEqual(out["error"], "remote_worker_failed")
        blob = repr(out)
        self.assertNotIn("status_500", blob)
        self.assertNotIn("500", out["text"])

    def test_protocol_failure_maps_to_invalid_response(self):
        client = FakeClient(job_exc=rwc.RemoteProtocolError("job_digest_shape"))
        out = remote_execution.run_remote_ocr(record=self.rec, run_id=self.run_id, client=client)
        self.assertEqual(out["error"], "remote_invalid_response")

    def test_success_projection_carries_no_ocr_text_or_internal_types(self):
        secret_text = "секрет /opt/model.bin token=abc"
        client = FakeClient(job=_payload(secret_text))
        out = self._remember_derived(
            remote_execution.run_remote_ocr(record=self.rec, run_id=self.run_id, client=client))
        self.assertEqual(set(out), {"ok", "operation", "resource", "pages", "confidence", "chars"})
        self.assertNotIn(secret_text, repr(out))          # the OCR text never crosses
        self.assertEqual(out["chars"], len(secret_text))
        self.assertNotIsInstance(out, remote_execution.RemoteJobResult)
        for value in out.values():
            self.assertNotIsInstance(value, remote_execution.RemoteJobResult)

    def test_advertised_page_and_processing_caps_are_enforced(self):
        for caps, payload in (
            (_caps(max_pages=1), _payload(page_count=2)),
            (_caps(response_timeout=1), _payload(processing_ms=1001)),
        ):
            with self.subTest(caps=caps, payload=payload), \
                    mock.patch.object(rs, "register_resource", side_effect=AssertionError("registered")):
                out = remote_execution.run_remote_ocr(
                    record=self.rec, run_id=self.run_id,
                    client=FakeClient(caps=caps, job=payload),
                )
            self.assertEqual(out["error"], "remote_invalid_response")

    def test_one_total_deadline_suppresses_late_registration(self):
        client = FakeClient(timeout_seconds=660.0)
        with mock.patch.object(
            remote_execution.time, "monotonic", side_effect=[100.0, 100.0, 110.0, 761.0]
        ), mock.patch.object(rs, "register_resource", side_effect=AssertionError("registered")):
            out = remote_execution.run_remote_ocr(
                record=self.rec, run_id=self.run_id, client=client
            )
        self.assertEqual(out["error"], "remote_worker_failed")
        self.assertEqual(client.job_calls, 1)
        self.assertLessEqual(client.last_job_kwargs["timeout_seconds"], 650.0)


# ── 14 / 15 / 16. derived resource lifecycle ──────────────────────────────────
class DerivedResourceTest(_Base):
    def test_owner_inherited_and_both_ids_bound(self):
        client = FakeClient(job=_payload("Распознано"))
        out = self._remember_derived(
            remote_execution.run_remote_ocr(record=self.rec, run_id=self.run_id, client=client))
        self.assertTrue(out["ok"])
        derived_id = out["resource"]["resource_id"]
        self.assertNotEqual(derived_id, self.rec.resource_id)
        derived = rs.get_record(derived_id)
        self.assertIsNotNone(derived)
        self.assertEqual(derived.owner_session, self.owner)           # inherited
        # The store normalizes the MIME by dropping parameters (charset), so the
        # registered content_type is the bare "text/plain".
        self.assertEqual(derived.content_type, "text/plain")
        self.assertEqual(derived.kind, "document")
        self.assertTrue(run_binding.is_bound(self.run_id, derived_id))       # new bound
        self.assertTrue(run_binding.is_bound(self.run_id, self.rec.resource_id))  # original kept

    def test_add_bound_failure_discards_blob_and_metadata(self):
        created = {}
        real_register = rs.register_resource

        def capture(**kw):
            rec = real_register(**kw)
            created["rec"] = rec
            return rec

        client = FakeClient(job=_payload("text"))
        with mock.patch.object(rs, "register_resource", side_effect=capture), \
                mock.patch.object(run_binding, "add_bound", return_value=False):
            out = remote_execution.run_remote_ocr(record=self.rec, run_id=self.run_id, client=client)
        self.assertEqual(out["error"], "remote_bind_failed")
        rec = created["rec"]
        self._derived_ids.append(rec.resource_id)
        # discard removed BOTH the blob and the metadata sidecar.
        self.assertIsNone(rs.get_record(rec.resource_id))
        self.assertFalse(Path(rec.storage_path).exists())
        self.assertFalse((rs._meta_dir() / f"{rec.resource_id}.json").exists())
        self.assertFalse(run_binding.is_bound(self.run_id, rec.resource_id))

    def test_add_bound_never_resurrects_a_cleared_run(self):
        run_binding.clear_run(self.run_id)
        self.assertFalse(run_binding.add_bound(self.run_id, "f" * 32))
        self.assertEqual(run_binding.bound_resources(self.run_id), set())

    def test_run_cleared_during_job_discards_derived_resource(self):
        created = {}
        real_register = rs.register_resource

        def capture(**kw):
            rec = real_register(**kw)
            created["rec"] = rec
            return rec

        class ClearingClient(FakeClient):
            def run_ocr_job(inner_self, **kw):
                run_binding.clear_run(self.run_id)
                return super().run_ocr_job(**kw)

        with mock.patch.object(rs, "register_resource", side_effect=capture):
            out = remote_execution.run_remote_ocr(
                record=self.rec, run_id=self.run_id, client=ClearingClient()
            )
        self.assertEqual(out["error"], "remote_bind_failed")
        derived = created["rec"]
        self.assertIsNone(rs.get_record(derived.resource_id))
        self.assertFalse(Path(derived.storage_path).exists())
        self.assertEqual(run_binding.bound_resources(self.run_id), set())

    def test_same_source_replacement_cannot_receive_old_derived_resource(self):
        created = {}
        real_register = rs.register_resource

        def capture(**kw):
            rec = real_register(**kw)
            created["rec"] = rec
            return rec

        class ReplacingClient(FakeClient):
            def run_ocr_job(inner_self, **kw):
                # Same source, new generation: proves this is not merely a set
                # membership check and closes the run-id ABA race.
                run_binding.bind_resources(self.run_id, [self.rec.resource_id])
                return super().run_ocr_job(**kw)

        with mock.patch.object(rs, "register_resource", side_effect=capture):
            out = remote_execution.run_remote_ocr(
                record=self.rec, run_id=self.run_id, client=ReplacingClient()
            )
        self.assertEqual(out["error"], "remote_bind_failed")
        derived = created["rec"]
        self.assertIsNone(rs.get_record(derived.resource_id))
        self.assertEqual(run_binding.bound_resources(self.run_id), {self.rec.resource_id})

    def test_bind_baseexception_still_discards_in_finally(self):
        created = {}
        real_register = rs.register_resource

        def capture(**kw):
            rec = real_register(**kw)
            created["rec"] = rec
            return rec

        with mock.patch.object(rs, "register_resource", side_effect=capture), \
                mock.patch.object(run_binding, "add_bound", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                remote_execution.run_remote_ocr(
                    record=self.rec, run_id=self.run_id, client=FakeClient()
                )
        derived = created["rec"]
        self.assertIsNone(rs.get_record(derived.resource_id))
        self.assertFalse(Path(derived.storage_path).exists())


# ── 18 / 19 / 20. non-regression + interop ────────────────────────────────────
class NonRegressionTest(_Base):
    def test_resource_process_still_rejects_remote(self):
        out = tool_resource_process(resource_id=self.rec.resource_id,
                                    operation="extract_text", execution_target="remote")
        self.assertEqual(out["error"], "invalid_execution_target")
        from app.application.code_agent.tool_schemas import build_tool_schemas
        spec = next(s for s in build_tool_schemas() if s["function"]["name"] == "resource_process")
        self.assertNotIn("remote", spec["function"]["parameters"]["properties"]["execution_target"]["enum"])

    def test_upload_stays_deferred(self):
        # Registering a resource performs no processing and no remote call.
        with mock.patch.object(rwc.WorkerClient, "capabilities", side_effect=AssertionError("network")), \
                mock.patch.object(rwc.WorkerClient, "run_ocr_job", side_effect=AssertionError("network")):
            rec = rs.register_resource(original_name="x.pdf", content_type="application/pdf",
                                       owner_session=self.owner, data=b"%PDF-1.4 more")
        self.addCleanup(rs.discard, rec)
        self.assertEqual(rec.size, len(b"%PDF-1.4 more"))

    def test_derived_resource_flows_into_materialize_and_publish(self):
        from app.core.config import GENERATED_DIR
        text = "Распознанный OCR текст для проверки интеропа"
        client = FakeClient(job=_payload(text))
        out = self._remember_derived(
            remote_execution.run_remote_ocr(record=self.rec, run_id=self.run_id, client=client))
        derived_id = out["resource"]["resource_id"]

        proj = Path(tempfile.mkdtemp())
        dispatch = build_tool_dispatch(proj)
        mat = dispatch["resource_materialize"](resource_id=derived_id, destination_name="result.txt")
        self.assertTrue(mat["ok"], mat)
        self.assertEqual((proj / "result.txt").read_text(encoding="utf-8"), text)

        name = f"r5c_ocr_{os.getpid()}_{len(self._derived_ids)}.txt"
        pub = dispatch["resource_publish"](project_path="result.txt", download_name=name)
        try:
            self.assertTrue(pub["ok"], pub)
            self.assertEqual((GENERATED_DIR / name).read_text(encoding="utf-8"), text)
        finally:
            (GENERATED_DIR / name).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
