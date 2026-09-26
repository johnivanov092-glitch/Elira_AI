"""Opt-in, frozen eight-task Qwen warmup using already accepted local packages.

prepare writes only a NEW output tree; it never runs Qwen or edits the MCP stand.
stage requires all three MCP acceptance phases and --confirm-idle, and copies the
existing shipment publication byte-for-byte into that isolated stand. run --live
uses the ordinary agent and its normal verified-outcome learning hook. No labels
are registered with the learner, and this harness never calls observe().

The business data are fictional acceptance fixtures. The executions, selected
skills and bound result_verify receipts must be real. Four frozen fresh goals
are reserved for skill_advisor_eval.py's separate first-decision diagnostic.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import nullcontext
import csv
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import traceback

from autonomy_mcp import acceptance_oracle as oracle
from autonomy_mcp.warehouse_fixture import make_truth, serve


PLATFORM = Path(__file__).resolve().parents[3]
SHIPMENT = "shipment-weekly-reconciliation"
SHIPMENT_CANDIDATE = "17144a8fa13d4dc38f18853e4d25f046"
SHIPMENT_REVISION = "2c84471be8b89867a30a9299eb13e9be4b21b258"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def production_snapshot() -> dict:
    # Reuse the acceptance controller's exact production boundary. Its module
    # shares a name with the fixture package, so load the file explicitly.
    path = Path(__file__).with_name("autonomy_mcp.py")
    spec = importlib.util.spec_from_file_location("warmup_mcp_acceptance_controller", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.production_snapshot()


# These alternatives affect the predeclared split only, never acceptance scores.
# Exactly one formulation per genuinely different scenario is retained.
CLOSINGS = (
    "Проверь точность результата и дай ссылки на готовые файлы.",
    "Перед ответом проверь готовые файлы и укажи их пути.",
    "В конце сообщи, где лежат проверенные результаты.",
    "Сначала выполни работу и проверку, затем перечисли созданные файлы.",
    "Проверь полученные данные и кратко сообщи результат.",
    "Заверши задачу проверкой файлов и коротким ответом с путями.",
    "Готовые результаты должны пройти проверку; укажи их расположение.",
    "Проверка расчётов обязательна, после неё сообщи пути к файлам.",
    "Выполни расчёты, проверь их и перечисли результаты в ответе.",
    "Дай краткий ответ после проверки выходных файлов.",
    "Сохрани проверенные результаты и укажи файлы в ответе.",
    "В ответе нужны пути к результатам и краткое подтверждение проверки.",
    "Убедись в правильности результатов перед заключительным ответом.",
    "Проверь полноту и точность, затем сообщи, какие файлы готовы.",
    "Закончи проверкой результата; в ответе перечисли готовые файлы.",
    "После выполнения проверь результат и сообщи расположение файлов.",
)


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def tree(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    result = {}
    for item in sorted(path.rglob("*")):
        if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
            raise ValueError(f"Fixture cannot contain a redirected path: {item}")
        if item.is_file() and "__pycache__" not in item.parts:
            result[item.relative_to(path).as_posix()] = oracle.file_hash(item)
    return result


def setup(root: Path, mcp_root: Path) -> None:
    """Call before importing app, in each fresh process."""
    sys.path.insert(0, str(PLATFORM / "backend"))
    from dotenv import load_dotenv
    load_dotenv(PLATFORM / "backend/.env.local", override=False)
    for name in ("temp", "config", "runs", "pip-cache"):
        (root / name).mkdir(parents=True, exist_ok=True)
    os.environ.update({"ELIRA_DATA_DIR": str(mcp_root / "data"),
        "ELIRA_AGENT_RUNS_DIR": str(root / "runs"), "ELIRA_CONFIG_ROOT": str(root / "config"),
        "ELIRA_PLATFORM_ROOT": str(PLATFORM), "ELIRA_RELEASE_ID": "isolated-skill-advisor-warmup",
        "LOCAL_EMBED_ENABLED": "false", "ELIRA_SKILL_ADVISOR_MODE": "shadow",
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
        "PIP_CACHE_DIR": str(root / "pip-cache"), "TEMP": str(root / "temp"),
        "TMP": str(root / "temp"), "TMPDIR": str(root / "temp")})
    sys.dont_write_bytecode = True


def csv_bytes(headers: list[str], rows: list[list[str]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(headers)
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def shipment_fixture(project: Path, control: Path, label: str, variant: int,
                     *, returns: bool = False, utf8: bool = False) -> dict:
    ledger = []
    for index in range(1, 19):
        state = "cancelled" if index % 5 == 0 else "returned" if returns or index % 4 == 0 else "completed"
        ledger.append((f"REF-{label}-{index:03d}", f"SKU-{index % 5:03d}",
                       Decimal(137 * index + variant * 19) / 1000, state))
    records = [*reversed(ledger), ledger[2], ledger[5], ledger[5]]
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, delimiter=";" if utf8 else "|", lineterminator="\r\n")
    writer.writerow(["Номер отправки", "Артикул", "Количество", "Состояние"] if utf8 else
                    ["DeliveryRef", "ProductCode", "Units", "State"])
    for ref, sku, amount, state in records:
        text = f"{amount:.3f}"
        writer.writerow([ref, sku, text.replace(".", ",") if utf8 else text,
                         {"completed": "отгружено", "returned": "возврат", "cancelled": "отменено"}[state] if utf8 else state])
    source = project / "input" / f"{label}.csv"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(stream.getvalue().encode("utf-8") if utf8 else b"\xff\xfe" + stream.getvalue().encode("utf-16-le"))
    normalized, totals, counts = [], defaultdict(Decimal), defaultdict(int)
    for ref, sku, amount, state in ledger:
        if state == "cancelled":
            continue
        signed = -amount if state == "returned" else amount
        normalized.append([ref, sku, f"{signed:.3f}"])
        totals[sku] += signed
        counts[sku] += 1
    outputs = {f"{label}/normalized.csv": csv_bytes(["shipment_id", "sku", "quantity"], sorted(normalized)),
               f"{label}/summary.csv": csv_bytes(["sku", "net_quantity", "shipment_count"],
                    [[sku, f"{totals[sku]:.3f}", str(counts[sku])] for sku in sorted(totals)])}
    for name, content in outputs.items():
        target = control / "expected" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return {"input": str(source), "input_sha256": oracle.file_hash(source),
            "expected": {"output/" + name: hashlib.sha256(content).hexdigest() for name, content in outputs.items()}}


def prepare(root: Path, mcp_root: Path, shipment_root: Path) -> int:
    if root.exists():
        raise FileExistsError("Prepare requires a new output path; never overwrite frozen cases")
    if not root.is_relative_to(PLATFORM / ".scratch") or root in {mcp_root, shipment_root}:
        raise ValueError("Warmup output must be its own directory under this repository's .scratch")
    source_manifest = oracle.read_json(mcp_root / "control/manifest.json")
    if not source_manifest or source_manifest.get("root") != str(mcp_root):
        raise ValueError("Expected an existing isolated MCP acceptance stand")
    root.mkdir(parents=True)
    # Preparation imports only normalization into a new empty local data root.
    setup(root, root / "prepare-runtime")
    from app.application.code_agent import skill_advisor

    scenarios = [
        ("ship-weekly", "shipment", "train", "Подготовь недельную сверку отгрузок поставщика по выгрузке: нужен очищенный реестр операций и итог по артикулам."),
        ("ship-returns", "shipment", "train", "Подготовь корректировочный реестр возвратов поставщику: отдели отменённые записи и посчитай уменьшение количества по каждому артикулу."),
        ("ship-repair", "shipment", "train", "Исправь ошибочные файлы normalized.csv и summary.csv в указанной папке результатов: в них потеряны возвраты и повторно учтены дубли. Разрешаю перезаписать оба файла, восстановив их по исходной выгрузке."),
        ("ship-batch", "shipment", "holdout", "Подготовь отдельную сверку для каждой из двух выгрузок поставщиков. Форматы файлов различаются; не объединяй операции разных выгрузок в один отчёт."),
        ("stock-snapshot", "mcp", "train", "Сними актуальный полный складской остаток через доступное подключение к демонстрационному складу. Нужен реестр всех товаров и отдельный список дефицита."),
        ("stock-purchase", "mcp", "train", "Подготовь список пополнения запасов по живым данным склада: заказывать нужно только недостающее до минимума количество, сохрани дробные единицы без округления. Полный складской срез сохрани как основание расчёта."),
        ("stock-repair", "mcp", "train", "Сохранённые stock.csv и shortages.csv устарели. Проверь остатки по действующему складскому подключению и исправь оба файла по актуальным данным; разрешаю их перезаписать."),
        ("stock-selection", "mcp", "holdout", "Проверь остатки выбранных товаров A-003 и Я-011 по живому складу. Сохрани отдельный selected.csv только с этими товарами, а также полный складской срез и список общего дефицита для сверки."),
    ]
    shipment_rules = (
        "Исходник UTF-16 LE с BOM и разделителем |: DeliveryRef,ProductCode,Units,State; "
        "completed означает отгрузку, returned — возврат, cancelled — отмену. "
        "Если файл UTF-8 с разделителем ;, поля Номер отправки,Артикул,Количество,Состояние; "
        "статусы отгружено,возврат,отменено, десятичный разделитель — запятая. "
        "Совпадающие дубли номера отправки учитывай один раз, отмены исключи, возвраты вычти. "
        "Для каждого входа запиши normalized.csv (shipment_id,sku,quantity) и summary.csv "
        "(sku,net_quantity,shipment_count). Сортировка строковая по shipment_id и sku; "
        "shipment_count считает уникальные операции, включая возвраты. Количества с точкой и ровно "
        "тремя десятичными знаками; CSV с запятой, UTF-8 без BOM, LF и финальный перевод строки. "
        "Входные файлы не изменяй."
    )
    stock_rules = (
        f"Склад: {source_manifest['base_url']}, описание API: {source_manifest['base_url']}/docs. "
        "Прочитай все страницы. output/stock.csv: sku,name,quantity,minimum; "
        "output/shortages.csv: sku,quantity,minimum,shortfall, только положительный дефицит. "
        "Дефицит max(minimum-quantity,0), количества в обычных единицах без округления. "
        "Сортировка по sku как строке, CSV с запятой, UTF-8 без BOM и LF."
    )
    cases = []
    for index, (case_id, category, split, goal) in enumerate(scenarios):
        project, control = root / "projects" / case_id, root / "control" / case_id
        (project / "output").mkdir(parents=True)
        control.mkdir(parents=True)
        evidence = []
        if category == "shipment":
            evidence.append(shipment_fixture(project, control, "main", index, returns=case_id == "ship-returns"))
            if case_id == "ship-batch":
                evidence.append(shipment_fixture(project, control, "second", index + 1, utf8=True))
            mapping = " ".join(f"Вход {item['input']} → папка {project / 'output' / Path(item['input']).stem}." for item in evidence)
            rules = shipment_rules + " " + mapping
        else:
            truth = make_truth(index + 1)
            oracle.write_json(control / "truth.json", truth)
            rules = stock_rules
            if case_id == "stock-selection":
                rules += " output/selected.csv имеет те же столбцы, что stock.csv, и только два запрошенных SKU."
        if case_id == "ship-repair":
            broken = project / "output/main"
            broken.mkdir()
            (broken / "normalized.csv").write_bytes(b"shipment_id,sku,quantity\nWRONG,SKU-000,99.000\n")
            (broken / "summary.csv").write_bytes(b"sku,net_quantity,shipment_count\nSKU-000,99.000,100\n")
        elif case_id == "stock-repair":
            (project / "output/stock.csv").write_bytes(b"sku,name,quantity,minimum\nA-001,stale,999,0\n")
            (project / "output/shortages.csv").write_bytes(b"sku,quantity,minimum,shortfall\n")
        pool = [f"{goal}\nРабочая папка: {project}\n{rules}\n{closing}" for closing in CLOSINGS]
        chosen = next((n for n, value in enumerate(pool) if
                       (int(skill_advisor._digest(skill_advisor._query(value))[:8], 16) % 5 == 0) == (split == "holdout")), None)
        if chosen is None:
            raise ValueError(f"Frozen formulation pool cannot meet the predeclared split: {case_id}; no Qwen was run")
        message = pool[chosen]
        request_hash = skill_advisor._digest(skill_advisor._query(message))
        case = {"id": case_id, "category": category, "split": split, "user_message": message,
                "normalized_request_hash": request_hash, "chosen_formulation": chosen,
                "formulation_pool_sha256": digest(pool), "shipment_inputs": evidence,
                "initial_project": tree(project), "control_files": tree(control)}
        oracle.write_json(control / "formulations.json", pool)
        cases.append(case)
    fresh = [
        {"id": "fresh-ship-close", "category": "shipment", "user_message": "Закрой период по новой выгрузке отгрузок: подготовь нормализованный реестр и сводку артикулов, исключи технические повторы и отмены, вычти возвраты с точностью до тысячной."},
        {"id": "fresh-ship-audit", "category": "shipment", "user_message": "Бухгалтерии нужен проверенный итог движения товаров из CSV поставщика: количество со знаком по артикулу и число уникальных отправок, возвраты тоже считаются операциями. Приложи отдельный реестр операций."},
        {"id": "fresh-stock-order", "category": "mcp", "user_message": "Рассчитай потребность закупки по действующему складу через доступное подключение. Собери все страницы остатков и минимальных запасов, сохрани полный срез и CSV с точным положительным дефицитом."},
        {"id": "fresh-stock-check", "category": "mcp", "user_message": "Нужно сверить обеспеченность склада по его API: выгрузи актуальные количества всех товаров, отдельно перечисли позиции ниже минимального запаса и недостающее количество в обычных единицах."},
    ]
    for case in fresh:
        case["user_message"] = (
            "Выбери подходящий сохранённый навык для следующей новой цели и загрузи его инструкции. "
            "Сейчас нужна только эта подготовка; саму цель пока выполнять не нужно.\n\n" + case["user_message"]
        )
    hashes = [case["normalized_request_hash"] for case in cases]
    hashes += [skill_advisor._digest(skill_advisor._query(case["user_message"])) for case in fresh]
    if len(set(hashes)) != 12:
        raise ValueError("Frozen goals collide after actual learner normalization")
    oracle.write_json(root / "control/cases.json", cases)
    oracle.write_json(root / "control/fresh-goals.json", fresh)
    manifest = {"created_utc": datetime.now(timezone.utc).isoformat(), "root": str(root),
                "mcp_root": str(mcp_root), "shipment_root": str(shipment_root),
                "mcp_port": source_manifest["port"], "base_url": source_manifest["base_url"],
                "cases": [case["id"] for case in cases], "thinking": False, "advisor_mode": "shadow",
                "split": "predeclared 3 train + 1 holdout per category; selected by normalized hash before inference",
                "fictional_business_data": True, "learning": "only normal completed-run verified-outcome hook",
                "frozen_control": tree(root / "control")}
    oracle.write_json(root / "manifest.json", manifest)
    print(json.dumps({"status": "prepared_without_live", "cases": 8, "fresh": 4,
                      "root": str(root), "stand_modified": False}), flush=True)
    return 0


def checked_manifest(root: Path) -> dict:
    manifest = oracle.read_json(root / "manifest.json")
    if not manifest or manifest["root"] != str(root) or tree(root / "control") != manifest["frozen_control"]:
        raise ValueError("Frozen case formulations, inputs or expected results changed")
    return manifest


def runtime_snapshot(root: Path, manifest: dict) -> dict:
    from app.application.code_agent import skill_development, task_skills
    from app.infrastructure.llm.openai_compatible import local_llm_config
    cfg = local_llm_config()
    catalog = task_skills.discover_skills()
    return {"sources": {str(path.relative_to(PLATFORM)): oracle.file_hash(path)
                        for path in sorted((PLATFORM / "backend/app").rglob("*.py"))},
            "harness_sha256": oracle.file_hash(Path(__file__)),
            "warehouse_fixture_sha256": oracle.file_hash(Path(serve.__code__.co_filename)),
            "oracle_sha256": oracle.file_hash(Path(oracle.__file__)),
            "environment_file_sha256": oracle.file_hash(PLATFORM / "backend/.env.local"),
            "model": cfg.model, "base_url": cfg.base_url, "context_window": cfg.context_window,
            "config_sha256": digest(tree(root / "config")),
            "catalog": catalog, "bindings": {item["name"]: task_skills._skill_binding(task_skills._read(item["name"]))
                                              for item in catalog["skills"]},
            "mcp_config_sha256": oracle.file_hash(Path(manifest["mcp_root"]) / "data/mcp_servers.json"),
            "active": oracle.read_json(skill_development.ROOT / "active.json", {})}


def cohort() -> dict:
    """Describe existing experience without deleting, importing or relabelling it."""
    from app.application.code_agent import skill_advisor
    samples = skill_advisor._samples()
    classes = {}
    for sample in samples:
        key = skill_advisor._class(sample["binding"])
        entry = classes.setdefault(key, {"binding": sample["binding"], "samples": 0,
                                         "train_groups": set(), "holdout_groups": set()})
        entry["samples"] += 1
        entry["holdout_groups" if sample["holdout"] else "train_groups"].add(sample["group"])
    return {"samples": len(samples), "sample_ids": sorted(sample["id"] for sample in samples),
            "classes": {key: {**value, "train_groups": sorted(value["train_groups"]),
                              "holdout_groups": sorted(value["holdout_groups"])} for key, value in classes.items()}}


def stage(root: Path, manifest: dict) -> int:
    mcp_root, shipment_root = Path(manifest["mcp_root"]), Path(manifest["shipment_root"])
    if (root / "stage.json").exists():
        raise FileExistsError("This warmup was already staged")
    phases = [oracle.read_json(mcp_root / f"phase-{phase}.json", {}) for phase in (1, 2, 3)]
    if not all(item.get("verification", {}).get("ok") is True for item in phases):
        raise ValueError("All three MCP phases must pass before the stand can be reused")
    if any(item.get("surviving_processes") or item.get("cleanup_errors") for item in phases):
        raise ValueError("MCP acceptance did not finish with clean owned-process shutdown")
    names = phases[-1]["verification"]["bound_packages"]
    if len(names) != 1:
        raise ValueError("Expected exactly one verified final MCP integration")
    if not oracle.read_json(shipment_root / "phase-3.json", {}).get("verification", {}).get("ok"):
        raise ValueError("Shipment acceptance has not passed its final reuse phase")
    setup(root, mcp_root)
    from app.application.code_agent import skill_development, skill_advisor
    from app.core.config import DATA_DIR
    if DATA_DIR != mcp_root / "data":
        raise RuntimeError("Warmup data isolation failed")
    for case in oracle.read_json(root / "control/cases.json"):
        request_hash = skill_advisor._digest(skill_advisor._query(case["user_message"]))
        if request_hash != case["normalized_request_hash"] or (
            (int(request_hash[:8], 16) % 5 == 0) != (case["split"] == "holdout")
        ):
            raise ValueError("Learner normalization changed after preparation; do not alter this frozen split")
    current = skill_development.active_package(names[0])
    expected = phases[-1]["validated_packages"][names[0]]["package"]
    if current != expected:
        raise ValueError("MCP package changed after its final verified reuse")
    destination = skill_development.ROOT
    source = shipment_root / "data/skill_development"
    source_active = oracle.read_json(source / "active.json", {})
    entry = source_active[SHIPMENT]
    if entry["candidate_id"] != SHIPMENT_CANDIDATE or entry["revision"] != SHIPMENT_REVISION:
        raise ValueError("Expected the exact accepted shipment-v2 publication")
    skill_development.ROOT = source
    try:
        source_package = skill_development.active_package(SHIPMENT)
    finally:
        skill_development.ROOT = destination
    active_path = destination / "active.json"
    active = oracle.read_json(active_path, {})
    if SHIPMENT in active:
        raise ValueError("Destination already has this shipment skill; no overwrite is allowed")
    transfer = [(source / "packages" / SHIPMENT, destination / "packages" / SHIPMENT),
                (source / "history" / SHIPMENT, destination / "history" / SHIPMENT)]
    candidates = [entry["candidate_id"]]
    if entry.get("previous"):
        candidates.append(entry["previous"]["candidate_id"])
    transfer += [(source / "receipts" / f"{candidate}.json", destination / "receipts" / f"{candidate}.json")
                 for candidate in candidates]
    if any(target.exists() or not origin.exists() for origin, target in transfer):
        raise ValueError("Publication copy would overwrite files or lacks source evidence")
    copied = {}
    for origin, target in transfer:
        target.parent.mkdir(parents=True, exist_ok=True)
        if origin.is_dir():
            before = tree(origin)
            shutil.copytree(origin, target)
            if tree(target) != before or tree(origin) != before:
                raise RuntimeError("Publication copy is not byte-identical")
            copied[str(origin)] = before
        else:
            before = oracle.file_hash(origin)
            shutil.copy2(origin, target)
            if oracle.file_hash(target) != before or oracle.file_hash(origin) != before:
                raise RuntimeError("Publication receipt copy changed")
            copied[str(origin)] = before
    # Keep the original receipts/history; this is migration, not a new skill
    # publication or a synthetic learning observation.
    oracle.write_json(root / "shipment-copy.json", {"copied": copied, "source_package": source_package,
                                                   "destination_active_before": active})
    skill_development.validated_package(SHIPMENT, SHIPMENT_CANDIDATE, expected=entry)
    active[SHIPMENT] = entry
    temporary = destination / "active.warmup.tmp"
    if temporary.exists():
        raise FileExistsError("Unfinished warmup staging exists")
    oracle.write_json(temporary, active)
    os.replace(temporary, active_path)
    snapshot = runtime_snapshot(root, manifest)
    category_names = {"shipment": SHIPMENT, "mcp": names[0]}
    fresh = oracle.read_json(root / "control/fresh-goals.json")
    oracle.write_json(root / "fresh-cases.json", [{**case, "expected_skills": [category_names[case["category"]]]} for case in fresh])
    oracle.write_json(root / "stage.json", {"snapshot": snapshot, "category_names": category_names,
        "mcp_server_ids": [item["id"] for item in oracle.read_json(mcp_root / "data/mcp_servers.json")["servers"]],
        "accepted_phase_hashes": [oracle.file_hash(mcp_root / f"phase-{n}.json") for n in (1, 2, 3)],
        "advisor_before": skill_advisor.status(), "advisor_files_before": tree(mcp_root / "data/skill_advisor"),
        "original_cohort": cohort(),
        "fresh_cases_sha256": oracle.file_hash(root / "fresh-cases.json")})
    print(json.dumps({"status": "staged_without_live", "category_names": category_names}), flush=True)
    return 0


def verify_case(root: Path, case: dict, stage_info: dict, summary: dict) -> dict:
    project, control = root / "projects" / case["id"], root / "control" / case["id"]
    checks = {"completed": summary.get("done", {}).get("answer_status") == "complete"
              and summary.get("done", {}).get("ok") is True and not summary.get("exception")}
    if case["category"] == "shipment":
        for item in case["shipment_inputs"]:
            checks["input:" + Path(item["input"]).name] = oracle.file_hash(Path(item["input"])) == item["input_sha256"]
            for relative, expected in item["expected"].items():
                output = project / relative
                checks[relative] = output.is_file() and oracle.file_hash(output) == expected
    else:
        truth = oracle.read_json(control / "truth.json")
        checks["stock"] = oracle.verify_csv(project / "output/stock.csv", truth, False)["ok"]
        checks["shortages"] = oracle.verify_csv(project / "output/shortages.csv", truth, True)["ok"]
        if case["id"] == "stock-selection":
            checks["selected"] = oracle.verify_csv(project / "output/selected.csv",
                [row for row in truth if row["sku"] in {"A-003", "Я-011"}], False)["ok"]
        events = [json.loads(line) for line in (root / "results" / case["id"] / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        calls = [call for call in oracle.tool_windows(events, stage_info["mcp_server_ids"]) if call["ok"]]
        audit = [json.loads(line) for line in (root / "results" / case["id"] / "http.jsonl").read_text(encoding="utf-8").splitlines()]
        pages = {row["page_index"] for row in audit if row["status"] == 200 and row["page_index"] is not None
                 and any(call["start"] <= row["time_ns"] <= call["end"] for call in calls)}
        checks["native_mcp_all_pages"] = bool(calls) and pages == set(range((len(truth) + 2) // 3))
    expected_name = stage_info["category_names"][case["category"]]
    checks["selected_exact_version"] = summary.get("skill_binding") == stage_info["snapshot"]["bindings"][expected_name]
    learning = summary.get("learning") or {}
    checks["bound_learning_receipt"] = summary.get("learning_evidence_current") is True and learning.get("ok") is True \
        and learning.get("status") in {"learned", "recorded", "duplicate"}
    from app.application.code_agent import skill_advisor
    sample = next((row for row in skill_advisor._samples() if row["id"] == learning.get("sample_id")), None)
    checks["persisted_observation_identity"] = bool(sample and sample["run_id"] == summary.get("run_id")
        and sample["request_hash"] == case["normalized_request_hash"]
        and sample["holdout"] == (case["split"] == "holdout")
        and sample["binding"] == summary.get("skill_binding"))
    checks["clean_shutdown"] = not summary.get("cleanup_errors") and not summary.get("surviving_processes")
    checks["frozen_state"] = summary.get("frozen_state_unchanged") is True
    checks["production_unchanged"] = summary.get("production_unchanged") is True
    return {"ok": all(checks.values()), "checks": checks}


def worker(root: Path, manifest: dict, case: dict) -> int:
    mcp_root = Path(manifest["mcp_root"])
    setup(root, mcp_root)
    from app.application.code_agent.agent_loop import request_cancel, stream_code_agent
    from app.application.code_agent.run_journal import RunJournal
    from app.application.code_agent.task_outcomes import TaskOutcome
    from app.application.code_agent.tools import _run, _shell
    from app.application.tool_providers import lsp_runtime, mcp_runtime
    from app.infrastructure.llm.openai_compatible import local_llm_config
    from app.core.config import DATA_DIR
    if DATA_DIR != mcp_root / "data" or mcp_runtime.CONFIG_PATH != mcp_root / "data/mcp_servers.json":
        raise RuntimeError("Warmup runtime isolation failed")
    cfg = local_llm_config()
    if not cfg.enabled:
        raise RuntimeError("Configured Qwen provider is disabled")
    run_id = "advisor-warmup-" + case["id"]
    result = root / "results" / case["id"]
    if (result / "summary.json").exists():
        raise FileExistsError("A warmup case must not be replayed or overwritten")
    project = root / "projects" / case["id"]
    summary = {"case_id": case["id"], "run_id": run_id, "thinking": False, "advisor_mode": "shadow",
               "history_messages": 0, "cleanup_errors": [], "started_utc": datetime.now(timezone.utc).isoformat()}
    instructions = (f"Рабочая область задачи: {project}. Служебные данные и уже сохранённые возможности "
                    f"изолированы в {mcp_root / 'data'}. Работай только с этой областью и этими пакетами, "
                    "не меняй действующее приложение или другие проекты. Входные файлы не изменяй. "
                    "Выполни задачу самостоятельно без уточняющих вопросов.")
    started, finished = time.monotonic(), threading.Event()

    def monitor() -> None:
        while not finished.wait(0.5):
            if ((root / "STOP").exists() or time.monotonic() - started > 600) and request_cancel(run_id):
                summary["cancel_reason"] = "operator_stop" if (root / "STOP").exists() else "warmup_wall_timeout"
                return

    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    try:
        with (result / "events.jsonl").open("x", encoding="utf-8", newline="\n") as output:
            for event in stream_code_agent(user_message=case["user_message"], task_instructions=instructions,
                    project_root=project, working_dir=project, model=cfg.model, run_id=run_id,
                    session_id=run_id, conversation_history=[], auto_remember=False,
                    permission_mode="bypass", thinking=False):
                output.write(json.dumps({**event, "observed_ns": time.time_ns()}, ensure_ascii=False, default=str) + "\n")
                output.flush()
                if event.get("type") in {"done", "final_response", "error"}:
                    summary[event["type"]] = event
    except Exception as exc:
        summary.update(exception=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc())
    finally:
        finished.set()
        thread.join(timeout=2)
        try:
            state = RunJournal.load(run_id).state
            outcome = TaskOutcome(state.get("task_outcome"))
            evidence = outcome.learning_evidence(int(state.get("code_input_epoch") or 0))
            summary.update(learning=state.get("skill_advisor_learning"), skill_binding=outcome.skill_binding(),
                           learning_evidence_current=evidence is not None, learning_evidence=evidence)
        except Exception as exc:
            summary["journal_error"] = str(exc)
        for label, cleanup in (("mcp", mcp_runtime.stop_all_servers), ("lsp", lsp_runtime.stop_all_servers),
                               ("server", _run.stop_all_servers), ("run", lambda: _shell.kill_run_processes(run_id))):
            try:
                cleanup()
            except Exception as exc:
                summary["cleanup_errors"].append({"runtime": label, "error": str(exc)})
        summary["elapsed_seconds"] = round(time.monotonic() - started, 3)
        oracle.write_json(result / "summary.json", summary)
    return int(bool(summary.get("exception")))


def run(root: Path, manifest: dict, selected: str) -> int:
    import psutil
    stage_info = oracle.read_json(root / "stage.json")
    if not stage_info:
        raise ValueError("Stage the accepted packages first")
    setup(root, Path(manifest["mcp_root"]))
    from app.application.code_agent import skill_advisor
    from app.application.tool_providers import mcp_runtime
    cases = oracle.read_json(root / "control/cases.json")
    if selected != "all" and selected not in manifest["cases"]:
        raise ValueError("Unknown frozen case")
    if any(item["status"] != "stopped" for item in mcp_runtime.list_servers()):
        raise ValueError("Unexpected connected MCP process before warmup")
    for case in cases:
        if selected not in {"all", case["id"]}:
            continue
        checked_manifest(root)
        if (root / "STOP").exists():
            raise RuntimeError("Operator STOP is present")
        if runtime_snapshot(root, manifest) != stage_info["snapshot"]:
            raise RuntimeError("Frozen code, configuration or accepted package identity changed")
        project, result = root / "projects" / case["id"], root / "results" / case["id"]
        if result.exists() or tree(project) != case["initial_project"]:
            raise RuntimeError("Case was already attempted or its input workspace changed")
        result.mkdir(parents=True)
        protected = production_snapshot()
        server = serve(manifest["mcp_port"], {3: oracle.read_json(root / "control" / case["id"] / "truth.json")},
                       result / "http.jsonl") if case["category"] == "mcp" else nullcontext(None)
        tracked, survivors = {}, []
        with server as fixture, (result / "worker.log").open("xb") as log:
            if fixture is not None:
                fixture.phase = 3
            child = subprocess.Popen([sys.executable, str(Path(__file__)), "worker", "--root", str(root),
                                      "--case", case["id"], "--live"], cwd=project, stdout=log, stderr=subprocess.STDOUT,
                                     creationflags=CREATE_NO_WINDOW)
            process, started = psutil.Process(child.pid), time.monotonic()
            tracked[(process.pid, process.create_time())] = process
            try:
                while child.poll() is None:
                    try:
                        for descendant in process.children(recursive=True):
                            tracked[(descendant.pid, descendant.create_time())] = descendant
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                    if time.monotonic() - started > 660:
                        break
                    time.sleep(0.25)
            finally:
                for (pid, created), descendant in reversed(list(tracked.items())):
                    try:
                        if descendant.is_running() and descendant.create_time() == created:
                            survivors.append({"pid": pid, "created": created, "name": descendant.name()})
                            descendant.terminate()
                            try:
                                descendant.wait(timeout=5)
                            except psutil.TimeoutExpired:
                                if descendant.is_running() and descendant.create_time() == created:
                                    descendant.kill()
                                    descendant.wait(timeout=5)
                    except psutil.NoSuchProcess:
                        pass
                    except (psutil.AccessDenied, psutil.TimeoutExpired) as exc:
                        survivors.append({"pid": pid, "cleanup_error": str(exc)})
        summary = oracle.read_json(result / "summary.json", {"exception": "worker did not save a summary"})
        summary.update(surviving_processes=survivors, worker_exit_code=child.poll(),
                       frozen_state_unchanged=runtime_snapshot(root, manifest) == stage_info["snapshot"],
                       production_unchanged=production_snapshot() == protected)
        try:
            summary["verification"] = verify_case(root, case, stage_info, summary)
        except Exception as exc:
            summary["verification"] = {"ok": False, "oracle_error": str(exc)}
        oracle.write_json(result / "summary.json", summary)
        oracle.write_json(result / "protected-before.json", protected)
        oracle.write_json(result / "protected-after.json", production_snapshot())
        print(json.dumps({"case_id": case["id"], "verification": summary["verification"],
                          "learning": summary.get("learning"), "seconds": summary.get("elapsed_seconds")}), flush=True)
        if not summary["frozen_state_unchanged"] or not summary["production_unchanged"] or survivors:
            raise RuntimeError("State drift or surviving owned processes; preserve evidence and stop")
    results = {case["id"]: oracle.read_json(root / "results" / case["id"] / "summary.json", {}) for case in cases}
    complete = all(value.get("verification", {}).get("ok") is True for value in results.values())
    status = skill_advisor.status()
    final_cohort = cohort()
    preserved = set(stage_info["original_cohort"]["sample_ids"]) <= set(final_cohort["sample_ids"])
    complete = complete and preserved
    verdict = {"all_eight_verified": complete, "advisor": status,
               "original_cohort_preserved": preserved,
               "original_cohort": stage_info["original_cohort"], "final_cohort": final_cohort,
               "status": "ready_for_fresh_first_decision_diagnostic" if complete and status.get("model_version")
                         else "insufficient_evidence" if complete else "incomplete_or_invalid_warmup",
               "cases": {key: value.get("verification") for key, value in results.items()},
               "limits": "Eight controlled real executions on fictional fixtures, not broad effectiveness evidence; do not adapt cases or thresholds."}
    oracle.write_json(root / "warmup-summary.json", verdict)
    print(json.dumps(verdict), flush=True)
    return int(not complete)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "stage", "run", "worker"))
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--mcp-root", type=Path)
    parser.add_argument("--shipment-root", type=Path)
    parser.add_argument("--confirm-idle", action="store_true", help="Operator has coordinated reuse with the MCP acceptance owner")
    parser.add_argument("--live", action="store_true", help="Explicitly opt into real Qwen tasks after obtaining its slot")
    parser.add_argument("--case", default="all")
    args = parser.parse_args()
    root = args.root.resolve()
    if args.action == "prepare":
        if not args.mcp_root or not args.shipment_root:
            parser.error("prepare requires --mcp-root and --shipment-root")
        return prepare(root, args.mcp_root.resolve(), args.shipment_root.resolve())
    manifest = checked_manifest(root)
    if args.action == "stage":
        if not args.confirm_idle:
            parser.error("stage requires --confirm-idle after coordinating with the MCP acceptance owner")
        return stage(root, manifest)
    if args.action == "worker":
        if not args.live:
            parser.error("worker also requires --live")
        cases = oracle.read_json(root / "control/cases.json")
        case = next((case for case in cases if case["id"] == args.case), None)
        if case is None or not (root / "stage.json").exists():
            parser.error("worker requires one staged case")
        return worker(root, manifest, case)
    if not args.live:
        parser.error("run requires --live; prepare/stage do not run Qwen")
    lock = root / "driver.lock"
    with lock.open("x", encoding="ascii") as handle:
        handle.write(str(os.getpid()))
    try:
        return run(root, manifest, args.case)
    finally:
        lock.unlink()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"status": "warmup_error", "error": f"{type(exc).__name__}: {exc}"}), flush=True)
        raise SystemExit(1)
