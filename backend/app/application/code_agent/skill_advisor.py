"""Offline learned skill suggestions; never selects tools or changes permissions.

Only the run's verified-outcome boundary may call observe(). Features describe
the original request and observed environment, never the answer/checker text.
Positive-only multinomial NB learns associations, not a skill's causal benefit
or calibrated probability of success. Unknown tasks keep the full Qwen catalog.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import contextmanager
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any, Iterator

from app.core.config import DATA_DIR
from app.core.redaction import redact_text


ROOT = DATA_DIR / "skill_advisor"
logger = logging.getLogger(__name__)
_SCHEMA = 1
_FEATURES = "redacted-unicode-words-context-sha256-v2"
_HEX = re.compile(r"[0-9a-f]{64}")
_FEATURE = re.compile(r"[qe]:[0-9a-f]{16}")
_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_LOCAL_LOCK = threading.Lock()
_MAX_SAMPLES = 2048
_MAX_STORE_BYTES = 32 * 1024 * 1024


class _Busy(Exception):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _hash(value: Any) -> str:
    _require(isinstance(value, str) and bool(_HEX.fullmatch(value)), "Invalid evidence digest")
    return value


def _query(value: str) -> str:
    _require(isinstance(value, str) and bool(value.strip()), "Original task request is required")
    text = redact_text(value).casefold()
    # File/endpoint identities cannot turn a replay into an independent test
    # task. This is feature hygiene, never request routing or tool eligibility.
    text = re.sub(r"https?://[^\s<>]+|www\.[^\s<>]+", " ", text)
    text = re.sub(r'''["'](?:[a-z]:[\\/]|/|\\\\)[^"']*["']''', " ", text)
    text = re.sub(r"(?<!\w)(?:[a-z]:[\\/]|\\\\)[^\s<>]+", " ", text)
    text = re.sub(r"(?<!\w)/[^\s<>]+", " ", text)
    text = re.sub(r"(?<!\w)[\w.-]+(?:[\\/][\w.-]+)+", " ", text)
    text = re.sub(r"(?<!\w)[\w.-]+\.[a-z][a-z0-9]{0,9}(?!\w)", " ", text)
    text = re.sub(r"\b(?:\d+|[0-9a-f]{8,})\b", " ", text)
    text = re.sub(r"\[redacted[^\]]*\]|<redacted[^>]*>", " ", text)
    return " ".join(text.split())


def features(query: str, context: dict | None = None) -> dict[str, int]:
    """Bounded hashed features; no task text or environment values are stored."""
    counts = Counter(re.findall(r"[^\W_]{2,64}", _query(query), flags=re.UNICODE))
    words = list(counts)
    # Preserve both the task introduction and the actual instruction at its end
    # when a long request supplies more vocabulary than this small adviser uses.
    selected = words if len(words) <= 192 else [*words[:96], *words[-96:]]
    result = {"q:" + _digest(word)[:16]: min(count, 3)
              for word in selected for count in (counts[word],)}
    if context:
        _require(isinstance(context, dict), "Environment context must be an object")
        for key, value in sorted(context.items())[:32]:
            _require(isinstance(key, str), "Context feature names must be strings")
            if isinstance(value, (str, bool, int)) and len(str(value)) <= 160:
                result["e:" + _digest([key, value])[:16]] = 1
    return dict(sorted(result.items()))


def _binding(value: Any) -> dict:
    _require(isinstance(value, dict), "Missing verified skill binding")
    name, identity = value.get("name"), value.get("identity")
    _require(isinstance(name, str) and len(name) <= 64 and bool(_NAME.fullmatch(name)), "Invalid skill name")
    _require(isinstance(identity, dict), "Missing skill version identity")
    selected = {"sha256": _hash(identity.get("sha256"))}
    extra = {key for key in ("candidate_id", "revision", "package_sha256") if identity.get(key)}
    if extra:
        _require(len(extra) == 3, "Incomplete developed skill identity")
        _require(bool(re.fullmatch(r"[0-9a-f]{32}", str(identity["candidate_id"]))), "Invalid candidate identity")
        _require(bool(re.fullmatch(r"[0-9a-f]{40}", str(identity["revision"]))), "Invalid skill revision")
        selected.update({"candidate_id": identity["candidate_id"], "revision": identity["revision"],
                         "package_sha256": _hash(identity["package_sha256"])})
    return {"name": name, "identity": selected}


def _class(binding: dict) -> str:
    return _digest(binding)


def _sample(query: str, evidence: dict, run_id: str, context: dict | None) -> dict:
    _require(isinstance(evidence, dict) and evidence.get("provenance") == "observed_verification",
             "Learning needs observed verification provenance")
    _require(evidence.get("status") in {None, "passed"} and evidence.get("ok") is not False,
             "Failed verification cannot be a positive example")
    _require(isinstance(run_id, str) and 1 <= len(run_id) <= 128, "Invalid run identity")
    binding = _binding(evidence.get("skill_binding"))
    hashes = {}
    for field in ("targets", "reports"):
        rows = evidence.get(field)
        _require(isinstance(rows, list) and 0 < len(rows) <= 100, "Missing bounded verification hashes")
        hashes[field] = sorted({_hash(row.get("sha256")) for row in rows if isinstance(row, dict)})
        _require(len(hashes[field]) > 0 and all(isinstance(row, dict) for row in rows), "Invalid verification hashes")
    observed = {"provenance": "observed_verification", "input_version": _hash(evidence.get("input_version")), **hashes}
    request_hash = _digest(_query(query))
    vector = features(query, context)
    _require(any(key.startswith("q:") for key in vector), "No learnable task features")
    # Every identical request stays in one split, including retries and resume.
    group = request_hash
    return {"id": _digest([run_id, request_hash, binding, observed]), "run_id": run_id,
            "request_hash": request_hash, "group": group,
            "holdout": int(group[:8], 16) % 5 == 0,
            "features": vector, "binding": binding, "evidence": observed}


def _path(relative: str) -> Path:
    root = ROOT.absolute()
    path = root / relative
    _require(root.resolve() == root and path.resolve() == path.absolute() and path.is_relative_to(root),
             "Advisor store contains a redirected path")
    return path


def _read(path: Path) -> dict:
    with path.open("rb") as stream:
        raw = stream.read(_MAX_STORE_BYTES + 1)
    _require(len(raw) <= _MAX_STORE_BYTES, "Advisor record exceeds its storage bound")
    value = json.loads(raw.decode("utf-8"))
    _require(isinstance(value, dict), "Invalid advisor record")
    return value


def _write(path: Path, value: dict) -> None:
    raw = _canonical(value) + b"\n"
    _require(len(raw) <= _MAX_STORE_BYTES, "Advisor record exceeds its storage bound")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def _locked() -> Iterator[None]:
    if not _LOCAL_LOCK.acquire(blocking=False):
        raise _Busy()
    try:
        _path("").mkdir(parents=True, exist_ok=True)
        with _path("state.lock").open("a+b") as stream:
            if os.name == "nt":
                import msvcrt
                if stream.seek(0, 2) == 0:
                    stream.write(b"0")
                    stream.flush()
                stream.seek(0)
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise _Busy() from exc
                try:
                    yield
                finally:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    raise _Busy() from exc
                try:
                    yield
                finally:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    finally:
        _LOCAL_LOCK.release()


def _samples() -> list[dict]:
    path = _path("dataset.json")
    if not path.exists():
        return []
    record = _read(path)
    rows = record.get("samples")
    _require(record.get("schema") == _SCHEMA and isinstance(rows, list) and len(rows) <= _MAX_SAMPLES,
             "Invalid advisor dataset")
    _require(record.get("sha256") == _digest(rows), "Advisor dataset checksum mismatch")
    seen = set()
    for row in rows:
        _require(isinstance(row, dict) and _hash(row.get("id")) not in seen, "Duplicate sample identity")
        seen.add(row["id"])
        _binding(row.get("binding"))
        _hash(row.get("request_hash"))
        _require(row.get("group") == row["request_hash"], "Invalid sample grouping")
        _require(row.get("holdout") is (int(row["group"][:8], 16) % 5 == 0), "Sample split changed")
        vector = row.get("features")
        _require(isinstance(vector, dict) and 0 < len(vector) <= 224 and all(
            isinstance(key, str) and _FEATURE.fullmatch(key) and type(value) is int and 1 <= value <= 3
            for key, value in vector.items()), "Invalid learned feature vector")
    return rows


def _state() -> dict:
    path = _path("active.json")
    if not path.exists():
        return {"schema": _SCHEMA, "active": None, "previous": None, "candidate": None}
    state = _read(path)
    _require(state.get("schema") == _SCHEMA, "Unsupported advisor state schema")
    for key in ("active", "previous", "candidate"):
        if state.get(key) is not None:
            _hash(state[key])
    versions = state.get("approved_versions", [])
    _require(isinstance(versions, list) and len(versions) <= _MAX_SAMPLES, "Invalid approved model history")
    for version in versions:
        _hash(version)
    if state.get("active"):
        _require(state["active"] in versions, "Active advisor version was not evaluated")
    return state


def _model(version: str) -> dict:
    _hash(version)
    model = _read(_path(f"models/{version}.json"))
    _require(_digest(model) == version and model.get("schema") == _SCHEMA
             and model.get("features") == _FEATURES, "Advisor model checksum/schema mismatch")
    _require(isinstance(model.get("classes"), dict), "Invalid learned classes")
    for key, entry in model["classes"].items():
        _require(key == _class(_binding(entry.get("binding"))), "Invalid learned skill identity")
        _require(type(entry.get("support")) is int and entry["support"] > 0, "Invalid learned support")
        _require(isinstance(entry.get("weights"), dict), "Invalid learned weights")
        _require(isinstance(entry.get("default_weight"), (int, float)) and math.isfinite(entry["default_weight"]), "Invalid default weight")
        _require(all(_FEATURE.fullmatch(name) and isinstance(value, (int, float)) and math.isfinite(value)
                     for name, value in entry["weights"].items()), "Invalid learned weight")
    return model


def _unique(rows: list[dict]) -> list[dict]:
    # A repeated run with the same task/context/version supplies no extra vote.
    selected = {}
    for row in sorted(rows, key=lambda item: item["id"]):
        key = _digest([row["group"], row["features"], row["binding"]])
        selected.setdefault(key, row)
    return list(selected.values())


def _fit(rows: list[dict]) -> dict:
    counts: dict[str, Counter] = defaultdict(Counter)
    supports: Counter = Counter()
    bindings = {}
    for row in _unique([row for row in rows if not row["holdout"]]):
        label = _class(row["binding"])
        counts[label].update(row["features"])
        supports[label] += 1
        bindings[label] = row["binding"]
    vocabulary = {key for vector in counts.values() for key in vector}
    classes = {}
    # Laplace smoothing with uniform class priors: repeat frequency does not
    # itself recommend the most frequently used skill. Weights are learned
    # conditional token log-likelihoods, not an overall success counter.
    for label, vector in sorted(counts.items()):
        denominator = sum(vector.values()) + max(1, len(vocabulary))
        classes[label] = {"binding": bindings[label], "support": supports[label],
                          "default_weight": -math.log(denominator),
                          "weights": {key: math.log((count + 1) / denominator) for key, count in sorted(vector.items())}}
    return {"schema": _SCHEMA, "features": _FEATURES, "algorithm": "multinomial-nb-positive-uniform-prior-alpha1",
            "dataset_sha256": _digest(rows), "classes": classes}


def _rank(model: dict, vector: dict[str, int], eligible: set[str] | None = None) -> list[tuple[str, float]]:
    candidates = {key: value for key, value in model["classes"].items() if eligible is None or key in eligible}
    known = {feature for entry in candidates.values() for feature in entry["weights"]}
    query = {key for key in vector if key.startswith("q:")}
    matched = query & known
    # Environment-only overlap must never recommend a skill for an unknown task.
    if not query or not matched or len(matched) / len(query) < 0.35:
        return []
    scored = []
    for key, entry in candidates.items():
        score = sum(count * entry["weights"].get(feature, entry["default_weight"])
                    for feature, count in vector.items() if feature in known)
        scored.append((key, score))
    ranked = sorted(scored, key=lambda item: (-item[1], item[0]))
    return [] if len(ranked) > 1 and abs(ranked[0][1] - ranked[1][1]) < 1e-9 else ranked


def _evaluate(model: dict, rows: list[dict], incumbent: dict | None) -> dict:
    heldout = _unique([row for row in rows if row["holdout"]])
    training = _unique([row for row in rows if not row["holdout"]])
    popularity = Counter(_class(row["binding"]) for row in training)
    baseline = sorted(popularity, key=lambda key: (-popularity[key], key))

    def mrr(scoring: dict | None, use_baseline: bool = False) -> float:
        grouped: dict[str, list[float]] = defaultdict(list)
        for row in heldout:
            ordered = baseline if use_baseline else [key for key, _ in _rank(scoring, row["features"])] if scoring else []
            label = _class(row["binding"])
            grouped[row["group"]].append(1.0 / (ordered.index(label) + 1) if label in ordered else 0.0)
        return sum(sum(values) / len(values) for values in grouped.values()) / len(grouped) if grouped else 0.0

    metrics = {"train_groups": len({row["group"] for row in training}),
               "holdout_groups": len({row["group"] for row in heldout}),
               "holdout_classes": len({_class(row["binding"]) for row in heldout}),
               "classes": len(model["classes"]), "candidate_mrr": mrr(model),
               "baseline_mrr": mrr(None, True), "incumbent_mrr": mrr(incumbent),
               "baseline": "training-frequency-only", "grouping": "normalized-original-request"}
    sufficient = (metrics["train_groups"] >= 4 and metrics["holdout_groups"] >= 2
                  and metrics["classes"] >= 2 and metrics["holdout_classes"] >= 2)
    metrics["promote"] = sufficient and metrics["candidate_mrr"] > metrics["baseline_mrr"] + 1e-9 and (
        incumbent is None or metrics["candidate_mrr"] + 1e-9 >= metrics["incumbent_mrr"])
    metrics["reason"] = "heldout_improvement" if metrics["promote"] else "insufficient_evidence" if not sufficient else "no_heldout_improvement"
    return metrics


def _unavailable(operation: str, exc: Exception) -> dict:
    # Do not put task text, paths from evidence, or checker output in diagnostics.
    logger.warning("Skill advisor %s unavailable (%s)", operation, type(exc).__name__)
    return {"ok": False, "status": "unavailable", "model_version": None,
            "reason": type(exc).__name__, "recommendations": []}


def observe(query: str, evidence: dict, run_id: str, context: dict | None = None) -> dict:
    """Record one verified association; never accept failed/unverified outcomes."""
    try:
        sample = _sample(query, evidence, run_id, context)
        with _locked():
            rows, state = _samples(), _state()
            incumbent = _model(state["active"]) if state.get("active") else None
            existing = next((row for row in rows if row["id"] == sample["id"]), None)
            if existing:
                _require(existing == sample, "A replay changed a recorded sample")
                if state.get("dataset_sha256") == _digest(rows):
                    return {"ok": True, "status": "duplicate", "model_version": state.get("active"), "sample_id": sample["id"]}
            else:
                _require(len(rows) < _MAX_SAMPLES, "Advisor dataset capacity reached")
                rows = sorted([*rows, sample], key=lambda item: item["id"])
            candidate = _fit(rows)
            version = _digest(candidate)
            evaluation = _evaluate(candidate, rows, incumbent)
            model_path = _path(f"models/{version}.json")
            if model_path.exists():
                _model(version)
            else:
                _write(model_path, candidate)
            # Immutable model first, dataset next, atomic active pointer last.
            # A retry repairs an interrupted publish, but never undoes rollback.
            _write(_path("dataset.json"), {"schema": _SCHEMA, "sha256": _digest(rows), "samples": rows})
            if evaluation["promote"] and state.get("active") != version:
                state["previous"], state["active"] = state.get("active"), version
                state["approved_versions"] = [*state.get("approved_versions", []), version]
            state.update({"candidate": version, "dataset_sha256": _digest(rows), "evaluation": evaluation,
                          "sample_count": len(rows), "last_action": "observe"})
            _write(_path("active.json"), state)
            return {"ok": True, "status": "learned" if evaluation["promote"] else "recorded", "sample_id": sample["id"],
                    "model_version": state.get("active"), "candidate_version": version, "evaluation": evaluation}
    except _Busy:
        return {"ok": False, "status": "busy", "model_version": None, "reason": "advisor_store_locked"}
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        return _unavailable("observe", exc)


def advise(query: str, catalog: list[dict], identities: dict[str, dict], context: dict | None = None) -> dict:
    """Return optional hints only for exact currently available skill versions."""
    try:
        state = _state()
        if not state.get("active"):
            return {"ok": True, "status": "cold_start", "model_version": None, "recommendations": [],
                    "reason": (state.get("evaluation") or {}).get("reason", "no_verified_examples")}
        model = _model(state["active"])
        names = {item.get("name") for item in catalog if isinstance(item, dict)}
        eligible = set()
        for name in names:
            if name in identities:
                binding = _binding(identities[name])
                _require(binding["name"] == name, "Catalog identity/name mismatch")
                eligible.add(_class(binding))
        ranked = _rank(model, features(query, context), eligible)
        if not ranked or (len(ranked) > 1 and abs(ranked[0][1] - ranked[1][1]) < 1e-9):
            return {"ok": True, "status": "abstained", "model_version": state["active"], "recommendations": [],
                    "reason": "unknown_task_or_version" if not ranked else "ambiguous_evidence"}
        best = ranked[0][1]
        recommendations = [{**model["classes"][key]["binding"], "score": round(score - best, 6),
                            "support": model["classes"][key]["support"]} for key, score in ranked[:3]]
        return {"ok": True, "status": "ready", "model_version": state["active"], "recommendations": recommendations,
                "reason": "learned_relative_ranking", "score_kind": "relative_log_likelihood_not_probability"}
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        return _unavailable("advise", exc)


def status() -> dict:
    try:
        state = _state()
        if state.get("active"):
            _model(state["active"])
        return {**state, "ok": True, "status": "ready" if state.get("active") else "cold_start",
                "model_version": state.get("active"),
                "reason": (state.get("evaluation") or {}).get("reason", "no_verified_examples")}
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        return _unavailable("status", exc)


def rollback(version: str) -> dict:
    try:
        with _locked():
            _model(version)
            state = _state()
            _require(version in state.get("approved_versions", []), "Only an evaluated, previously active model can be restored")
            if state.get("active") != version:
                state["previous"], state["active"] = state.get("active"), version
                state["last_action"] = "rollback"
                _write(_path("active.json"), state)
            return {"ok": True, "status": "rolled_back", "model_version": version}
    except _Busy:
        return {"ok": False, "status": "busy", "model_version": None, "reason": "advisor_store_locked"}
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        return _unavailable("rollback", exc)
