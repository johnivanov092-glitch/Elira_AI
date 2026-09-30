"""One provider exchange and the existing code-agent sampling contract.

The coordinator chooses the next turn; this module only adapts its provider
call to the established heartbeat, reasoning, delta and response events.
"""
from __future__ import annotations

import queue
import threading
import time
from typing import Any, Callable, Iterator

from app.application.code_agent.inline_tool_calls import _contains_tool_trace, _extract_inline_tool_calls
from app.application.code_agent.loop_helpers import _strip_think_blocks
from app.application.monitoring.inference import extract_llm_usage
from app.application.persona.service import mode_temperature
from app.infrastructure.llm.openai_compatible import (
    LLMStreamCancelHandle,
    chat_completion_event_stream,
    is_local_llm_model,
    local_llm_config,
)

# Per-request DRY sampler params (llama.cpp accepts them in the request body —
# verified against the live server). DRY penalises repeated token sequences at
# sampling time, so the model can't lock into a degenerate "same paragraph
# forever" loop. Originally think-only; extended to ALL runs after a live
# non-think run produced a paragraph repeated ×20 in the ANSWER channel (the
# content path had no anti-repeat protection at all). Safe for codegen: DRY's
# default sequence breakers ("\n" etc.) reset matching at line boundaries, so
# legitimate repeated code structure isn't penalised the way run-on prose is.
# Bound the lookback; it can still include recent HISTORY on short replies.
# Allow short repeated facts/names: length=2 made Qwen mutate Elira after three
# identity questions. In a fixed-history/seed live A/B, length=12 preserved the
# exact name with thinking both off and low, while keeping DRY for long prose.
_DRY_WINDOW_TOKENS = 1024
_ANTI_REPEAT_SAMPLING = {
    "dry_multiplier": 0.8,
    "dry_base": 1.75,
    "dry_allowed_length": 12,
    "dry_penalty_last_n": _DRY_WINDOW_TOKENS,
    # Reset DRY matching at these separators so LEGITIMATELY-repeated IPs / MACs /
    # numbers / versions / paths (192.168.88.1, 2C-C8-1B, /24, v1.0) are not seen as
    # a penalizable repeat. Without "." DRY penalised the repeated octets of an IP,
    # and the model MUTATED them to dodge the penalty (live: 192.168→192.169→192.166
    # →192.170… during a network scan, plus a walk through 8.8.8.8/9.9.9.9/6.6.6.6).
    # Keeps the llama.cpp defaults (\n : " *) so repeated PROSE — the ×20-paragraph
    # runaway — is still caught (a repeated sentence resets only at its own period).
    # File-path delimiters are breakers too: without "_" / "\\", copying a long
    # path verbatim from glob into read_file is treated as repetition and Qwen
    # abbreviates or mutates the tool argument.
    # Digits are breakers too: a repeated number/model-code (RTX 5090 in every table
    # row, a price repeated down a column) was DRY-penalised and the model dropped a
    # digit to dodge it (live: "5090"→"509"/"090"). A digit resets the match, so
    # numbers survive verbatim; word-based degeneration (no digits) is still caught.
    "dry_sequence_breakers": [
        "\n", ":", "\"", "*", ".", "-", "/", "\\", "_", ",", ";", "=",
        "0", "1", "2", "3", "4", "5", "6", "7", "8", "9",
    ],
}


_REASONING_EFFORTS = frozenset({"none", "low", "medium", "xhigh"})


def _normalize_reasoning_effort(value: Any, *, thinking: bool = False) -> str:
    """Normalize the public effort selector while preserving the legacy bool."""
    effort = str(value or "").strip().lower()
    if effort in _REASONING_EFFORTS:
        return effort
    return "xhigh" if thinking else "none"


def _thinking_template_kwargs(effort: str | bool) -> dict[str, bool | str]:
    """Build Qwen's reasoning contract; ``none`` fully disables thinking."""
    normalized = _normalize_reasoning_effort(
        None if isinstance(effort, bool) else effort,
        thinking=bool(effort) if isinstance(effort, bool) else False,
    )
    if normalized != "none":
        return {
            "enable_thinking": True,
            "reasoning_effort": normalized,
        }
    return {
        "enable_thinking": False,
        "reasoning_effort": "none",
    }


# Completion is model-owned: evidence is recorded for observability, but the
# runtime never forces extra turns, rewrites the answer, or auto-runs verification.
_LLM_HEARTBEAT_EVERY = 10.0
_LLM_CANCEL_POLL_SECONDS = 0.1
def _effective_temperature(profile_name: str, role: str | None) -> float:
    """Compatibility arguments never switch the single personality's sampling."""
    return float(mode_temperature(profile_name))


def _chat_events(
    *,
    chat_fn: Callable[..., dict[str, Any]],
    chat_stream_fn: Callable[..., Any] | None,
    kwargs: dict[str, Any],
    cancel_event: "threading.Event | None" = None,
    cancel_handle: LLMStreamCancelHandle | None = None,
) -> Iterator[dict[str, Any]]:
    """Run a blocking provider call without leaving the SSE stream silent.

    When ``cancel_event`` is set mid-stream the worker stops pulling tokens
    and closes the underlying generator (which closes the upstream HTTP
    response), so pressing Stop actually frees the server instead of letting
    it generate the full answer into a queue nobody reads.
    """
    events: queue.Queue[tuple[str, Any]] = queue.Queue()
    upstream_handle = cancel_handle or LLMStreamCancelHandle()
    worker_kwargs = dict(kwargs)
    worker_options = dict(worker_kwargs.get("options") or {})
    worker_options["_stream_cancel_handle"] = upstream_handle
    worker_kwargs["options"] = worker_options

    def worker() -> None:
        stream = None
        try:
            if chat_stream_fn is None:
                events.put(("response", chat_fn(**worker_kwargs)))
                return
            final_response: dict[str, Any] | None = None
            collected: list[str] = []
            stream = chat_stream_fn(**worker_kwargs)
            for item in stream:
                if cancel_event is not None and cancel_event.is_set():
                    break
                if isinstance(item, str):
                    collected.append(item)
                    events.put(("delta", item))
                    continue
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("type") or "")
                if item_type == "delta":
                    text = str(item.get("content") or item.get("text") or "")
                    if text:
                        collected.append(text)
                        events.put(("delta", text))
                elif item_type == "reasoning":
                    # Thinking tokens — relayed on their own channel, never
                    # folded into `collected` (which becomes the answer).
                    rtext = str(item.get("content") or item.get("text") or "")
                    if rtext:
                        events.put(("reasoning", rtext))
                elif item_type == "message":
                    response = item.get("response")
                    if isinstance(response, dict):
                        final_response = response
            if final_response is None:
                final_response = {
                    "message": {"content": "".join(collected), "tool_calls": []},
                }
            events.put(("response", final_response))
        except Exception as exc:  # propagated in the caller thread
            events.put(("error", exc))
        finally:
            # Closing the generator triggers its `finally`, which calls
            # response.close() and frees the upstream llama.cpp connection.
            close = getattr(stream, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
            events.put(("done", None))

    threading.Thread(target=worker, name="elira-code-agent-llm", daemon=True).start()
    done = False
    next_heartbeat = time.monotonic() + _LLM_HEARTBEAT_EVERY
    try:
        while not done:
            try:
                timeout = max(
                    0.001,
                    min(_LLM_CANCEL_POLL_SECONDS, next_heartbeat - time.monotonic()),
                )
                kind, value = events.get(timeout=timeout)
            except queue.Empty:
                if cancel_event is not None and cancel_event.is_set():
                    return
                if time.monotonic() >= next_heartbeat:
                    next_heartbeat = time.monotonic() + _LLM_HEARTBEAT_EVERY
                    yield {"type": "heartbeat"}
                continue
            if kind == "done":
                done = True
            elif kind == "error":
                raise value
            else:
                yield {"type": kind, "value": value}
    finally:
        # The run owns the shared handle across planning, compaction and normal
        # generation. Only cancellation makes it permanently closed; normal
        # completion merely releases the provider response for the next call.
        if cancel_event is not None and cancel_event.is_set():
            upstream_handle.close()


def _local_chat_stream(**kwargs: Any) -> Iterator[dict[str, Any]]:
    model = str(kwargs.get("model") or "")
    if not is_local_llm_model(model):
        expected = local_llm_config().model
        raise RuntimeError(f"Local llama-server provider expects model '{expected}', got '{model}'.")
    yield from chat_completion_event_stream(
        model=model,
        messages=list(kwargs.get("messages") or []),
        tools=kwargs.get("tools"),
        options=kwargs.get("options"),
    )


def decode_response(response: dict[str, Any]) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """Decode the final payload before telemetry and its visible usage event."""
    message = (response or {}).get("message") or {}
    content = _strip_think_blocks(message.get("content") or "").strip()
    tool_calls = message.get("tool_calls") or []
    usage = extract_llm_usage(response)
    return content, tool_calls, usage


def recover_tool_calls(content: str, tool_calls: list[dict[str, Any]],
                       tool_names: set[str]) -> tuple[str, list[dict[str, Any]]]:
    """Recover inline calls only after the coordinator publishes usage."""
    if not tool_calls and content:
        inline_calls = _extract_inline_tool_calls(content, tool_names)
        if inline_calls:
            tool_calls = inline_calls
            content = ""
    return content, tool_calls


def stream_model_turn(*, chat, stream_chat, kwargs: dict[str, Any],
                      cancel_event: threading.Event, upstream_cancel_handle: LLMStreamCancelHandle,
                      step: int):
    """Relay one draft/reasoning exchange and return its final provider payload."""
    response: dict[str, Any] = {}
    pending_delta = ""
    suppress_deltas = False
    for llm_event in _chat_events(
        chat_fn=chat,
        chat_stream_fn=stream_chat,
        kwargs=kwargs,
        cancel_event=cancel_event,
        cancel_handle=upstream_cancel_handle,
    ):
        if cancel_event.is_set():
            break
        if llm_event["type"] == "heartbeat":
            yield {"type": "heartbeat", "step": step}
            continue
        if llm_event["type"] == "response":
            response = dict(llm_event["value"] or {})
            continue
        if llm_event["type"] == "reasoning":
            # Surface thinking tokens on their own SSE event so the UI
            # can show them in a separate, collapsible block. Kept out
            # of the answer stream and out of message history.
            rtext = str(llm_event["value"] or "")
            if rtext:
                yield {"type": "reasoning_delta", "step": step, "text": rtext}
            continue
        if llm_event["type"] != "delta":
            continue
        pending_delta += str(llm_event["value"] or "")
        marker_text = pending_delta.lower()
        if "<tool" in marker_text or "<function=" in marker_text:
            suppress_deltas = True
            pending_delta = ""
            continue
        if not suppress_deltas and len(pending_delta) > 32:
            visible, pending_delta = pending_delta[:-32], pending_delta[-32:]
            if visible:
                yield {"type": "delta", "step": step, "text": visible, "answer_state": "draft"}
    response_content = str(((response.get("message") or {}).get("content") or ""))
    if not suppress_deltas and not _contains_tool_trace(response_content) and pending_delta:
        yield {"type": "delta", "step": step, "text": pending_delta, "answer_state": "draft"}
    return response
