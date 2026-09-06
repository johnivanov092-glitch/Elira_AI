"""Opt-in first-decision skill diagnostic using the real public prompt builder.

Captures every request before replay; proposed tools are NEVER executed.
Use the ordinary UI for the separate end-to-end execution check.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[3]
CASES = [
    ("conversation", "Привет, как настроение?", []),
    ("diagnostics", "Приложение стало зависать после обновления. Начни диагностику причины регрессии.", ["diagnostics"]),
    ("code-change", "Проведи ревью изменений в подключённом репозитории и найди ошибки контрактов.", ["code-change"]),
    ("git-release", "Проверь состояние git и подготовь merge текущей ветки в main; пока только подготовка без push.", ["git-release"]),
    ("windows-admin", "На Windows служба приложения падает при запуске. Начни диагностику службы и событий, пока без изменений.", ["windows-admin", "diagnostics"]),
    ("linux-admin", "На Linux сервере приложение не стартует как служба. Начни диагностику через сохранённое SSH подключение, пока без изменений.", ["linux-admin", "diagnostics"]),
    ("network-dns-tls", "Сайт открывается по IP, но выдаёт ошибку по доменному имени. Начни диагностику DNS и TLS.", ["network-dns-tls", "diagnostics"]),
    ("containers-deploy", "Подготовь обновление контейнерного приложения по compose в подключённом проекте с проверкой health и откатом.", ["containers-deploy"]),
    ("sql-migrations", "Подготовь миграцию схемы БД проекта с сохранением данных и проверкой восстановления, пока не применяй.", ["sql-migrations"]),
    ("python", "Исправь обработку таймаута в Python-коде подключённого проекта и проверь тестами.", ["python", "code-change", "diagnostics"]),
    ("java", "Исправь утечку потоков в Java-проекте и проверь сборку соответствующего модуля.", ["java", "code-change", "diagnostics"]),
    ("javascript-typescript", "Исправь гонку fetch-запросов в TypeScript-компоненте проекта, проверь отмену и тесты.", ["javascript-typescript", "code-change", "diagnostics"]),
    ("rust", "Исправь обработку ошибок Rust-модуля в Cargo workspace и проверь тесты.", ["rust", "code-change", "diagnostics"]),
]


def classify(response: dict, expected: list[str]) -> dict:
    calls = (response.get("message") or {}).get("tool_calls") or []
    selected = []
    for call in calls:
        function = call.get("function") or {}
        args = function.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                continue
        if not isinstance(args, dict):
            continue
        if function.get("name") == "runtime_control" and args.get("operation") == "skill_load":
            selected.append(args.get("name"))
    return {"selected": selected, "selection_status":
        ("matched" if set(selected) & set(expected) else "deferred_or_missed") if expected
        else ("unexpected_skill" if selected else "no_skill_needed")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cases", help="Comma-separated IDs; default all 13")
    parser.add_argument("--seed", type=int, default=41)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ROOT / "backend"))
    from dotenv import load_dotenv
    load_dotenv(ROOT / "backend" / ".env.local", override=False)
    from answer_contract_eval import replay_case, write_json

    chosen = args.cases.split(",") if args.cases else [case[0] for case in CASES]
    if set(chosen) - {case[0] for case in CASES}:
        parser.error("Unknown case ID")
    with tempfile.TemporaryDirectory(prefix="state-", dir=output) as directory:
        os.environ["ELIRA_DATA_DIR"] = directory
        os.environ["ELIRA_AGENT_RUNS_DIR"] = str(Path(directory) / "runs")
        os.environ["LOCAL_EMBED_ENABLED"] = "false"
        if not args.live:
            os.environ["LLAMA_SERVER_ENABLED"] = "false"
        from app.application.code_agent.agent_loop import stream_code_agent
        from app.infrastructure.llm.openai_compatible import local_llm_config
        config = local_llm_config()
        if args.live and not config.enabled:
            raise RuntimeError("Local model provider is disabled")
        requests, results = [], []
        for case_id, prompt, expected in CASES:
            if case_id not in chosen:
                continue
            captured = {}
            def capture(**kwargs):
                if not captured:
                    captured.update(deepcopy({key: value for key, value in kwargs.items() if key != "options"}))
                    captured["options"] = {key: deepcopy(value) for key, value in kwargs["options"].items() if not key.startswith("_")}
                return {"message": {"content": "Captured.", "tool_calls": []}}
            list(stream_code_agent(user_message=prompt, memory_query=prompt, project_root=directory,
                 model=config.model, chat_fn=capture, auto_remember=False, num_ctx=32768))
            if not captured:
                raise RuntimeError(f"No model request captured: {case_id}")
            case = {"id": case_id, "request": captured, "expected": expected}
            requests.append(case)
            write_json(output / "cases.json", requests)
            if args.live:
                result = replay_case(case, args.seed, output)
                result.update(classify(result.get("response") or {}, expected))
                results.append(result)
                write_json(output / "results.json", results)
                print(json.dumps({"case": case_id, **classify(result.get("response") or {}, expected)}, ensure_ascii=False), flush=True)
        write_json(output / "manifest.json", {"model": config.model, "seed": args.seed, "live": args.live,
            "cases": chosen, "limitation": "First model decision only; tools not executed; deferred is not a routing failure verdict."})
        return int(any(result.get("error") for result in results))


if __name__ == "__main__":
    raise SystemExit(main())
