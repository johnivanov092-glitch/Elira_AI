from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Generator

import requests

logger = logging.getLogger(__name__)


_TRUE_VALUES = {"1", "true", "yes", "on"}
LOCAL_EMBED_DIMENSION = 1024


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


class LLMStreamCancelHandle:
    """Thread-safe ownership of one streaming provider response.

    Agent cancellation happens on a different thread while the provider may be
    blocked before its first SSE line. Closing the bound Response interrupts the
    socket read; closing before bind makes a later response close immediately.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._response: requests.Response | None = None
        self._failed_responses: list[requests.Response] = []
        self._closed = False

    def bind(self, response: requests.Response) -> None:
        close_now = False
        with self._lock:
            if self._closed:
                close_now = True
            else:
                self._response = response
        if close_now:
            with self._close_lock:
                try:
                    response.close()
                except Exception:
                    with self._lock:
                        self._failed_responses.append(response)
                    raise

    def release(self, response: requests.Response) -> None:
        with self._lock:
            if self._response is response:
                self._response = None
            self._failed_responses = [item for item in self._failed_responses if item is not response]

    def close(self) -> None:
        # A failed transport close remains owned and retryable. Serialize close
        # calls so concurrent Stop/finally cannot acknowledge it prematurely.
        with self._close_lock:
            with self._lock:
                self._closed = True
                responses = list(self._failed_responses)
                if self._response is not None:
                    responses.append(self._response)
            errors: list[Exception] = []
            for response in responses:
                try:
                    response.close()
                except Exception as exc:
                    errors.append(exc)
                else:
                    self.release(response)
            if errors:
                raise errors[0]

    @property
    def is_closed(self) -> bool:
        with self._lock:
            return self._closed


def _stream_cancel_handle(options: dict[str, Any]) -> LLMStreamCancelHandle | None:
    handle = options.get("_stream_cancel_handle")
    return handle if isinstance(handle, LLMStreamCancelHandle) else None


def _bind_cancelable_response(
    response: requests.Response,
    cancel_handle: LLMStreamCancelHandle | None,
) -> None:
    if cancel_handle is not None:
        cancel_handle.bind(response)


def _close_cancelable_response(
    response: requests.Response | None,
    cancel_handle: LLMStreamCancelHandle | None,
) -> None:
    if response is None:
        return
    response.close()
    if cancel_handle is not None:
        cancel_handle.release(response)


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
_sampling_backends: dict[str, str] = {}
_sampling_omissions: set[tuple[str, str, tuple[str, ...]]] = set()


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


def _non_negative_float(value: Any) -> float:
    try:
        return max(0.0, float(value or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _chat_http_timeout(requested: float | None, configured: float) -> tuple[float, None]:
    """Keep a finite connect timeout but never impose a generation read deadline."""
    try:
        connect_timeout = float(requested if requested is not None else configured)
    except (TypeError, ValueError):
        connect_timeout = 30.0
    return max(0.1, connect_timeout), None


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
    dimension: int


def local_embed_config() -> LocalEmbedConfig:
    return LocalEmbedConfig(
        enabled=_env_bool("LOCAL_EMBED_ENABLED"),
        base_url=os.getenv("LOCAL_EMBED_BASE_URL", "http://192.168.88.15:8001/v1").rstrip("/"),
        model=os.getenv("LOCAL_EMBED_MODEL", "local-embed").strip() or "local-embed",
        api_key=os.getenv("LOCAL_EMBED_API_KEY", "local").strip() or "local",
        timeout_seconds=_env_float("LOCAL_EMBED_TIMEOUT_SECONDS", 30.0),
        dimension=LOCAL_EMBED_DIMENSION,
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
            try:
                vector = [float(x) for x in vec]
            except (TypeError, ValueError):
                return None
            if len(vector) != cfg.dimension:
                logger.warning(
                    "embedding_dimension_mismatch model=%s expected=%d actual=%d",
                    cfg.model,
                    cfg.dimension,
                    len(vector),
                )
                return None
            return vector
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
            reasoning = raw_message.get("reasoning_content") or raw_message.get("reasoning")
            if isinstance(reasoning, str) and reasoning:
                # Qwen's preserved-thinking history uses reasoning_content;
                # vLLM also accepts/returns the reasoning alias.
                item["reasoning_content"] = reasoning
                item["reasoning"] = reasoning
            raw_calls = raw_message.get("tool_calls")
            if isinstance(raw_calls, list) and raw_calls:
                calls = [
                    call for idx, raw_call in enumerate(raw_calls)
                    if (call := _request_tool_call(raw_call, message_index=message_index, call_index=idx)) is not None
                ]
                if calls:
                    item["tool_calls"] = calls
                    pending_tool_call_ids.extend(str(call["id"]) for call in calls)
            if content_text.strip() or item.get("tool_calls") or item.get("reasoning_content"):
                normalized.append(item)
            continue

        if role == "tool":
            tool_call_id = str(raw_message.get("tool_call_id") or "").strip()
            if not tool_call_id and pending_tool_call_ids:
                tool_call_id = pending_tool_call_ids.pop(0)
            elif tool_call_id in pending_tool_call_ids:
                pending_tool_call_ids.remove(tool_call_id)
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
        and not normalized[-1].get("reasoning_content")
        and not normalized[-2].get("reasoning_content")
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


def _openai_usage_metrics(
    usage: dict[str, Any],
    timings: dict[str, Any],
) -> dict[str, Any]:
    """Normalize llama.cpp's OpenAI usage/timing extension fields."""
    prompt_tokens = _positive_int(usage.get("prompt_tokens")) or 0
    prompt_details = (
        usage.get("prompt_tokens_details")
        if isinstance(usage.get("prompt_tokens_details"), dict)
        else {}
    )
    cached_prompt_tokens = _positive_int(prompt_details.get("cached_tokens"))
    if cached_prompt_tokens is None:
        cached_prompt_tokens = _positive_int(timings.get("cache_n")) or 0
    if prompt_tokens > 0:
        cached_prompt_tokens = min(cached_prompt_tokens, prompt_tokens)
    else:
        cached_prompt_tokens = 0
    cache_hit_ratio = (
        cached_prompt_tokens / prompt_tokens if prompt_tokens > 0 else 0.0
    )
    return {
        "cached_prompt_tokens": cached_prompt_tokens,
        "prompt_cache_hit_ratio": cache_hit_ratio,
        "prompt_eval_duration": int(
            _non_negative_float(timings.get("prompt_ms")) * 1_000_000
        ),
        "eval_duration": int(
            _non_negative_float(timings.get("predicted_ms")) * 1_000_000
        ),
        "server_prompt_tokens_per_second": _non_negative_float(
            timings.get("prompt_per_second")
        ),
        "server_tokens_per_second": _non_negative_float(
            timings.get("predicted_per_second")
        ),
    }


def _local_llm_response(data: dict[str, Any], *, elapsed_ns: int) -> dict[str, Any]:
    choices = data.get("choices") if isinstance(data.get("choices"), list) else []
    first = choices[0] if choices and isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first.get("message"), dict) else {}
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    timings = data.get("timings") if isinstance(data.get("timings"), dict) else {}
    prompt_tokens = _positive_int(usage.get("prompt_tokens")) or 0
    completion_tokens = _positive_int(usage.get("completion_tokens")) or 0
    return {
        "model": str(data.get("model") or ""),
        "message": {
            "role": str(message.get("role") or "assistant"),
            "content": str(message.get("content") or ""),
            # Reasoning arrives in a separate field when thinking is enabled
            # (--jinja server); surface it so callers can show it apart from the
            # answer. Empty string when thinking is off — never mixed into content.
            # vLLM names the same field `reasoning`.
            "reasoning_content": str(message.get("reasoning_content") or message.get("reasoning") or ""),
            "tool_calls": _normalize_tool_calls(message.get("tool_calls")),
        },
        "done": True,
        "finish_reason": first.get("finish_reason"),
        "prompt_eval_count": prompt_tokens,
        "eval_count": completion_tokens,
        "total_duration": elapsed_ns,
        **_openai_usage_metrics(usage, timings),
        "ttft_ms": 0,
        "provider": local_llm_config().provider,
        "raw_openai": data,
    }


def _guard_context_request(
    messages: list[dict[str, Any]],
    *,
    max_tokens: Any,
    requested_ctx: Any,
) -> None:
    """Compatibility hook; the live server is authoritative for context fit."""
    del messages, max_tokens, requested_ctx


def _apply_thinking_option(payload: dict[str, Any], opts: dict[str, Any]) -> None:
    """Pass Qwen's explicit reasoning controls to ``--jinja`` llama-server."""
    ctk = opts.get("chat_template_kwargs")
    if isinstance(ctk, dict) and ctk:
        payload["chat_template_kwargs"] = ctk
    effort = str(opts.get("reasoning_effort") or "").strip().lower()
    if effort in {"none", "low", "medium", "xhigh"}:
        payload["reasoning_effort"] = effort


def _apply_response_format(payload: dict[str, Any], opts: dict[str, Any]) -> None:
    """Forward an explicit JSON-schema format without sharing mutable options."""
    if "response_format" not in opts:
        return
    response_format = opts["response_format"]
    if not isinstance(response_format, dict) or response_format.get("type") != "json_schema":
        raise ValueError("response_format must be an object with type json_schema")
    structured = response_format.get("json_schema")
    if not isinstance(structured, dict):
        raise ValueError("response_format.json_schema must be an object")
    if not isinstance(structured.get("name"), str) or not structured["name"].strip():
        raise ValueError("response_format.json_schema.name must be a non-empty string")
    if not isinstance(structured.get("schema"), dict):
        raise ValueError("response_format.json_schema.schema must be an object")
    try:
        detached = json.loads(json.dumps(response_format, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ValueError("response_format must contain only JSON-compatible values") from exc
    if detached != response_format:
        raise ValueError("response_format must contain JSON values and string object keys")
    payload["response_format"] = detached


# Samplers are backend-specific. A permissive OpenAI request parser accepting
# unknown keys does not mean those keys reach its sampling implementation.
_SAMPLING_EXTRA_KEYS = frozenset({
    "dry_multiplier", "dry_base", "dry_allowed_length", "dry_penalty_last_n",
    "dry_sequence_breakers", "repeat_penalty", "repeat_last_n",
})


def _apply_sampling_extra(payload: dict[str, Any], opts: dict[str, Any]) -> None:
    """Use capabilities observed by the existing context probe; never map DRY to a different penalty."""
    extra = opts.get("sampling")
    if isinstance(extra, dict):
        endpoint = local_llm_config().base_url
        backend = _sampling_backends.get(endpoint, "unknown")
        allowed = (_SAMPLING_EXTRA_KEYS if backend == "llama.cpp" else
                   frozenset({"repetition_penalty", "presence_penalty", "frequency_penalty", "top_k", "min_p"})
                   if backend == "vllm" else frozenset())
        omitted = tuple(sorted(key for key, value in extra.items() if value is not None and key not in allowed))
        identity = (endpoint, backend, omitted)
        if omitted and identity not in _sampling_omissions:
            _sampling_omissions.add(identity)
            logger.warning("Sampling backend=%s: unsupported parameters omitted: %s; server defaults retained",
                           backend, ", ".join(omitted))
        for key, value in extra.items():
            if key in allowed and value is not None:
                payload[key] = value


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


def _apply_max_tokens_limit(
    payload: dict[str, Any], options: dict[str, Any], *, configured_max: Any,
) -> None:
    """Apply a caller's per-request output cap without widening config policy."""
    requested = _positive_int(options.get("max_tokens"))
    configured = _positive_int(configured_max)
    limits = [value for value in (requested, configured) if value]
    if limits:
        payload["max_tokens"] = min(limits)


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

    opts = options or {}
    cancel_handle = _stream_cancel_handle(opts)
    if cancel_handle is not None:
        # A cancellable synchronous caller (notably context compaction) still
        # uses server-side SSE. Client-side ``requests(..., stream=True)`` alone
        # would leave payload.stream=false and llama.cpp could withhold the
        # Response until generation ended, making Stop ineffective.
        final_response: dict[str, Any] | None = None
        for event in chat_completion_event_stream(
            model=model,
            messages=messages,
            tools=tools,
            options=opts,
            timeout=timeout,
        ):
            if event.get("type") == "message" and isinstance(event.get("response"), dict):
                final_response = dict(event["response"])
        if final_response is not None:
            return final_response
        if cancel_handle.is_closed:
            raise RuntimeError("OpenAI-compatible request cancelled")
        raise RuntimeError("OpenAI-compatible stream ended without a response")

    normalized_messages = _normalize_messages_for_request(messages)
    payload: dict[str, Any] = {
        "model": model or cfg.model,
        "messages": normalized_messages,
        "cache_prompt": True,
    }
    if tools:
        payload["tools"] = tools
    _apply_max_tokens_limit(payload, opts, configured_max=cfg.max_tokens)
    if "temperature" in opts:
        payload["temperature"] = opts["temperature"]
    if "top_p" in opts:
        payload["top_p"] = opts["top_p"]
    _apply_thinking_option(payload, opts)
    _apply_response_format(payload, opts)
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
                timeout=_chat_http_timeout(timeout, cfg.timeout_seconds),
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
        "cache_prompt": True,
    }
    if tools:
        payload["tools"] = tools
    opts = options or {}
    _apply_max_tokens_limit(payload, opts, configured_max=cfg.max_tokens)
    if "temperature" in opts:
        payload["temperature"] = opts["temperature"]
    if "top_p" in opts:
        payload["top_p"] = opts["top_p"]
    _apply_thinking_option(payload, opts)
    _apply_response_format(payload, opts)
    _apply_sampling_extra(payload, opts)
    _guard_context_request(
        normalized_messages,
        max_tokens=payload.get("max_tokens"),
        requested_ctx=_request_context_limit(opts, configured_context=cfg.context_window),
    )

    response: requests.Response | None = None
    cancel_handle = _stream_cancel_handle(opts)
    started = time.monotonic_ns()
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    calls: dict[int, dict[str, Any]] = {}
    usage: dict[str, Any] = {}
    timings: dict[str, Any] = {}
    finish_reason: str | None = None
    first_token_ns: int | None = None
    try:
        response = requests.post(
            f"{cfg.base_url}/chat/completions",
            headers=_headers(cfg),
            json=payload,
            timeout=_chat_http_timeout(timeout, cfg.timeout_seconds),
            stream=True,
        )
        _bind_cancelable_response(response, cancel_handle)
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
            if isinstance(data.get("timings"), dict):
                timings = dict(data["timings"])
            choices = data.get("choices") if isinstance(data.get("choices"), list) else []
            first = choices[0] if choices and isinstance(choices[0], dict) else {}
            if isinstance(first.get("finish_reason"), str):
                finish_reason = first["finish_reason"]
            delta = first.get("delta") if isinstance(first.get("delta"), dict) else {}
            if first_token_ns is None and any(
                delta.get(field) for field in ("reasoning_content", "reasoning", "content", "tool_calls")
            ):
                first_token_ns = time.monotonic_ns()
            # Thinking (--jinja) streams the chain-of-thought in its own
            # `reasoning_content` field (vLLM: `reasoning`), separate from the
            # answer's `content`. Route it to a distinct event so callers show
            # it apart from (and never blended into) the final answer.
            rtoken = str(delta.get("reasoning_content") or delta.get("reasoning") or "")
            if rtoken:
                reasoning_parts.append(rtoken)
                yield {"type": "reasoning", "content": rtoken}
            token = str(delta.get("content") or "")
            if token:
                content_parts.append(token)
                yield {"type": "delta", "content": token}
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
        _close_cancelable_response(response, cancel_handle)

    prompt_tokens = _positive_int(usage.get("prompt_tokens")) or 0
    completion_tokens = _positive_int(usage.get("completion_tokens")) or 0
    final_content = "".join(content_parts)
    metrics = _openai_usage_metrics(usage, timings)
    ttft_ms = (
        max(0, int((first_token_ns - started) / 1_000_000))
        if first_token_ns is not None
        else 0
    )
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
            "finish_reason": finish_reason,
            "reasoning_runaway": False,
            "content_runaway": False,
            "prompt_eval_count": prompt_tokens,
            "eval_count": completion_tokens,
            "total_duration": time.monotonic_ns() - started,
            **metrics,
            "ttft_ms": ttft_ms,
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


def _models_context_window(cfg: OpenAICompatibleConfig) -> int | None:
    """Served window from vLLM's ``/v1/models`` ``max_model_len``.

    llama.cpp omits this field, so its ``/models`` can never yield a stale
    config-sized window here. Prefers the configured model id, then any entry.
    """
    response = requests.get(
        f"{cfg.base_url}/models",
        headers=_headers(cfg),
        timeout=min(8.0, cfg.timeout_seconds),
    )
    response.raise_for_status()
    data = response.json()
    models = data.get("data") if isinstance(data, dict) else None
    windows: dict[str, int] = {}
    for item in models if isinstance(models, list) else []:
        if isinstance(item, dict):
            if item.get("owned_by") == "vllm":
                _sampling_backends[cfg.base_url] = "vllm"
            window = _positive_int(item.get("max_model_len"))
            if window:
                windows[str(item.get("id") or "")] = window
    return windows.get(cfg.model) or next(iter(windows.values()), None)


def server_context_window(*, fresh: bool = False) -> int | None:
    """Authoritative context window of the live inference server.

    llama.cpp: ``/props`` (``default_generation_settings.n_ctx``) — its
    ``/v1/models`` omits the loaded window, so callers that trust ``/models``
    silently fall back to the config default (e.g. 128k) and over-size prompts
    past the server's real window (e.g. 64k), which the server then
    truncates/errors — read as "the model stops holding context".
    vLLM has no ``/props``; its ``/v1/models`` ``max_model_len`` is the served
    limit. Returns None when the server is unreachable / unparseable.
    ``fresh=True`` bypasses the passive-consumer TTL cache so the next model
    run sees a model or ``-c`` swap immediately.
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
    _sampling_backends.pop(cfg.base_url, None)
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
            _sampling_backends[cfg.base_url] = "llama.cpp"
            value = _positive_int(gen.get("n_ctx"))
        if value is None:
            value = _positive_int(data.get("n_ctx"))
    except Exception:
        value = None
    if value is None:
        try:
            value = _models_context_window(cfg)
        except Exception:
            value = None
    _props_ctx_cache[cfg.base_url] = (now, value)
    return value
