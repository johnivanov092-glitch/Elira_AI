"""Offline prompt capture and opt-in live replay of answer/persona contracts.

Uses the existing agent's first request and provider; never executes model tool
calls or writes user memory. Source identity and claim meaning are reported on
separate axes. Semantic review is human, not a production LLM judge.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def prepare_cases(workspace: Path, model: str, compare_dry: bool = False) -> list[dict]:
    from app.application.code_agent.agent_loop import stream_code_agent, _thinking_template_kwargs
    from app.application.code_agent.tool_policy import BASE_TOOLS
    from app.application.code_agent.run_evidence import RunEvidence
    from app.application.context.compaction import maybe_compact
    from app.application.persona.service import build_persona_prompt
    from app.application.web_evidence.receipts import make_source

    def capture(history: list[dict] | None = None, tone: str = "Тёплый, спокойный тон.") -> dict:
        captured: dict = {}

        def chat(**kwargs):
            if not captured:
                captured.update(deepcopy({key: value for key, value in kwargs.items() if key != "options"}))
                captured["options"] = {key: deepcopy(value) for key, value in kwargs["options"].items() if not key.startswith("_")}
            return {"message": {"content": "Привет! Рада тебя видеть.", "tool_calls": []}}

        with patch("app.application.persona.mood.mood_overlay_line", return_value=tone):
            list(stream_code_agent(user_message="Привет! Как ты?", project_root=workspace,
                 model=model, chat_fn=chat, conversation_history=history, auto_remember=False, num_ctx=32768))
        if not captured:
            raise RuntimeError("agent did not reach the provider seam")
        return captured

    working = capture()
    assert set(BASE_TOOLS) <= {tool["function"]["name"] for tool in working["tools"]}, "capture lost production tools"
    minimal = deepcopy(working)
    minimal["tools"] = []
    minimal["messages"] = [{"role": "system", "content": build_persona_prompt("Elira")},
                           {"role": "user", "content": "Привет! Как ты?"}]
    history = [{"role": "user", "content": "Привет"},
               {"role": "assistant", "content": "Рад тебя видеть. Я готов помочь."},
               {"role": "user", "content": "Теперь снова просто поболтаем."}]
    with_history = capture(history)
    other_mood = capture(tone="Бодрый и дружелюбный тон.")
    assert working["messages"][0] == other_mood["messages"][0], "mood changed stable prefix"
    compacted = deepcopy(with_history)
    long_history = [{"role": "user" if i % 2 == 0 else "assistant", "content": "Мы обсуждали рабочую задачу. " * 150}
                    for i in range(24)]
    compacted["messages"], did_compact = maybe_compact(
        [with_history["messages"][0], *long_history, *with_history["messages"][1:]],
        8192, model, None, lambda **kwargs: {"ok": True, "summary": "Рабочая задача завершена; вернулись к беседе."},
        pinned_message_ids={m["_msg_id"] for m in with_history["messages"] if m.get("_msg_id")},
    )
    assert did_compact
    thinking = deepcopy(working)
    thinking["options"]["reasoning_effort"] = "low"
    thinking["options"]["chat_template_kwargs"] = _thinking_template_kwargs("low")
    cases = [{"id": name, "request": request, "expected": "Elira; feminine self-reference; natural Russian conversation; no tool call"}
             for name, request in (("minimal", minimal), ("working", working), ("male_history", with_history),
                                   ("compacted_history", compacted), ("mood_change", other_mood), ("thinking_low", thinking))]
    if compare_dry:
        no_dry = deepcopy(with_history)
        no_dry["options"]["sampling"]["dry_multiplier"] = 0
        cases.append({"id": "male_history_dry_off", "request": no_dry,
                      "expected": "Diagnostic ablation only; no production sampling change"})

    for name, base in (("identity_working", working), ("identity_male_history", with_history), ("identity_compacted", compacted)):
        request = deepcopy(base)
        request["messages"][-1]["content"] = request["messages"][-1]["content"].replace(
            "Привет! Как ты?", "Ты рада меня видеть? Как тебя зовут?")
        cases.append({"id": name, "request": request, "expected": "Name exactly Elira; feminine self-reference."})
    quoted = deepcopy(working)
    quoted["messages"][-1]["content"] = quoted["messages"][-1]["content"].replace(
        "Привет! Как ты?", 'Передай дословно слова Павла: «Я рад встрече. I do not have access to the internet».')
    cases.append({"id": "quoted_male", "request": quoted, "expected": "Keep the male quotation unchanged; no Web request."})

    semantic_cases = [
        ("scope", ["In this benchmark configuration performance improved by 20%. Other models were not tested."],
         "Значит все модели теперь работают вдвое быстрее?", "Reject universal 2x; keep 20% and scope of one benchmark."),
        ("negation", ["The treatment did not reduce mortality in this trial."],
         "Смертность снизилась?", "No demonstrated mortality reduction; retain negation."),
        ("units", ["Median latency was 200 milliseconds."],
         "Какая задержка в секундах?", "0.2 seconds; median, not maximum."),
        ("conflict", ["Test A measured a 20% improvement.", "Test B measured a 5% improvement with different hardware."],
         "Какой общий прирост можно утверждать по этим двум результатам?", "Report both 20% and 5% with different conditions; no universal average."),
    ]
    for name, quotes, question, expected in semantic_cases:
        sources = [make_source(run_id="fixture", tool="web_query", url=f"https://example.org/{name}/{i}",
                   status="excerpt", quote=quote, quote_verified=True, content_hash=fingerprint(quote), offset=0)
                   for i, quote in enumerate(quotes)]
        request = deepcopy(working)
        # Frozen test excerpts, not live websites. The original captured work
        # prompt/tool guidance remains present; no tool execution in this replay.
        request["messages"][-1]["content"] = request["messages"][-1]["content"].replace(
            "Привет! Как ты?", "Ответь только по полученным тестовым выдержкам. " + question)
        evidence = RunEvidence(sources=sources)
        request["messages"].insert(-1, {"role": "assistant", "_msg_id": "web-source-context", "content": evidence.source_context([])})
        cases.append({"id": f"source_{name}", "request": request, "sources": sources, "expected": expected})
    return cases


def replay_case(case: dict, seed: int, output: Path) -> dict:
    from app.application.code_agent.run_evidence import RunEvidence
    from app.infrastructure.llm import openai_compatible as provider

    request = deepcopy(case["request"])
    request["options"]["max_tokens"] = 1024  # bounded diagnostic; no production cap change
    real_post = provider.requests.post
    payloads = []

    def capture_post(url, **kwargs):
        kwargs["json"]["seed"] = seed  # diagnostic seed, outside production options
        payloads.append(deepcopy(kwargs["json"]))
        return real_post(url, **kwargs)

    started = time.perf_counter()
    first_answer_ms = None
    response: dict = {}
    error = None
    try:
        with patch.object(provider.requests, "post", side_effect=capture_post):
            for event in provider.chat_completion_event_stream(**request, timeout=90):
                if event["type"] == "delta" and first_answer_ms is None:
                    first_answer_ms = round((time.perf_counter() - started) * 1000, 2)
                if event["type"] == "message":
                    response = event["response"]
    except Exception as exc:
        error = str(exc)
    answer = str((response.get("message") or {}).get("content") or "")
    evidence = RunEvidence(sources=case.get("sources") or [])
    evidence.mark_sources_presented(request["messages"])
    result = {
        "case": case["id"], "seed": seed, "expected": case["expected"],
        "request_hash": fingerprint(payloads), "prefix_hash": fingerprint(request["messages"][0]),
        "prompt_chars": sum(len(str(m.get("content") or "")) for m in request["messages"]),
        "schema_chars": len(json.dumps(request.get("tools") or [], ensure_ascii=False)),
        "first_answer_ms": first_answer_ms, "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "response": response, "error": error, "citations": evidence.citations(answer),
        "semantic_review": "pending_human_review", "speech_review": "pending_human_review",
        "sources": case.get("sources") or [],
    }
    path = output / f"{case['id']}-{seed}.json"
    write_json(path, {"request_payloads": payloads, **result})
    print(json.dumps({"case": case["id"], "seed": seed, "ttft_ms": response.get("ttft_ms"),
                      "answer": answer, "error": error}, ensure_ascii=False), flush=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Replay against the configured LAN model; default captures only")
    parser.add_argument("--seeds", default="41,42,43")
    parser.add_argument("--compare-dry", action="store_true")
    parser.add_argument("--cases", help="Comma-separated case IDs; default all")
    parser.add_argument("--replay", type=Path, help="Reuse a saved cases.json without recapturing prompts")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ROOT / "backend"))
    from dotenv import load_dotenv
    load_dotenv(ROOT / "backend" / ".env.local", override=False)
    with tempfile.TemporaryDirectory(prefix="state-", dir=output) as data_dir:
        os.environ["ELIRA_DATA_DIR"] = data_dir
        os.environ["ELIRA_AGENT_RUNS_DIR"] = str(Path(data_dir) / "runs")
        os.environ["LOCAL_EMBED_ENABLED"] = "false"
        if not args.live:
            os.environ["LLAMA_SERVER_ENABLED"] = "false"
        from app.infrastructure.llm.openai_compatible import local_llm_config
        cfg = local_llm_config()
        if args.live and not cfg.enabled:
            raise RuntimeError("configured model provider is disabled")
        workspace = Path(data_dir) / "workspace"
        workspace.mkdir()
        cases = (json.loads(args.replay.read_text(encoding="utf-8")) if args.replay
                 else prepare_cases(workspace, cfg.model, args.compare_dry))
        if args.cases:
            selected = set(args.cases.split(","))
            unknown = selected - {case["id"] for case in cases}
            if unknown:
                parser.error(f"unknown cases: {sorted(unknown)}")
            cases = [case for case in cases if case["id"] in selected]
        write_json(output / "cases.json", cases)
        manifest = {"model": cfg.model, "base_url": cfg.base_url, "context_window": cfg.context_window,
                    "live": args.live, "cases": [case["id"] for case in cases],
                    "limitations": "Provider replay only; no tool execution, UI timing, or automatic semantic certification."}
        if args.live:
            import requests
            try:
                props_response = requests.get(cfg.base_url.removesuffix("/v1") + "/props", timeout=10)
                props_response.raise_for_status()
                props = props_response.json()
                manifest["server"] = {"default_generation_settings": props.get("default_generation_settings"),
                                      "chat_template_hash": fingerprint(props.get("chat_template")),
                                      "model_path": props.get("model_path")}
            except (requests.RequestException, ValueError) as exc:
                manifest["server_snapshot_error"] = str(exc)
        write_json(output / "manifest.json", manifest)
        if args.live:
            results = [replay_case(case, int(seed), output) for case in cases for seed in args.seeds.split(",")]
            write_json(output / "results.json", results)
            return int(any(result["error"] for result in results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
