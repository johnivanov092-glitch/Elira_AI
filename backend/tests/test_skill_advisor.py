from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from app.application.code_agent import skill_advisor as advisor


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def binding(name: str, version: str = "first") -> dict:
    return {"name": name, "identity": {"sha256": digest(name + version)}}


def evidence(name: str, number: int = 0, version: str = "first") -> dict:
    return {"provenance": "observed_verification", "skill_binding": binding(name, version),
            "input_version": digest(f"inputs-{number}"),
            "targets": [{"path": "not-opened-by-learner.csv", "sha256": digest(f"target-{number}")}],
            "reports": [{"path": "not-opened-by-learner.json", "sha256": digest(f"report-{number}")} ]}


@pytest.fixture
def store(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(advisor, "ROOT", tmp_path / "advisor")
    return advisor.ROOT


CATALOG = [{"name": "audio-transcribe", "description": "Saved audio procedure"},
           {"name": "table-reconcile", "description": "Saved table procedure"},
           {"name": "new-image-skill", "description": "A new untrained capability"}]
IDENTITIES = {item["name"]: binding(item["name"]) for item in CATALOG}
CONTEXT = {"platform": "windows", "compute": "local_gpu"}


# Synthetic fixtures exercise mechanics only. They are not evidence of quality
# on real user tasks; independent formulations, not numbered copies, form splits.
AUDIO_TASKS = [
    "Расшифруй аудио запись голоса в русский текст",
    "Сохрани аудио как текст и проверь окончания фраз",
    "Расшифруй запись разговора с временными метками",
    "Из аудио подготовь текст интервью без пропущенных реплик",
    "Переведи запись речи в текст и сохрани сегменты",
    "Проверь аудио целиком и расшифруй голос диктора",
    "Расшифруй голос на записи включая тихие фразы",
    "Подготовь текст лекции из аудио с разбивкой на сегменты",
    "Для записи встречи получи текст речи каждого участника",
    "Расшифруй аудио с шумом и отметь неразборчивую речь",
    "Сделай текст аудио заметки и проверь начало записи",
    "Расшифруй голосовое сообщение сохрани исходную речь",
    "Выдели речь из записи и подготовь текст субтитров",
    "Расшифруй запись интервью и проверь временные метки",
    "Прочитай аудио файл и сохрани текст речи локально",
    "Расшифруй долгую запись с разделением на части",
    "Извлеки текст речи из аудио без перевода языка",
    "Для аудио звонка сохрани текст и границы фраз",
]
TABLE_TASKS = [
    "Сверь таблицу строки суммы и колонки исходных данных",
    "Проверь суммы в таблице и найди повторяющиеся строки",
    "Сопоставь колонки таблицы с образцом и сохрани результат",
    "Объедини таблицу по ключу сохрани все строки",
    "Нормализуй колонки таблицы и проверь числовые суммы",
    "Сравни строки двух таблиц и выпиши расхождения",
    "Проверь таблицу закупок пересчитай итоговые суммы",
    "Сохрани таблицу с правильными колонками без потери строк",
    "Удали дублирующиеся строки таблицы и проверь суммы",
    "Сверь таблицу остатков по колонкам товара и количества",
    "Проверь строки таблицы на пустые обязательные колонки",
    "Подготовь сводную таблицу и сверку исходных сумм",
    "Разбери таблицу с разделителем и проверь каждую строку",
    "Исправь типы колонок таблицы сохрани денежные суммы",
    "Сопоставь строки таблицы продаж с реестром оплат",
    "Проверь таблицу после объединения по колонке артикула",
    "Преобразуй таблицу в нормализованные строки с проверкой",
    "Сверь таблицу отчёта по суммам и названиям колонок",
]


def populate() -> list[tuple[str, dict, str]]:
    recorded = []
    for number, tasks in enumerate(zip(AUDIO_TASKS, TABLE_TASKS)):
        for name, query in zip(("audio-transcribe", "table-reconcile"), tasks):
            proof, run = evidence(name, number), f"run-{name}-{number}"
            result = advisor.observe(query, proof, run, CONTEXT)
            assert result["status"] in {"learned", "recorded"}, result
            recorded.append((query, proof, run))
    return recorded


def test_learns_weights_on_grouped_holdout_keeps_unknown_and_new_skills_unselected(store):
    assert advisor.advise("расшифруй аудио", CATALOG, IDENTITIES)["status"] == "cold_start"
    populate()
    state = advisor.status()
    assert state["status"] == "ready"
    assert state["evaluation"]["holdout_groups"] >= 2
    assert state["evaluation"]["candidate_mrr"] > state["evaluation"]["baseline_mrr"]
    catalog_before = copy.deepcopy(CATALOG)
    for query, wanted in (("расшифруй запись голос", "audio-transcribe"), ("таблицу суммы колонки", "table-reconcile")):
        result = advisor.advise(query, CATALOG, IDENTITIES, CONTEXT)
        assert result["status"] == "ready", result
        assert result["recommendations"][0]["name"] == wanted
        assert result["score_kind"] == "relative_log_likelihood_not_probability"
        assert "new-image-skill" not in [item["name"] for item in result["recommendations"]]
    assert CATALOG == catalog_before  # Adviser never prunes or rewrites discovery.
    unknown = advisor.advise("квазары астрофизика интерферометр", CATALOG, IDENTITIES, CONTEXT)
    assert unknown["status"] == "abstained" and unknown["recommendations"] == []

    dataset = json.loads((store / "dataset.json").read_text())
    splits = {}
    for row in dataset["samples"]:
        splits.setdefault(row["group"], set()).add(row["holdout"])
    assert all(len(value) == 1 for value in splits.values())
    model = json.loads((store / "models" / f"{state['active']}.json").read_text())
    classes = list(model["classes"].values())
    assert len(classes) == 2
    feature = next(iter(advisor.features("расшифруй")))
    learned = {item["binding"]["name"]: item["weights"].get(feature, item["default_weight"]) for item in classes}
    assert learned["audio-transcribe"] > learned["table-reconcile"]
    # Raw task/checker text and paths do not enter learned storage.
    assert "расшифруй" not in (store / "dataset.json").read_text(encoding="utf-8")
    assert "not-opened-by-learner" not in (store / "dataset.json").read_text()


def test_paths_urls_and_numeric_ids_do_not_create_independent_holdout_or_votes(store):
    queries = [
        r'Сверь таблицу "D:\Private Folder\report-123.csv" задача 17',
        "Сверь таблицу /home/local/report-456.csv задача 42",
        "Сверь таблицу https://example.invalid/files/report-789.csv?input=103 задача 90",
    ]
    for number, query in enumerate(queries):
        assert advisor.observe(query, evidence("table-reconcile", number), f"replay-{number}")["ok"]
    rows = json.loads((store / "dataset.json").read_text())["samples"]
    assert len({row["group"] for row in rows}) == 1
    assert len({row["holdout"] for row in rows}) == 1
    assert rows[0]["features"] == rows[1]["features"] == rows[2]["features"]
    state = advisor.status()
    assert state["evaluation"]["train_groups"] + state["evaluation"]["holdout_groups"] == 1
    assert state["model_version"] is None


def test_exact_version_drift_and_new_class_remain_advisory(store):
    populate()
    changed = copy.deepcopy(IDENTITIES)
    changed["audio-transcribe"] = binding("audio-transcribe", "new-unseen-version")
    result = advisor.advise("расшифруй аудио запись голос", CATALOG, changed, CONTEXT)
    assert result["recommendations"] == []  # No result from the obsolete version.
    result = advisor.advise("рисунок изображение", CATALOG, IDENTITIES, CONTEXT)
    assert result["status"] == "abstained"
    # A new class is recorded without rebuilding a predeclared class registry.
    seen = advisor.observe("рисунок изображение проверка", evidence("new-image-skill"), "new-class", CONTEXT)
    assert seen["status"] in {"learned", "recorded"}


def test_missing_failed_or_network_evidence_never_becomes_negative_training(store):
    bad = evidence("audio-transcribe")
    bad["provenance"] = "network_error"
    assert advisor.observe("расшифруй аудио", bad, "network")["status"] == "unavailable"
    bad = evidence("audio-transcribe")
    bad["status"] = "failed"
    assert advisor.observe("расшифруй аудио", bad, "failed")["status"] == "unavailable"
    assert advisor.observe("расшифруй аудио", {"ok": True}, "done-ok")["status"] == "unavailable"
    assert not (store / "dataset.json").exists()


def test_idempotent_publish_and_rollback_do_not_reactivate_on_resume(store):
    examples = populate()
    before = advisor.status()
    previous = before["previous"]
    assert previous and previous != before["active"]
    dataset_bytes = (store / "dataset.json").read_bytes()
    query, proof, run = examples[-1]
    assert advisor.observe(query, proof, run, CONTEXT)["status"] == "duplicate"
    assert (store / "dataset.json").read_bytes() == dataset_bytes
    assert advisor.status()["active"] == before["active"]
    assert advisor.rollback(previous)["status"] == "rolled_back"
    assert advisor.observe(query, proof, run, CONTEXT)["status"] == "duplicate"
    assert advisor.status()["active"] == previous
    assert advisor.rollback("../anything")["status"] == "unavailable"


def test_corrupt_active_model_and_dataset_fall_back_without_repairing_user_data(store):
    examples = populate()
    state = advisor.status()
    model_path = store / "models" / f"{state['active']}.json"
    model_path.write_text('{"broken":true}', encoding="utf-8")
    result = advisor.advise("расшифруй аудио", CATALOG, IDENTITIES)
    assert result["status"] == "unavailable" and result["recommendations"] == []
    query, proof, _ = examples[0]
    dataset_before = (store / "dataset.json").read_bytes()
    assert advisor.observe(query, proof, "another-run", CONTEXT)["status"] == "unavailable"
    assert (store / "dataset.json").read_bytes() == dataset_before
    assert model_path.read_text() == '{"broken":true}'
    assert advisor.rollback(state["previous"])["status"] == "rolled_back"
    (store / "dataset.json").write_text('{"samples":[]}', encoding="utf-8")
    assert advisor.observe(query, proof, "after-corruption", CONTEXT)["status"] == "unavailable"
    assert (store / "dataset.json").read_text() == '{"samples":[]}'


def test_nonblocking_thread_and_process_lock_leave_no_partial_publish(store):
    proof = evidence("audio-transcribe")
    with advisor._locked():
        outcomes = []
        thread = threading.Thread(target=lambda: outcomes.append(advisor.observe("расшифруй аудио", proof, "parallel")))
        thread.start()
        thread.join(timeout=3)
        assert not thread.is_alive()
        assert outcomes == [{"ok": False, "status": "busy", "model_version": None, "reason": "advisor_store_locked"}]
        # Separate interpreter must respect the same OS lock, not just threading.
        script = """
import json,sys
from pathlib import Path
from app.application.code_agent import skill_advisor as a
a.ROOT=Path(sys.argv[1])
print(json.dumps(a.observe('расшифруй аудио',json.loads(sys.argv[2]),'child')))
"""
        child = subprocess.run([sys.executable, "-c", script, str(store), json.dumps(proof)],
                               capture_output=True, text=True, timeout=10,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                               env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])})
        assert child.returncode == 0, child.stderr
        assert json.loads(child.stdout)["status"] == "busy"
    assert not (store / "dataset.json").exists()
    assert advisor.observe("расшифруй аудио", proof, "after-lock")["status"] == "recorded"


def test_unvalidated_candidate_cannot_be_activated_by_rollback(store):
    result = advisor.observe("расшифруй аудио", evidence("audio-transcribe"), "only-example")
    assert result["status"] == "recorded" and result["model_version"] is None
    assert advisor.rollback(result["candidate_version"])["status"] == "unavailable"
    assert advisor.status()["active"] is None


def test_proven_failure_recovery_and_resume_are_one_case_and_shadow_only(store):
    populate()
    active = advisor.status()["active"]
    query = "Сверь таблицу суммы и строки исходных данных"
    proof = {**evidence("table-reconcile"), "outcome": "requirement_failure", "execution_ok": True,
             "case_id": digest("case"), "requirement_ids": ["req-sums"]}
    failed = advisor.observe(query, proof, "recovering", CONTEXT)
    assert failed["ok"] is True
    before = (store / "dataset.json").read_bytes()
    replay = {**proof, "reports": [{"sha256": digest("fresh checker timestamp")}]}
    assert advisor.observe(query, replay, "recovering", CONTEXT)["status"] == "duplicate"
    assert (store / "dataset.json").read_bytes() == before
    success = {**evidence("table-reconcile", 3), "outcome": "verified_success",
               "case_id": proof["case_id"], "requirement_ids": ["req-sums"]}
    assert advisor.observe(query, success, "recovering", CONTEXT)["ok"] is True
    rows = json.loads((store / "dataset.json").read_text(encoding="utf-8"))["samples"]
    cases = [row for row in rows if row["run_id"] == "recovering"]
    assert len(cases) == 2
    recovered = next(row for row in cases if row["outcome"] == "verified_success")
    assert recovered["recovery_of"] == [failed["sample_id"]]
    state = advisor.status()
    assert state["shadow_evaluation"]["mode"] == "shadow"
    assert state["shadow_evaluation"]["promote"] is False
    shadow = json.loads((store / "models" / f"{state['shadow_candidate']}.json").read_text())
    assert shadow["mode"] == "shadow"
    assert any(entry["failure_support"] for entry in shadow["classes"].values()) or cases[0]["holdout"]
    assert state["active"] != state["shadow_candidate"]
    assert advisor.rollback(state["shadow_candidate"])["status"] == "unavailable"
    assert advisor._model(active).get("mode") != "shadow"


def test_success_recheck_after_failure_links_recovery_without_an_extra_vote(store):
    query = "Сверь таблицу и суммы"
    success = {**evidence("table-reconcile"), "outcome": "verified_success", "case_id": digest("case")}
    assert advisor.observe(query, success, "same-case")["ok"]
    failure = {**evidence("table-reconcile", 1), "outcome": "requirement_failure", "execution_ok": True,
               "requirement_ids": ["req-sums"], "case_id": success["case_id"]}
    failed = advisor.observe(query, failure, "same-case")
    assert advisor.observe(query, {**success, "input_version": digest("corrected")}, "same-case")["ok"]
    rows = json.loads((store / "dataset.json").read_text())["samples"]
    assert len(rows) == 2
    assert next(row for row in rows if row["outcome"] == "verified_success")["recovery_of"] == [failed["sample_id"]]


@pytest.mark.parametrize("diagnostic", ["network_error", "cancelled", "unknown"])
def test_diagnostics_never_penalize_a_skill(store, diagnostic):
    proof = {**evidence("audio-transcribe"), "outcome": diagnostic}
    assert advisor.observe("Расшифруй аудио", proof, "diagnostic")["status"] == "unavailable"
    proof = {**evidence("audio-transcribe"), "outcome": "requirement_failure", "execution_ok": False,
             "requirement_ids": ["req-transcript"]}
    assert advisor.observe("Расшифруй аудио", proof, "execution-error")["status"] == "unavailable"
    proof = {**evidence("audio-transcribe"), "outcome": "requirement_failure", "execution_ok": True,
             "requirement_ids": ["req-transcript"], "skill_binding": {}}
    assert advisor.observe("Расшифруй аудио", proof, "unselected")["status"] == "unavailable"
    assert not (store / "dataset.json").exists()
