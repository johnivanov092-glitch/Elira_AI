from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


from app.api.routes import code_agent_routes as routes  # noqa: E402
from app.application.code_agent.tool_schemas import build_tool_schemas  # noqa: E402
from app.application.code_agent.tools import (  # noqa: E402
    reset_current_run_id,
    set_current_run_id,
)
from app.application.code_agent.tools._files import tool_read_file  # noqa: E402
from app.application.code_agent.tools._vision import tool_read_image  # noqa: E402
from app.application.media import resource_store, run_binding  # noqa: E402


def test_image_resource_tools_are_available_without_tool_search() -> None:
    refs = [{
        "resource_id": "a" * 32,
        "name": "screen.png",
        "kind": "image",
        "content_type": "image/png",
        "size": 123,
    }]

    tools = routes._base_tools_for_request("code", refs)

    assert tools is not None
    assert "resource_process" in tools
    assert "read_image" in tools


def test_image_resource_context_points_to_bound_id_not_project_path() -> None:
    rid = "b" * 32
    refs = [{
        "resource_id": rid,
        "name": "screen.png",
        "kind": "image",
        "content_type": "image/png",
        "size": 123,
    }]

    block = routes._inject_resource_context("describe it", refs)

    assert f'read_image(resource_id="{rid}")' in block
    assert "not a project file path" in block
    assert "never substitute another file" in block


def test_read_image_uses_bytes_from_bound_resource_id() -> None:
    data = b"\x89PNG\r\n\x1a\nattached-image"
    rec = resource_store.register_resource(
        original_name="screen.png",
        content_type="image/png",
        owner_session="session-image",
        data=data,
    )
    run_id = "run-attached-image"
    run_binding.bind_resources(run_id, [rec.resource_id])
    token = set_current_run_id(run_id)
    try:
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch(
                    "app.infrastructure.llm.vision_ocr.is_vision_enabled",
                    return_value=True,
                ), \
                mock.patch(
                    "app.infrastructure.llm.vision_ocr.describe_image",
                    return_value="the attached screenshot",
                ) as describe:
            # A stale project image must not be consulted when resource_id is used.
            Path(tmp, "stale.png").write_bytes(b"stale-project-image")
            out = tool_read_image(Path(tmp), resource_id=rec.resource_id)
    finally:
        reset_current_run_id(token)
        run_binding.clear_run(run_id)

    assert out["ok"] is True
    assert out["resource_id"] == rec.resource_id
    describe.assert_called_once_with("screen.png", data, prompt=None)
    assert rec.storage_path not in str(out)


def test_read_image_rejects_unbound_resource_before_vision() -> None:
    rec = resource_store.register_resource(
        original_name="screen.png",
        content_type="image/png",
        owner_session="session-image",
        data=b"\x89PNG\r\n\x1a\nimage",
    )
    token = set_current_run_id("run-without-binding")
    try:
        with mock.patch(
            "app.infrastructure.llm.vision_ocr.describe_image",
        ) as describe:
            out = tool_read_image(Path("."), resource_id=rec.resource_id)
    finally:
        reset_current_run_id(token)
        run_binding.clear_run("run-without-binding")

    assert out["ok"] is False
    assert out["error"] == "resource_not_bound"
    describe.assert_not_called()


def test_read_image_schema_accepts_path_or_bound_resource_id() -> None:
    spec = next(
        item["function"]
        for item in build_tool_schemas()
        if item["function"]["name"] == "read_image"
    )
    params = spec["parameters"]

    assert set(params["properties"]) == {"path", "resource_id", "prompt"}
    assert params["properties"]["resource_id"]["pattern"] == "^[0-9a-f]{32}$"
    assert params.get("required", []) == []


def test_missing_read_file_is_a_real_tool_failure() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        out = tool_read_file(Path(tmp), path="missing.png")

    assert out["ok"] is False
    assert out["error"] == "file_not_found"

