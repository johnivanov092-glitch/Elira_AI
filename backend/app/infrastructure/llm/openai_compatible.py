from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Generator

import requests


_TRUE_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class OpenAICompatibleConfig:
    enabled: bool
    provider: str
    base_url: str
    model: str
    api_key: str
    timeout_seconds: float
    max_tokens: int | None
    context_window: int | None


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE_VALUES


# Transient-error retry for non-stream chat completions (connection/timeout/5xx).
_LLM_RETRY_ATTEMPTS = 3
_LLM_RETRY_BACKOFF_S = 0.5

# Live server context window (/props) cache. /v1/models does NOT expose the
# loaded n_ctx, so /props (default_generation_settings.n_ctx) is the only
# truthful source of the real window. Cached briefly so a server restart with a
# new -c value is adopted within the TTL, without an app restart.
_PROPS_CTX_TTL_S = 120.0
_props_ctx_cache: dict[str, tuple[float, int | None]] = {}


def _env_value(name: str, default: str = "") -> str:
    raw = os.getenv(name)
    return default if raw is None else raw


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_optional_int(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def local_llm_config() -> OpenAICompatibleConfig:
    return OpenAICompatibleConfig(
        enabled=_env_bool("LLAMA_SERVER_ENABLED"),
        provider="llama_server",
        base_url=_env_value("LLAMA_SERVER_BASE_URL", "http://192.168.88.15:8000/v1").rstrip("/"),
        model=_env_value("LLAMA_SERVER_MODEL", "local-model").strip() or "local-model",
        api_key=_env_value("LLAMA_SERVER_API_KEY", "local").strip() or "local",
        timeout_seconds=_env_float("LLAMA_SERVER_TIMEOUT_SECONDS", 600.0),
        max_tokens=_env_optional_int("LLAMA_SERVER_MAX_TOKENS"),
        context_window=_env_optional_int("LLAMA_SERVER_CONTEXT_WINDOW") or 131072,
    )


def is_local_llm_enabled() -> bool:
    return local_llm_config().enabled


@dataclass(frozen=True)
class LocalEmbedConfig:
    enabled: bool
    base_url: str
    model: str
    api_key: str
    timeout_seconds: float


def local_embed_config() -> LocalEmbedConfig:
    return LocalEmbedConfig(
        enabled=_env_bool("LOCAL_EMBED_ENABLED"),
        base_url=os.getenv("LOCAL_EMBED_BASE_URL", "http://192.168.88.15:8001/v1").rstrip("/"),
        model=os.getenv("LOCAL_EMBED_MODEL", "local-embed").strip() or "local-embed",
        api_key=os.getenv("LOCAL_EMBED_API_KEY", "local").strip() or "local",
        timeout_seconds=_env_float("LOCAL_EMBED_TIMEOUT_SECONDS", 30.0),
    )


def is_local_embed_enabled() -> bool:
    return local_embed_config().enabled


def embed_text(text: str) -> list[float] | None:
    """Embed one string via the OpenAI-compatible /v1/embeddings endpoint.

    Returns the vector, or None on any failure. Callers must NOT silently fall
    back to a different embedding backend on None: mixed vector spaces would
    corrupt cosine search.
    """
    cfg = local_embed_config()
    if not cfg.enabled:
        return None
    try:
        response = requests.post(
            f"{cfg.base_url}/embeddings",
            headers=_headers(OpenAICompatibleConfig(
                enabled=True, provider="local_embed", base_url=cfg.base_url,
                model=cfg.model, api_key=cfg.api_key,
                timeout_seconds=cfg.timeout_seconds, max_tokens=None, context_window=None,
            )),
            json={"model": cfg.model, "input": text},
            timeout=cfg.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError):
        return None
    rows = data.get("data") if isinstance(data.get("data"), list) else []
    if rows and isinstance(rows[0], dict):
        vec = rows[0].get("embedding")
        if isinstance(vec, list) and vec:
            return [float(x) for x in vec]
    return None


def is_local_llm_model(model_name: str | None) -> bool:
    cfg = local_llm_config()
    return cfg.enabled and str(model_name or "").strip() == cfg.model


def _headers(cfg: OpenAICompatibleConfig) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
    }


def _decode_sse_line(raw_line: Any) -> str:
    if isinstance(raw_line, bytes):
        return raw_line.decode("utf-8", errors="replace")
    return str(raw_line)


def _http_error_message(
    exc: requests.HTTPError,
    *,
    service: str = "OpenAI-compatible provider",
    endpoint: str = "",
    path: str = "",
) -> str:
    response = exc.response
    if response is None:
        return str(exc)
    body = (response.text or "").strip()
    if len(body) > 500:
        body = body[:500] + "..."
    target = "/".join(
        part.strip("/") for part in (endpoint, path) if part.strip("/")
    )
    location = f" at {target}" if target else ""
    hint = " Verify the configured endpoint and service route." if response.status_code == 404 else ""
    return (
        f"{service} HTTP {response.status_code}{location}: "
        f"{body or response.reason}.{hint}"
    )


def _coerce_tool_arguments(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value if value is not None else {}


def _stringify_tool_arguments(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value if value is not None else {}, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return "{}"


def _normalize_tool_calls(raw_calls: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_calls, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in raw_calls:
        if not isinstance(item, dict):
            continue
        function = item.get("function") if isinstance(item.get("function"), dict) else {}
        name = str(function.get("name") or "").strip()
        if not name:
            continue
        call: dict[str, Any] = {
            "function": {
                "name": name,
                "arguments": _coerce_tool_arguments(function.get("arguments")),
            }
        }
        call_id = str(item.get("id") or "").strip()
        if call_id:
            call["id"] = call_id
        call["type"] = str(item.get("type") or "function").strip() or "function"
        normalized.append(call)
    return normalized


def _request_tool_call(raw_call: Any, *, message_index: int, call_index: int) -> dict[str, Any] | None:
    if not isinstance(raw_call, dict):
        return None
    function = raw_call.get("function") if isinstance(raw_call.get("function"), dict) else {}
    name = str(function.get("name") or raw_call.get("name") or "").strip()
    if not name:
        return None
    call_id = str(raw_call.get("id") or "").strip() or f"call_{message_index}_{call_index}"
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": _stringify_tool_arguments(function.get("arguments", raw_call.get("arguments", {}))),
        },
    }


def _normalize_messages_for_request(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    pending_tool_call_ids: list[str] = []
    for message_index, raw_message in enumerate(messages or []):
        if not isinstance(raw_message, dict):
            continue
        role = str(raw_message.get("role") or "").strip()
        if role not in {"system", "user", "assistant", "tool"}:
            continue
        content = raw_message.get("content")
        content_text = "" if content is None else str(content)

        if role == "assistant":
            item: dict[str, Any] = {"role": "assistant", "content": content_text}
            raw_calls = raw_message.get("tool_calls")
            if isinstance(raw_calls, list) and raw_calls:
                calls = [
                    call for idx, raw_call in enumerate(raw_calls)
                    if (call := _request_tool_call(raw_call, message_index=message_index, call_index=idx)) is not None
                ]
                if calls:
                    item["tool_calls"] = calls
                    pending_tool_call_ids.extend(str(call["id"]) for call in calls)
            if content_text.strip() or item.get("tool_calls"):
                normalized.append(item)
            continue

        if role == "tool":
            tool_call_id = str(raw_message.get("tool_call_id") or "").strip()
            if not tool_call_id and pending_tool_call_ids:
                tool_call_id = pending_tool_call_ids.pop(0)
            if not tool_call_id:
                name = str(raw_message.get("name") or "tool").strip() or "tool"
                normalized.append({"role": "assistant", "content": f"[tool result {name}] {content_text}"})
                continue
            normalized.append({"role": "tool", "tool_call_id": tool_call_id, "content": content_text})
            continue

        if role in {"system", "user"} and content_text.strip():
            normalized.append({"role": role, "content": content_text})
            continue

    # llama.cpp rejects two or more assistant messages at the tail. Merge only
    # plain-text turns; tool-call turns retain their pairing semantics.
    while (
        len(normalized) >= 2
        and normalized[-1].get("role") == "assistant"
        and normalized[-2].get("role") == "assistant"
        and not normalized[-1].get("tool_calls")
        and not normalized[-2].get("tool_calls")
    ):
        latter = normalized.pop()
        former = normalized.pop()
        merged = "\n\n".join(
            part for part in (str(former.get("content") or "").strip(), str(latter.get("content") or "").strip())
            if part
        )
        if merged:
            normalized.append({"role": "assistant", "content": merged})
    return normalized


def _local_llm_response(data: dict[str, Any], *, elapsed_ns: int) -> dict[str, Any]:
    choices = data.get("choices") if isinstance(data.get("choices"), list) else []
    first = choices[0] if choices and isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first.get("message"), dict) else {}
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    return {
        "model": str(data.get("model") or ""),
        "message": {
            "role": str(message.get("role") or "assistant"),
            "content": str(message.get("content") or ""),
            # Reasoning arrives in a separate field when thinking is enabled
            # (--jinja server); surface it so callers can show it apart from the
            # answer. Empty string when thinking is off — never mixed into content.
            "reasoning_content": str(message.get("reasoning_content") or ""),
            "tool_calls": _normalize_tool_calls(message.get("tool_calls")),
        },
        "done": True,
        "prompt_eval_count": prompt_tokens,
        "eval_count": completion_tokens,
        "total_duration": elapsed_ns,
        "provider": local_llm_config().provider,
        "raw_openai": data,
    }


# When the caller sets no max_tokens, reserve a realistic output budget in the
# pre-send guard anyway. Without this an at-the-limit prompt passed the gate,
# the server context-shifted mid-generation and returned empty/garbled content —
# which downstream looks like "the model repeated its previous answer".
_DEFAULT_OUTPUT_RESERVE_TOKENS = 1024


def _estimate_tokens(text: str) -> int:
    """Coarse char→token estimate. Cyrillic tokenizes ~2.8 chars/token (vs ~4 for
    ASCII/code); a flat /4 under-counted Russian prompts by ~30%, letting
    over-limit prompts through the guard."""
    cyr = sum(1 for ch in text if "Ѐ" <= ch <= "ӿ")
    ascii_like = len(text) - cyr
    return int(cyr / 2.8 + ascii_like / 4) + 1


def _guard_context_request(
    messages: list[dict[str, Any]],
    *,
    max_tokens: Any,
    requested_ctx: Any,
) -> None:
    context_limit = _positive_int(requested_ctx)
    if context_limit is None:
        return
    prompt_tokens = _estimate_tokens(json.dumps(messages, ensure_ascii=False))
    output_tokens = _positive_int(max_tokens) or _DEFAULT_OUTPUT_RESERVE_TOKENS
    safety_margin = max(128, context_limit // 64)
    required = prompt_tokens + output_tokens + safety_margin
    if required > context_limit:
        raise RuntimeError(
            "Context request blocked before send: estimated prompt and output "
            f"require {required} tokens, but the active limit is {context_limit}."
        )


def _apply_thinking_option(payload: dict[str, Any], opts: dict[str, Any]) -> None:
    """Pass a per-request ``chat_template_kwargs`` (e.g. ``{"enable_thinking": true}``)
    through to a ``--jinja`` llama-server. This lets a single run toggle model
    reasoning without a server restart or config edit; when the caller doesn't set
    it the key is omitted and the server keeps its own default (reasoning off)."""
    ctk = opts.get("chat_template_kwargs")
    if isinstance(ctk, dict) and ctk:
        payload["chat_template_kwargs"] = ctk


# Extra sampler params llama.cpp accepts in the request body (verified against the
# live server via /props). Whitelisted so callers can only set known keys, never
# inject arbitrary payload fields. Used for per-request DRY anti-repetition on
# thinking runs (a reasoning model can fall into a degenerate "same sentence
# forever" loop; DRY penalises repeated token sequences at sampling time so the
# loop never forms). Sent per-request → no server restart, server default stays off.
_SAMPLING_EXTRA_KEYS = frozenset({
    "dry_multiplier", "dry_base", "dry_allowed_length", "dry_penalty_last_n",
    "dry_sequence_breakers", "repeat_penalty", "repeat_last_n",
})


def _apply_sampling_extra(payload: dict[str, Any], opts: dict[str, Any]) -> None:
    """Merge whitelisted extra sampling params (e.g. DRY) from ``options['sampling']``
    into the request body top-level. Omitted keys keep the server default."""
    extra = opts.get("sampling")
    if isinstance(extra, dict):
        for key, value in extra.items():
            if key in _SAMPLING_EXTRA_KEYS and value is not None:
                payload[key] = value


# Safety ceiling for a single generation's reasoning stream (chars). Far above any
# real chain-of-thought (a normal thinking answer is a few thousand chars); a
# runaway repetition loop is cut here so it can't generate into the whole context
# window or burn the execution deadline. Only reached when thinking is on.
_MAX_REASONING_CHARS = 24000

# Content-channel analogue (the answer had NO runaway protection: a live run
# produced one paragraph ×20). Hard cap plus a paragraph-repeat detector: if the
# latest paragraph (>40 chars) already occurs many times in the accumulated
# answer, the generation is degenerate — cut it. Checked on paragraph boundaries
# only, so the per-token cost is negligible.
_MAX_CONTENT_CHARS = 60000
_CONTENT_REPEAT_PARA_LIMIT = 6


def _content_looks_degenerate(text: str) -> bool:
    """True when the tail paragraph of *text* repeats _CONTENT_REPEAT_PARA_LIMIT+
    times — the signature of a sampling attractor, not a legitimate answer."""
    paras = [p.strip() for p in text.split("\n") if len(p.strip()) > 40]
    if len(paras) < _CONTENT_REPEAT_PARA_LIMIT:
        return False
    tail = paras[-1]
    return paras.count(tail) >= _CONTENT_REPEAT_PARA_LIMIT


def _collapse_repeated_paragraphs(text: str) -> str:
    """Collapse consecutive duplicate paragraphs (used after a degenerate cut so
    the surviving answer reads once, not ×N)."""
    out: list[str] = []
    prev = None
    for para in text.split("\n"):
        key = para.strip()
        if key and key == prev:
            continue
        out.append(para)
        prev = key if key else prev
    return "\n".join(out)


def _request_context_limit(options: dict[str, Any], *, configured_context: Any) -> int | None:
    requested_context = _positive_int(options.get("num_ctx"))
    active_context = _positive_int(
        options.get("active_context_limit") or options.get("server_context_window")
    )
    configured = _positive_int(configured_context)
    limits: list[int] = []
    if requested_context:
        limits.append(requested_context)
    if active_context:
        limits.append(active_context)
    elif configured:
        limits.append(configured)
    return min(limits) if limits else None


def chat_completion(
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    options: dict[str, Any] | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    cfg = local_llm_config()
    if not cfg.enabled:
        raise RuntimeError("local llama-server provider is disabled")

    normalized_messages = _normalize_messages_for_request(messages)
    payload: dict[str, Any] = {
        "model": model or cfg.model,
        "messages": normalized_messages,
    }
    if tools:
        payload["tools"] = tools
    if cfg.max_tokens:
        payload["max_tokens"] = cfg.max_tokens

    opts = options or {}
    if "temperature" in opts:
        payload["temperature"] = opts["temperature"]
    _apply_thinking_option(payload, opts)
    _apply_sampling_extra(payload, opts)

    _guard_context_request(
        normalized_messages,
        max_tokens=payload.get("max_tokens"),
        requested_ctx=_request_context_limit(opts, configured_context=cfg.context_window),
    )

    started = time.monotonic_ns()
    data = None
    last_exc: Exception | None = None
    # Retry transient hiccups (connection drop, read timeout, 5xx) a couple of
    # times with linear backoff. Client errors (4xx) and invalid JSON are not
    # retried — retrying won't fix them.
    for _attempt in range(_LLM_RETRY_ATTEMPTS):
        try:
            response = requests.post(
                f"{cfg.base_url}/chat/completions",
                headers=_headers(cfg),
                json=payload,
                timeout=timeout or cfg.timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
            break
        except ValueError as exc:
            raise RuntimeError("OpenAI-compatible provider returned invalid JSON") from exc
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc  # transient → retry
        except requests.HTTPError as exc:
            status = getattr(exc.response, "status_code", 0) or 0
            if not (500 <= status < 600):
                raise RuntimeError(_http_error_message(exc)) from exc  # 4xx → no retry
            last_exc = exc  # 5xx → transient
        except requests.RequestException as exc:
            raise RuntimeError(f"OpenAI-compatible request failed: {exc}") from exc
        if _attempt < _LLM_RETRY_ATTEMPTS - 1:
            time.sleep(_LLM_RETRY_BACKOFF_S * (_attempt + 1))

    if data is None:  # all attempts exhausted on a transient error
        if isinstance(last_exc, requests.HTTPError):
            raise RuntimeError(_http_error_message(last_exc)) from last_exc
        raise RuntimeError(f"OpenAI-compatible request failed after retries: {last_exc}") from last_exc

    return _local_llm_response(data, elapsed_ns=time.monotonic_ns() - started)


def chat_completion_stream(
    *,
    model: str,
    messages: list[dict[str, Any]],
    options: dict[str, Any] | None = None,
    timeout: float | None = None,
) -> Generator[str, None, None]:
    cfg = local_llm_config()
    if not cfg.enabled:
        raise RuntimeError("local llama-server provider is disabled")

    normalized_messages = _normalize_messages_for_request(messages)
    payload: dict[str, Any] = {
        "model": model or cfg.model,
        "messages": normalized_messages,
        "stream": True,
    }
    if cfg.max_tokens:
        payload["max_tokens"] = cfg.max_tokens
    opts = options or {}
    if "temperature" in opts:
        payload["temperature"] = opts["temperature"]
    _apply_thinking_option(payload, opts)
    _apply_sampling_extra(payload, opts)
    _guard_context_request(
        normalized_messages,
        max_tokens=payload.get("max_tokens"),
        requested_ctx=_request_context_limit(opts, configured_context=cfg.context_window),
    )

    response: requests.Response | None = None
    try:
        response = requests.post(
            f"{cfg.base_url}/chat/completions",
            headers=_headers(cfg),
            json=payload,
            timeout=timeout or cfg.timeout_seconds,
            stream=True,
        )
        response.raise_for_status()
        for raw_line in response.iter_lines(decode_unicode=False):
            if not raw_line:
                continue
            line = _decode_sse_line(raw_line).strip()
            if line.startswith("data:"):
                line = line[5:].strip()
            if line == "[DONE]":
                break
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            choices = data.get("choices") if isinstance(data.get("choices"), list) else []
            first = choices[0] if choices and isinstance(choices[0], dict) else {}
            delta = first.get("delta") if isinstance(first.get("delta"), dict) else {}
            token = str(delta.get("content") or "")
            if token:
                yield token
    except requests.HTTPError as exc:
        raise RuntimeError(_http_error_message(exc)) from exc
    except requests.RequestException as exc:
        raise RuntimeError(f"OpenAI-compatible stream failed: {exc}") from exc
    finally:
        if response is not None:
            response.close()


def chat_completion_event_stream(
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    options: dict[str, Any] | None = None,
    timeout: float | None = None,
) -> Generator[dict[str, Any], None, None]:
    """Stream visible deltas and assemble OpenAI tool-call fragments."""
    cfg = local_llm_config()
    if not cfg.enabled:
        raise RuntimeError("local llama-server provider is disabled")

    normalized_messages = _normalize_messages_for_request(messages)
    payload: dict[str, Any] = {
        "model": model or cfg.model,
        "messages": normalized_messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if tools:
        payload["tools"] = tools
    if cfg.max_tokens:
        payload["max_tokens"] = cfg.max_tokens
    opts = options or {}
    if "temperature" in opts:
        payload["temperature"] = opts["temperature"]
    _apply_thinking_option(payload, opts)
    _apply_sampling_extra(payload, opts)
    _guard_context_request(
        normalized_messages,
        max_tokens=payload.get("max_tokens"),
        requested_ctx=_request_context_limit(opts, configured_context=cfg.context_window),
    )

    response: requests.Response | None = None
    started = time.monotonic_ns()
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    reasoning_chars = 0
    reasoning_runaway = False
    content_chars = 0
    content_runaway = False
    content_check_at = 2000  # next accumulated-size checkpoint for the detector
    calls: dict[int, dict[str, Any]] = {}
    usage: dict[str, Any] = {}
    try:
        response = requests.post(
            f"{cfg.base_url}/chat/completions",
            headers=_headers(cfg),
            json=payload,
            timeout=timeout or cfg.timeout_seconds,
            stream=True,
        )
        response.raise_for_status()
        for raw_line in response.iter_lines(decode_unicode=False):
            if not raw_line:
                continue
            line = _decode_sse_line(raw_line).strip()
            if line.startswith("data:"):
                line = line[5:].strip()
            if line == "[DONE]":
                break
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data.get("usage"), dict):
                usage = dict(data["usage"])
            choices = data.get("choices") if isinstance(data.get("choices"), list) else []
            first = choices[0] if choices and isinstance(choices[0], dict) else {}
            delta = first.get("delta") if isinstance(first.get("delta"), dict) else {}
            # Thinking (--jinja) streams the chain-of-thought in its own
            # `reasoning_content` field, separate from the answer's `content`.
            # Route it to a distinct event so callers show it apart from (and
            # never blended into) the final answer.
            rtoken = str(delta.get("reasoning_content") or "")
            if rtoken:
                reasoning_parts.append(rtoken)
                reasoning_chars += len(rtoken)
                yield {"type": "reasoning", "content": rtoken}
                # Runaway-reasoning guard (safety net beside per-request DRY): a
                # degenerate "same sentence forever" loop is cut here before it
                # fills the context window. Stop reading → `finally` closes the
                # upstream connection and frees the server.
                if reasoning_chars > _MAX_REASONING_CHARS:
                    reasoning_runaway = True
                    break
            token = str(delta.get("content") or "")
            if token:
                content_parts.append(token)
                content_chars += len(token)
                yield {"type": "delta", "content": token}
                # Content runaway guard: hard cap + paragraph-repeat detector,
                # evaluated at coarse checkpoints so it costs ~nothing per token.
                if content_chars > _MAX_CONTENT_CHARS:
                    content_runaway = True
                    break
                if content_chars >= content_check_at:
                    content_check_at = content_chars + 2000
                    if _content_looks_degenerate("".join(content_parts)):
                        content_runaway = True
                        break
            for fragment in delta.get("tool_calls") or []:
                if not isinstance(fragment, dict):
                    continue
                index = int(fragment.get("index") or 0)
                call = calls.setdefault(index, {
                    "id": "",
                    "type": "function",
                    "function": {"name": "", "arguments": ""},
                })
                if fragment.get("id"):
                    call["id"] += str(fragment["id"])
                if fragment.get("type"):
                    call["type"] = str(fragment["type"])
                function = fragment.get("function") if isinstance(fragment.get("function"), dict) else {}
                call["function"]["name"] += str(function.get("name") or "")
                call["function"]["arguments"] += str(function.get("arguments") or "")
    except requests.HTTPError as exc:
        raise RuntimeError(_http_error_message(
            exc,
            service="main LLM",
            endpoint=cfg.base_url,
            path="chat/completions",
        )) from exc
    except requests.RequestException as exc:
        raise RuntimeError(f"OpenAI-compatible stream failed: {exc}") from exc
    finally:
        if response is not None:
            response.close()

    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    final_content = "".join(content_parts)
    # If reasoning ran away and produced no actual answer, surface a clear note
    # instead of an empty turn, so the loop finalizes usefully rather than looping.
    if reasoning_runaway and not final_content.strip() and not calls:
        final_content = (
            "Рассуждение зациклилось и было прервано. Переформулируй вопрос "
            "или отключи «Мозг» для этой задачи."
        )
    # A degenerate answer was cut mid-loop: collapse the accumulated repeats so
    # the surviving text reads once, and note the cut.
    if content_runaway:
        final_content = _collapse_repeated_paragraphs(final_content).rstrip()
        final_content += "\n\n[генерация прервана: ответ начал зацикливаться]"
    yield {
        "type": "message",
        "response": {
            "model": model or cfg.model,
            "message": {
                "role": "assistant",
                "content": final_content,
                "reasoning_content": "".join(reasoning_parts),
                "tool_calls": [calls[index] for index in sorted(calls)],
            },
            "done": True,
            "reasoning_runaway": reasoning_runaway,
            "content_runaway": content_runaway,
            "prompt_eval_count": prompt_tokens,
            "eval_count": completion_tokens,
            "total_duration": time.monotonic_ns() - started,
            "provider": cfg.provider,
        },
    }


def list_models() -> list[dict[str, Any]]:
    cfg = local_llm_config()
    if not cfg.enabled:
        return []
    try:
        response = requests.get(
            f"{cfg.base_url}/models",
            headers=_headers(cfg),
            timeout=cfg.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
    except requests.HTTPError as exc:
        raise RuntimeError(_http_error_message(exc)) from exc
    except requests.RequestException as exc:
        raise RuntimeError(f"OpenAI-compatible models request failed: {exc}") from exc
    except ValueError as exc:
        raise RuntimeError("OpenAI-compatible provider returned invalid JSON") from exc

    models = data.get("data") if isinstance(data.get("data"), list) else []
    result: list[dict[str, Any]] = []
    for item in models:
        if not isinstance(item, dict):
            continue
        meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
        model_id = str(item.get("id") or "").strip()
        if not model_id:
            continue
        context_window = (
            _positive_int(item.get("n_ctx"))
            or _positive_int(item.get("context_window"))
            or _positive_int(item.get("context_length"))
            or _positive_int(item.get("max_context_length"))
            or _positive_int(meta.get("n_ctx"))
            or cfg.context_window
        )
        result.append(
            {
                "name": model_id,
                "model": model_id,
                "size": 0,
                "modified_at": "",
                "digest": str(item.get("root") or ""),
                "provider": cfg.provider,
                "context_window": context_window,
                "n_ctx": context_window,
            }
        )
    return result


def server_context_window(*, fresh: bool = False) -> int | None:
    """Authoritative context window (n_ctx) of the live llama.cpp server.

    Read from the server's ``/props`` endpoint
    (``default_generation_settings.n_ctx``) — the ONLY place the loaded window is
    exposed. ``/v1/models`` omits it, so callers that trust ``/models`` silently
    fall back to the config default (e.g. 128k) and over-size prompts past the
    server's real window (e.g. 64k), which the server then truncates/errors —
    read as "the model stops holding context". Returns None when the server is
    unreachable or the payload is unparseable; live model-call paths must fail
    closed rather than substitute a possibly stale configured value.

    ``fresh=True`` bypasses the TTL cache: a NEW model run must see a model /
    ``-c`` change on the same base_url immediately (next run, no restart, no
    two-minute stale window). The TTL cache stays for passive consumers
    (meter previews, drift probes) where one probe per ``_PROPS_CTX_TTL_S``
    is enough.
    """
    cfg = local_llm_config()
    if not cfg.enabled:
        return None
    now = time.monotonic()
    if not fresh:
        cached = _props_ctx_cache.get(cfg.base_url)
        if cached is not None and (now - cached[0]) < _PROPS_CTX_TTL_S:
            return cached[1]
    value: int | None = None
    try:
        # base_url ends with /v1 (OpenAI-compat); /props lives at the server root.
        root = cfg.base_url[:-3].rstrip("/") if cfg.base_url.endswith("/v1") else cfg.base_url
        response = requests.get(
            f"{root}/props",
            headers=_headers(cfg),
            timeout=min(8.0, cfg.timeout_seconds),
        )
        response.raise_for_status()
        data = response.json()
        gen = data.get("default_generation_settings")
        if isinstance(gen, dict):
            value = _positive_int(gen.get("n_ctx"))
        if value is None:
            value = _positive_int(data.get("n_ctx"))
    except Exception:
        value = None
    _props_ctx_cache[cfg.base_url] = (now, value)
    return value
