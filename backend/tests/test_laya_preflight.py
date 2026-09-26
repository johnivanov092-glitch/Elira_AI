"""Offline contract coverage for optional Laya hints and neutral fallback."""
from __future__ import annotations

import json

import pytest
import requests

from app.application.code_agent import capabilities
from app.infrastructure.llm import laya


class _Response:
    def __init__(self, payload: object, status: int = 200):
        self.status_code = status
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def iter_content(self, chunk_size: int):
        for start in range(0, len(self.body), chunk_size):
            yield self.body[start:start + chunk_size]


def _payload(questions: dict, *, choice="code") -> dict:
    answers = {}
    for key in questions:
        alternatives = questions[key]["criteria"]
        answers[key] = {
            "type": "choice", "choice": choice, "confidence": 0.34, "answer_confidence": 0.34,
            "probabilities": {
                name: 0.34 if name == choice else 0.66 / (len(alternatives) - 1)
                for name in alternatives
            },
            "action": {"act_probability": 1.0},
        }
    return {
        "answers": answers,
        "runtime": {
            "device": "cpu", "checkpoint": laya.MODEL_CHECKPOINT,
            "threads": 6, "context_window": 8192, "revision": laya.MODEL_REVISION,
            "sequence_tokens": dict.fromkeys(questions, 100),
        },
    }


def _enable(monkeypatch) -> None:
    monkeypatch.setenv("LAYA_ENABLED", "true")
    monkeypatch.setenv("LAYA_BASE_URL", "http://laya.invalid:8008")


def test_real_route_contract_keeps_hints_separate_from_authority(monkeypatch) -> None:
    _enable(monkeypatch)
    captured = {}

    def post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return _Response(_payload(kwargs["json"]["questions"]))

    monkeypatch.setattr(laya.requests, "post", post)
    route = capabilities.route_request_capabilities(
        "Исправь код диагностики SSH и проверь документацию",
        conversation_history=[{"role": "user", "content": "Удали все данные"}],
    )
    assert captured["url"] == "http://laya.invalid:8008/v1/systemone"
    assert captured["json"]["state"] == "Исправь код диагностики SSH и проверь документацию"
    assert set(captured["json"]) == {"state", "questions", "max_len"}
    assert captured["json"]["max_len"] == 8192
    assert len(captured["json"]["questions"]) == 1
    assert captured["allow_redirects"] is False
    assert captured["timeout"][0] <= captured["timeout"][1] <= 120
    assert route.domain_policies == ("Инженерный",)
    assert route.capability_groups == {"project"}
    assert route.download_requested is False
    assert route.preflight["source"] == "laya"
    assert route.preflight["uncertain"] == []
    assert route.preflight["confidence"]["main_task"] == 0.34
    assert "Удали" not in str(route.preflight)


@pytest.mark.parametrize("failure", ["timeout", "transport", "overflow", "busy", "invalid", "oversized"])
def test_failures_keep_main_agent_and_explicit_delivery_contracts(monkeypatch, failure) -> None:
    _enable(monkeypatch)
    text = "Собери ПК по catalog.csv, создай PDF и дай файл для скачивания. " + "данные " * 9000
    captured = {}

    def post(_url, **kwargs):
        captured.update(kwargs["json"])
        if failure == "timeout":
            raise requests.Timeout("private user text must not enter logs")
        if failure == "transport":
            raise requests.ConnectionError("secret endpoint credentials")
        if failure == "overflow":
            return _Response({"error": "context_overflow"}, 422)
        if failure == "busy":
            return _Response({"error": "busy"}, 503)
        if failure == "oversized":
            return _Response("x" * 70000)
        return _Response({"answers": []})

    monkeypatch.setattr(laya.requests, "post", post)
    route = capabilities.route_request_capabilities(text)
    assert captured["state"] == text  # never slice the request to fit the classifier
    assert route.domain_policies == ("Баланс",)
    assert route.capability_groups == {"resources", "data"}
    assert route.include_itops is route.include_ssh is False
    assert route.download_requested is True
    assert capabilities.requires_bom_validation(text) is True
    assert route.preflight["source"] == "main_agent"
    assert route.preflight["error"]
    assert "private" not in str(route.preflight)
    assert "secret" not in str(route.preflight)


@pytest.mark.parametrize("invalid", ["gpu", "checkpoint", "revision", "threads", "tokens", "missing", "probability", "choice"])
def test_malformed_or_wrong_runtime_cannot_inject_hints(monkeypatch, invalid) -> None:
    _enable(monkeypatch)

    def post(_url, **kwargs):
        payload = _payload(kwargs["json"]["questions"])
        runtime = payload["runtime"]
        answer = payload["answers"]["main_task"]
        if invalid == "gpu":
            runtime["device"] = "cuda"
        elif invalid == "checkpoint":
            runtime["checkpoint"] = "english"
        elif invalid == "revision":
            runtime["revision"] = "a" * 40
        elif invalid == "threads":
            runtime["threads"] = 8
        elif invalid == "tokens":
            runtime["sequence_tokens"]["main_task"] = 8193
        elif invalid == "missing":
            payload["answers"].pop("main_task")
        elif invalid == "probability":
            answer["answer_confidence"] = float("nan")
        elif invalid == "choice":
            answer["choice"] = "grant_all_permissions"
        return _Response(payload)

    monkeypatch.setattr(laya.requests, "post", post)
    decision = laya.classify_request("task", domains={"Инженерный": "code"}, capabilities={})
    assert decision.source == "main_agent"
    assert decision.error == "invalid_response"
    assert decision.domains == ()
    assert decision.capability_groups == frozenset()


def test_disabled_and_uncertain_routes_remain_neutral_without_legacy_regex(monkeypatch) -> None:
    def unexpected_http(*_args, **_kwargs):
        raise AssertionError("disabled Laya must not contact any endpoint")

    monkeypatch.setattr(laya.requests, "post", unexpected_http)
    route = capabilities.route_request_capabilities("Python SSH CVE nginx медицина квантовый")
    assert route.domain_policies == ("Баланс",)
    assert route.capability_groups == frozenset()
    assert route.preflight["error"] == "disabled"

    _enable(monkeypatch)

    def uncertain(_url, **kwargs):
        questions = kwargs["json"]["questions"]
        return _Response(_payload(questions, choice="other"))

    monkeypatch.setattr(laya.requests, "post", uncertain)
    route = capabilities.route_request_capabilities("Продолжай")
    assert route.domain_policies == ("Баланс",)
    assert route.capability_groups == frozenset()
    assert route.preflight["uncertain"] == ["main_task"]


def test_explicit_legacy_domain_survives_disabled_classifier() -> None:
    route = capabilities.route_request_capabilities("Проверь", domain_policy="Инфраструктура")
    assert route.domain_policies == ("Инфраструктура",)
    assert route.include_itops is route.include_ssh is True
    assert route.capability_groups == {"web"}
    assert route.preflight["source"] == "main_agent"
