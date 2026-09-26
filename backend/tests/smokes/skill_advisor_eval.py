"""Opt-in first-decision Qwen comparison: adviser off/on/shadow, no tools run.

--data-dir points to an isolated acceptance stand (read-only skills/model).
--cases is a JSON list of {id, user_message, expected_skills: [names]}.
--output must be a new directory. Default captures the real public agent's
first request with a stub. --live replays only those requests using the existing
provider diagnostic seam; it never sends responses back to the agent loop.

No active learned model means insufficient evidence and NO live replay. This
diagnostic does not train, synthesize experience, or certify entire task success.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
MODES = ("off", "on", "shadow")


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                    encoding="utf-8", newline="\n")


def load_cases(path: Path) -> list[dict]:
    cases = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(cases, dict):
        cases = cases.get("cases")
    if not isinstance(cases, list) or not cases or len(cases) > 40:
        raise ValueError("Cases must contain 1-40 explicitly labelled requests")
    seen = set()
    for case in cases:
        if not isinstance(case, dict) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", str(case.get("id", ""))):
            raise ValueError("Each case needs a unique safe id")
        if case["id"] in seen:
            raise ValueError("Duplicate case id")
        seen.add(case["id"])
        if not isinstance(case.get("user_message"), str) or not case["user_message"].strip():
            raise ValueError("Each case needs its saved original user_message")
        expected = case.get("expected_skills")
        if not isinstance(expected, list) or any(not isinstance(name, str) or not name for name in expected):
            raise ValueError("expected_skills must be explicit, including [] for no skill needed")
    return cases


def tree_files(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    result = {}
    for item in sorted(path.rglob("*")):
        if item.is_symlink():
            raise ValueError("Diagnostic snapshot cannot follow redirected files")
        if item.is_file() and item.name != "state.lock" and "__pycache__" not in item.parts:
            result[item.relative_to(path).as_posix()] = file_hash(item)
    return result


def freeze(data_dir: Path, case_path: Path, settings: dict) -> dict:
    from app.application.code_agent import task_skills
    from app.infrastructure.llm.openai_compatible import local_llm_config

    config = local_llm_config()
    configuration = {"model": config.model, "base_url": config.base_url, "enabled": config.enabled,
                     "context_window": config.context_window, "max_tokens": config.max_tokens,
                     "timeout_seconds": config.timeout_seconds, **settings}
    source = {path.relative_to(ROOT).as_posix(): file_hash(path)
              for path in sorted((ROOT / "backend/app").rglob("*.py")) if "__pycache__" not in path.parts}
    for path in (Path(__file__), HERE / "skill_selection_eval.py", HERE / "answer_contract_eval.py"):
        source[path.relative_to(ROOT).as_posix()] = file_hash(path)
    catalog = task_skills.discover_skills()
    bindings = {item["name"]: task_skills._skill_binding(task_skills._read(item["name"]))
                for item in catalog["skills"]}
    return {"source_sha256": fingerprint(source), "source_files": source,
            "configuration_sha256": fingerprint(configuration), "configuration": configuration,
            "environment_file_sha256": file_hash(ROOT / "backend/.env.local") if (ROOT / "backend/.env.local").exists() else None,
            "adviser_files": tree_files(data_dir / "skill_advisor"),
            "catalog_sha256": fingerprint([catalog, bindings]), "catalog": catalog, "identities": bindings,
            "cases_file_sha256": file_hash(case_path)}


def server_snapshot() -> dict:
    """Live-only read of actual llama.cpp identity/settings, never credentials."""
    from app.infrastructure.llm import openai_compatible as provider

    config = provider.local_llm_config()
    response = provider.requests.get(config.base_url.removesuffix("/v1") + "/props",
                                     headers=provider._headers(config), timeout=10)
    response.raise_for_status()
    props = response.json()
    if not isinstance(props, dict):
        raise ValueError("llama.cpp returned invalid server properties")
    selected = {"model_path": props.get("model_path"),
                "default_generation_settings": props.get("default_generation_settings"),
                "chat_template_sha256": fingerprint(props.get("chat_template")),
                "total_slots": props.get("total_slots")}
    return {"sha256": fingerprint(selected), "properties": selected}


class _CaptureViolation(BaseException):
    pass


def capture_case(case: dict, mode: str, workspace: Path, model: str, num_ctx: int) -> dict:
    from app.application.code_agent import agent_loop, task_skills

    captured = {}
    events = []

    def chat(**kwargs):
        captured.update(deepcopy({key: value for key, value in kwargs.items() if key != "options"}))
        captured["options"] = {key: deepcopy(value) for key, value in kwargs["options"].items() if not key.startswith("_")}
        return {"message": {"content": "Диагностический ответ.", "tool_calls": []}}

    def prohibited(*_args, **_kwargs):
        raise _CaptureViolation("Prompt capture attempted network, inference, learning, or tool execution")

    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {"ELIRA_SKILL_ADVISOR_MODE": mode, "LLAMA_SERVER_ENABLED": "false"}))
        stack.enter_context(patch.object(agent_loop, "_kernel_exec", side_effect=prohibited))
        stack.enter_context(patch.object(agent_loop, "learn_from_run", return_value={"status": "diagnostic_disabled"}))
        stack.enter_context(patch("requests.sessions.Session.request", side_effect=prohibited))
        stack.enter_context(patch("urllib.request.urlopen", side_effect=prohibited))
        stream = agent_loop.stream_code_agent(
            user_message=case["user_message"], memory_query=case["user_message"],
            project_root=workspace, model=model, chat_fn=chat, auto_remember=False,
            num_ctx=num_ctx, reasoning_effort="none", run_id="advisor-diag-" + case["id"],
        )
        try:
            for event in stream:
                if event.get("type") == "skill_advisor_consulted":
                    events.append(deepcopy(event))
                if captured:
                    break  # Stop at the first yielded boundary after the stub.
        finally:
            stream.close()
    if not captured:
        raise RuntimeError(f"Public prompt did not reach the first-decision seam: {case['id']}/{mode}")
    catalog = [message for message in captured["messages"] if message.get("_msg_id") == task_skills.CATALOG_ID]
    if len(catalog) != 1:
        raise RuntimeError("Public prompt lost its complete skill catalog")
    return {"id": case["id"] + "-" + mode, "case_id": case["id"], "mode": mode,
            "user_message": case["user_message"], "expected": case["expected_skills"],
            "request": captured, "advisor_events": events,
            "catalog_sha256": fingerprint(catalog), "request_sha256": fingerprint(captured)}


def comparable_request(case: dict) -> dict:
    from app.application.code_agent.task_skills import ADVISOR_CONTEXT_ID

    request = deepcopy(case["request"])
    request["messages"] = [message for message in request["messages"] if message.get("_msg_id") != ADVISOR_CONTEXT_ID]
    return request


def novelty(cases: list[dict]) -> dict:
    from app.application.code_agent import skill_advisor

    samples = skill_advisor._samples()
    used = {sample["request_hash"] for sample in samples}
    seen = set()
    result = {}
    for case in cases:
        request_hash = skill_advisor._digest(skill_advisor._query(case["user_message"]))
        if request_hash in used or request_hash in seen:
            raise ValueError(f"Case is not independent after request normalization: {case['id']}")
        seen.add(request_hash)
        query = {key for key in skill_advisor.features(case["user_message"]) if key.startswith("q:")}
        overlap = 0.0
        for sample in samples:
            other = {key for key in sample["features"] if key.startswith("q:")}
            overlap = max(overlap, len(query & other) / max(1, len(query | other)))
        result[case["id"]] = {"normalized_request_hash": request_hash,
                              "not_in_training_or_holdout": True, "max_feature_jaccard": round(overlap, 4)}
    return result


def selection(response: dict, expected: list[str]) -> dict:
    from skill_selection_eval import classify

    classified = classify(response, expected)
    selected = classified["selected"]
    outcome = ("matched" if set(selected) & set(expected) else "different_first_choice") if selected else "deferred" if expected else "no_skill_needed"
    if selected and not expected:
        outcome = "unexpected_first_choice"
    return {"actual_selected": selected, "first_decision_status": outcome,
            "task_success": "not_evaluated", "deferred_is_failure": False}


def replay(captured: dict, seed: int, output: Path) -> dict:
    from answer_contract_eval import replay_case

    # Reuse the existing provider-only replay. Its returned proposed calls never
    # re-enter stream_code_agent, so this path cannot execute or learn from them.
    with redirect_stdout(io.StringIO()):
        raw = replay_case(captured, seed, output)
    response = raw.get("response") or {}
    result = {"case_id": captured["case_id"], "mode": captured["mode"], "seed": seed,
              "request_sha256": captured["request_sha256"], "wire_request_sha256": raw.get("request_hash"),
              "elapsed_ms": raw.get("elapsed_ms"), "first_answer_ms": raw.get("first_answer_ms"),
              "ttft_ms": response.get("ttft_ms"), "prompt_tokens": response.get("prompt_eval_count"),
              "completion_tokens": response.get("eval_count"), "cached_prompt_tokens": response.get("cached_prompt_tokens"),
              "error": raw.get("error"), **selection(response, captured["expected"])}
    if result["error"]:
        result["first_decision_status"] = "provider_error"
    print(json.dumps({key: result[key] for key in ("case_id", "mode", "first_decision_status", "elapsed_ms", "prompt_tokens", "completion_tokens")}), flush=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--live", action="store_true", help="Opt-in provider replay; coordinate its Qwen slot separately")
    parser.add_argument("--seed", type=int, default=41)
    parser.add_argument("--num-ctx", type=int, default=32768)
    args = parser.parse_args()
    data_dir, output, case_path = args.data_dir.resolve(), args.output.resolve(), args.cases.resolve()
    if not data_dir.is_dir() or data_dir.is_relative_to((ROOT / "data").resolve()):
        parser.error("--data-dir must be an existing isolated acceptance directory, not production data")
    if output.exists() or output.is_relative_to(data_dir):
        parser.error("--output must be a new path outside the read-only acceptance data")
    if not 4096 <= args.num_ctx <= 131072:
        parser.error("Diagnostic context must be 4096-131072")
    cases = load_cases(case_path)
    output.mkdir(parents=True)
    runtime = output / "capture-runtime"
    workspace = runtime / "workspace"
    workspace.mkdir(parents=True)
    sys.path.insert(0, str(ROOT / "backend"))
    sys.path.insert(0, str(HERE))
    from dotenv import load_dotenv
    load_dotenv(ROOT / "backend/.env.local", override=False)
    os.environ.update({"ELIRA_DATA_DIR": str(runtime / "data"), "ELIRA_AGENT_RUNS_DIR": str(runtime / "runs"),
                       "ELIRA_CONFIG_ROOT": str(runtime / "config"), "LOCAL_EMBED_ENABLED": "false"})
    from app.application.code_agent import skill_advisor, skill_development
    from app.infrastructure.llm.openai_compatible import local_llm_config
    # Only immutable advisor/package reads target the acceptance stand. All
    # incidental prompt-builder journal/persona state belongs to this output.
    skill_advisor.ROOT = data_dir / "skill_advisor"
    skill_development.ROOT = data_dir / "skill_development"
    config = local_llm_config()
    settings = {"seed": args.seed, "num_ctx": args.num_ctx, "reasoning_effort": "none", "replay_max_tokens": 1024}
    before = freeze(data_dir, case_path, settings)
    write_json(output / "freeze-before.json", before)
    manifest = {"diagnostic": "first_decision_only", "live_requested": args.live, "live_executed": False,
                "data_dir": str(data_dir), "settings": settings, "status": "capturing",
                "planned_replay_order": [[case["id"], mode] for index, case in enumerate(cases)
                    for mode in MODES[index % 3:] + MODES[:index % 3]],
                "limitations": ["No proposed tools are executed; whole-task completion is not evaluated.",
                                "Deferred first choice is not a skill-selection failure.",
                                "Normalized request independence does not prove semantic independence.",
                                "Observed latency includes server cache effects; mode order is rotated.",
                                "A small sample and residual cache/order noise do not establish a speedup from one pair."]}
    write_json(output / "manifest.json", manifest)  # Freeze order before any live request.
    code = 0
    try:
        model_status = skill_advisor.status()
        if not model_status.get("ok"):
            raise ValueError("Acceptance advisor store is not readable/valid")
        manifest["advisor_status"] = model_status
        manifest["novelty"] = novelty(cases)
        names = {item["name"] for item in before["catalog"]["skills"]}
        if any(set(case["expected_skills"]) - names for case in cases):
            raise ValueError("An expected skill is absent from the actual full catalog")
        captures = []
        for case in cases:
            variants = [capture_case(case, mode, workspace, config.model, args.num_ctx) for mode in MODES]
            if len({item["catalog_sha256"] for item in variants}) != 1:
                raise RuntimeError("Off/on/shadow changed the complete catalog")
            if len({fingerprint(comparable_request(item)) for item in variants}) != 1:
                raise RuntimeError("Off/on/shadow differ beyond the optional adviser hint")
            captures.extend(variants)
        write_json(output / "captured-cases.json", captures)
        middle = freeze(data_dir, case_path, settings)
        if middle != before:
            raise RuntimeError("Source, configuration, package identity, or learned state changed during capture")
        if not model_status.get("model_version"):
            manifest["status"] = "insufficient_evidence"
            manifest["reason"] = "No activated learned model; no off/on benefit can be measured and no live request was sent."
        elif args.live:
            if not config.enabled:
                raise RuntimeError("Configured Qwen provider is disabled")
            manifest["server_before"] = server_snapshot()
            results = []
            order = []
            by_id = {(item["case_id"], item["mode"]): item for item in captures}
            for index, case in enumerate(cases):
                modes = MODES[index % 3:] + MODES[:index % 3]
                for mode in modes:
                    order.append([case["id"], mode])
                    results.append(replay(by_id[(case["id"], mode)], args.seed, output))
                    write_json(output / "results.json", results)
            manifest.update({"status": "provider_error" if any(item["error"] for item in results) else "diagnostic_complete",
                             "live_executed": True, "replay_order": order})
            manifest["first_decisions_by_mode"] = {
                mode: {outcome: sum(item["mode"] == mode and item["first_decision_status"] == outcome for item in results)
                       for outcome in ("matched", "different_first_choice", "deferred", "no_skill_needed", "unexpected_first_choice", "provider_error")}
                for mode in MODES
            }
            code = int(any(item["error"] for item in results))
        else:
            manifest["status"] = "captured_only"
    except (Exception, _CaptureViolation) as exc:
        manifest.update({"status": "diagnostic_error", "error": str(exc)})
        code = 1
    finally:
        try:
            after = freeze(data_dir, case_path, settings)
            write_json(output / "freeze-after.json", after)
            unchanged = after == before
        except Exception as exc:
            unchanged = False
            write_json(output / "freeze-after.json", {"snapshot_error": str(exc)})
        manifest["frozen_state_unchanged"] = unchanged
        if "server_before" in manifest:
            try:
                manifest["server_after"] = server_snapshot()
                manifest["server_configuration_unchanged"] = manifest["server_before"] == manifest["server_after"]
            except Exception as exc:
                manifest["server_configuration_unchanged"] = False
                manifest["server_snapshot_error"] = str(exc)
            unchanged = unchanged and manifest["server_configuration_unchanged"]
            manifest["frozen_state_unchanged"] = unchanged
        if not unchanged:
            manifest["status"] = "invalidated_by_state_change"
            code = 1
        write_json(output / "manifest.json", manifest)
    print(json.dumps({"status": manifest["status"], "live_executed": manifest["live_executed"],
                      "frozen_state_unchanged": manifest["frozen_state_unchanged"], "output": str(output)}), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
