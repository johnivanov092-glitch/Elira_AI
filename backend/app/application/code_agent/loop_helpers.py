"""Code-agent loop helpers — text/format, approval, context-window, RAG and
telemetry utilities used by the streaming loop.

Extracted verbatim from ``agent_loop.py`` (no behaviour change) to shrink that
module. This is a *leaf*: it imports nothing from ``agent_loop`` (only from
``.history`` for ``summarize_history``, which is itself a leaf), so re-exporting
these names back into ``agent_loop`` forms no import cycle. The core loop calls
every helper here through the ``agent_loop`` module namespace, so tests that
``patch`` these names on ``agent_loop`` (e.g. ``_approval_status``,
``_APPROVAL_POLL_INTERVAL``, ``_record_code_route_metric``,
``_try_remember_turn``) keep working unchanged.
"""
from __future__ import annotations

import logging
import re
import threading
from pathlib import Path
from typing import Any, Callable

from app.application.projects.scope import project_scope_id
from app.application.code_agent.history import summarize_history
from app.infrastructure.text import truncate_middle
from app.application.code_agent.tool_policy import CRITICAL_TOOLS, EDIT_ONLY_TOOLS
from app.change_executor.policy import AUTO, decide_approval, evidence_for_tool_call

logger = logging.getLogger(__name__)


# Patterns that say "user explicitly wants you to RUN something". When
# present, we suffix the user message with an inline reminder — small
# but effective at unsticking models that hallucinate "I have no access".
_EXECUTION_INTENT = re.compile(
    r"(?<!\w)(запусти|запустить|выполни|выполнить|проверь|проверить|"
    r"создай файл|создай тест|run|execute|run tests|run it|"
    r"сделай это|поправь и запусти|"
    r"можно\s+ли.{0,80}(?:сделать|добавить|изменить|исправить|обновить))(?!\w)",
    re.IGNORECASE | re.UNICODE,
)
_EXPLANATION_ONLY = re.compile(
    r"(?:только\s+объясни|не\s+меняй|ничего\s+не\s+меняй|без\s+изменений)",
    re.IGNORECASE | re.UNICODE,
)


def _maybe_inject_execution_reminder(user_message: str) -> str:
    """If the user's wording clearly demands execution, append a short
    reminder telling the model 'this is a tool-use turn, not a
    text-answer turn'. Some local tool-calling models occasionally drift
    into 'helpful explanation' mode otherwise.
    """
    if (
        _EXECUTION_INTENT.search(user_message or "")
        and not _EXPLANATION_ONLY.search(user_message or "")
    ):
        return (
            user_message
            + "\n\n[reminder] Это задача на выполнение. Используй инструменты "
            + "(run_bash / write_file / read_file и т.д.) и сделай это сам. "
            + "Не объясняй мне как запустить — запусти."
        )
    return user_message


def _truncate(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[... truncated]"


# How much of a tool's text output to send back to the LLM as the
# 'tool' message on the next turn. Locally we don't pay for tokens, but
# `num_ctx` is a hard limit — a single 80K-char `pytest -v` dump would
# eat the entire context and start truncating the system prompt + user
# task. 12000 chars is ~3000 tokens ≈ 18% of the default 16K context per
# call, leaving room for several tool calls per turn plus the model's
# own reasoning.
TOOL_RESULT_LLM_LIMIT = 12000


def _truncate_for_llm(text: str, limit: int = TOOL_RESULT_LLM_LIMIT) -> str:
    """Truncate a tool output before feeding it back to the LLM.

    Strategy: if the output fits, return it unchanged. Otherwise, keep
    the start (typical context of what happened) *and* the end (final
    lines / exit code / last error) — drop the middle. This matters for
    `run_bash` long outputs where the exit code + stack trace at the
    bottom is the most useful part, and for `read_file` of large files
    where the start has imports / docstring and the end has main code.
    """
    return truncate_middle(text, limit)


def _messages_char_count(messages: list[dict[str, Any]]) -> int:
    total = 0
    for item in messages:
        content = item.get("content")
        if isinstance(content, str):
            total += len(content)
    return total


WRAP_UP_PROMPT = (
    "[Прогон оборван: {reason}.] Больше НЕ вызывай инструменты. Подведи ЧЕСТНЫЙ "
    "итог СТРОГО по фактам из результатов инструментов выше — не приукрашивай:\n"
    "- Пиши «сделано» ТОЛЬКО если это прямо подтверждает результат конкретного "
    "инструмента выше. Иначе — «не подтверждено» или «не доделано».\n"
    "- НЕ пиши «проверено» / «работает» / «зашёл», если в шагах НЕТ вызова, который "
    "это реально проверил. Предположение — это не проверка.\n"
    "- НЕ придумывай ключи, пароли, вывод команд, пути или значения, которых не "
    "было в результатах инструментов. Нет данных — так и скажи «не получил».\n"
    "- Если шаг завершился ошибкой — напиши, что он ПРОВАЛИЛСЯ, не сглаживай в успех.\n"
    "Формат: что реально сделано (по фактам), что не доделано, следующий шаг."
)


def _short_arg_hint(args: dict[str, Any]) -> str:
    """Most identifying argument of a tool call, for the run's call log."""
    for key in ("path", "command", "pattern", "query"):
        val = args.get(key)
        if isinstance(val, str) and val:
            return val if len(val) <= 60 else val[:60] + "…"
    return ""


def _tool_started_requires_approval_delay(tool_name: str, args: dict[str, Any]) -> bool:
    """Delay live "started" UI until human approval has been granted."""
    try:
        from app.application.tool_registry.runtime import get_tool
        spec = get_tool(tool_name)
    except Exception:
        return False
    if not spec or spec.get("permission") != "require_approval":
        return False
    if tool_name == "run_bash":
        command = str(args.get("command", "")).strip()
        if command:
            try:
                from app.application.code_agent.tools import is_shell_safe
                return not is_shell_safe(command)
            except Exception:
                return True
    return True


# F1: while a tool call waits for human approval the loop pauses and polls
# the approval status. Module-level so tests can shrink the tick.
_APPROVAL_POLL_INTERVAL = 1.5
_APPROVAL_KEEPALIVE_EVERY = 10.0


def _approval_status(approval_id: str) -> str:
    """Current status of an approval row; 'pending' on any lookup problem."""
    try:
        from app.application.monitoring import runtime as _mon
        _mon.expire_old_approvals()
        row = _mon.get_approval(approval_id) or {}
        return str(row.get("status") or "pending")
    except Exception:
        return "pending"


# Permission modes (selector in the composer, mirrored in Settings):
#   "ask"          — every change pauses for the user (default).
#   "accept_edits" — low-risk runtime-reversible work proceeds; impactful work pauses.
#   "bypass"       — normal work proceeds; unprotected high-risk work still pauses.
# Compatibility inventory from tool_policy; the shared policy owns exact-call decisions.
_EDIT_ONLY_TOOLS = EDIT_ONLY_TOOLS


def _mode_auto_approves(permission_mode: str, tool_name: str) -> bool:
    """Compatibility helper for coarse tests; real calls use exact arguments."""
    args = {"command": "noncritical-command"} if tool_name == "run_bash" else {}
    return _call_auto_approves(permission_mode, tool_name, args)


def _call_auto_approves(
    permission_mode: str,
    tool_name: str,
    args: dict[str, Any] | None,
    *,
    channel: str = "local",
) -> bool:
    """Decide one exact call from mode and runtime-owned safety evidence."""
    try:
        evidence = evidence_for_tool_call(tool_name, args)
        return decide_approval(permission_mode, channel, evidence) == AUTO
    except Exception:
        logger.warning("approval policy failed; requiring approval", exc_info=True)
        return False


# Tools that must ALWAYS be confirmed, even in bypass (from tool_policy; shell
# criticality is decided per-command below via is_shell_critical).
_CRITICAL_TOOLS: frozenset[str] = CRITICAL_TOOLS


def _is_critical_call(tool_name: str, args: dict[str, Any] | None) -> bool:
    """A specific call that must NEVER auto-approve — the user confirms it even in
    bypass mode. Covers destructive-but-legitimate shell commands (rm / git reset
    / drop / docker rm / kill / uninstall …) and any tool in _CRITICAL_TOOLS.
    Catastrophic commands are blocked outright elsewhere; this is 'ask, never
    auto'. Keeps bypass = 'no friction for normal work' while still guarding the
    handful of operations that destroy data."""
    if tool_name in _CRITICAL_TOOLS:
        return True
    try:
        return evidence_for_tool_call(tool_name, args).impact == "high"
    except Exception:
        logger.warning("approval classification failed; treating call as critical", exc_info=True)
        return True


_REPEAT_REQUEST_MARKERS = (
    "еще раз", "ещё раз", "повтор", "снова", "заново", "repeat", "again", "same",
)


def _looks_like_repeat_request(text: str) -> bool:
    """True if the user explicitly asked to repeat / say it again, so an identical
    answer is legitimate and the anti-repeat gate must NOT fire."""
    t = (text or "").strip().lower()
    return any(m in t for m in _REPEAT_REQUEST_MARKERS)


# Client-side <think> stripper (safety net). The reasoning/content split relies
# on llama-server's template parsing; if a model swap or llama.cpp update breaks
# it, raw think-blocks would leak into content and then into history. Handles an
# unterminated trailing <think> too (a cut-off generation).
_THINK_BLOCK_RE = re.compile(r"<think>.*?(?:</think>|\Z)", re.DOTALL | re.IGNORECASE)


def _strip_think_blocks(text: str) -> str:
    if "<think" not in (text or "").lower():
        return text or ""
    return _THINK_BLOCK_RE.sub("", text or "")


def _normalized_fingerprint(name: str, args: dict[str, Any]) -> str:
    """Loop-guard fingerprint with whitespace-collapsed string values, so a stray
    space/newline in an argument doesn't make an identical retry look 'new' and
    slip past the repeat counter forever. Structure (offsets, different paths)
    still distinguishes legitimately different calls."""
    import json as _json

    def _norm(value: Any) -> Any:
        if isinstance(value, str):
            return " ".join(value.split())
        if isinstance(value, dict):
            return {str(k): _norm(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_norm(v) for v in value]
        return value

    return _json.dumps(
        {"tool": name, "arguments": _norm(args or {})},
        ensure_ascii=False, sort_keys=True, default=str,
    )


def _norm_answer(text: str) -> str:
    """Normalise an assistant answer for exact-duplicate comparison: collapse all
    whitespace and strip. Deterministic — only answers that are byte-identical
    after normalisation match, so genuinely different replies never trip the
    anti-repeat gate (no fuzzy similarity, no false positives on real work)."""
    return " ".join((text or "").split()).strip()


# "I'm about to do X" verbs/openers that a model emits INSTEAD of calling the
# tool. Covers first-person singular future ("прочитаю", "начну"), first-person
# plural / "давай …" filler ("давай посмотрим", "проверим", "сделаем" — the most
# common Russian stall), and English ("let me", "let's"). Deliberately excludes
# infinitives and 2nd-person imperatives, so a real closing answer that tells the
# USER what THEY can do ("теперь можешь запустить …") does not match.
_INTENT_TO_ACT_RE = re.compile(
    # first-person singular future / intent
    r"(прочита[юя]|перечита[юя]|дочита[юя]|дочитыва|добавл[юя]|сдела[юя]|"
    r"напиш[у]|создам|создаю|измен[юя]|исправл[юя]|запущ[у]|перепиш[у]|"
    r"обновл[юя]|внес[у]|посмотр[юя]|провер[юя]|перейд[у]|начн[уё]|приступ|"
    r"разбер[у]сь|доработа[юя]|реализу[юя]|проанализиру[юя]|допиш[у]|поправл[юя]|"
    # first-person plural ("давай посмотрим", "проверим", "сделаем", …)
    r"посмотрим|глянем|проверим|сделаем|прочитаем|прочтём|прочтем|начнём|начнем|"
    r"разберёмся|разберемся|добавим|исправим|напишем|создадим|обновим|перепишем|поправим|"
    r"давай(те)?\s+(посмотр|глян|провер|сдела|разбер|начн|прочит|добав|исправ|напиш|созда|обнов)|"
    # English
    r"let me\b|let['’]?s\b|let us\b|i['’]?ll\b|i will\b|i['’]?m going to)",
    re.IGNORECASE | re.UNICODE,
)


def _looks_like_intent_without_action(text: str) -> bool:
    """True when the model's prose is a forward-looking plan-to-act ("сейчас
    прочитаю…", "начну с…", "let me read…") rather than a delivered result —
    it announces the NEXT step but (the caller has already checked) calls no
    tool. Conservative on purpose: matches only a first-person intent verb at
    the START or TAIL of the message. The start matters for long design
    monologues: a model can open with "Я сделаю...", spend 300 chars describing
    the plan, and otherwise evade a tail-only check. Used to nudge the agent to
    actually act instead of ending the turn on a promise (a failure mode
    amplified by thinking mode)."""
    t = (text or "").strip()
    if not t:
        return False
    # A delivered answer normally neither STARTS nor ENDS with an unfulfilled
    # first-person action. Bounded windows avoid matching a future-step mention
    # buried inside an otherwise substantive result.
    head = t[:200]
    tail = t[-200:]
    return bool(_INTENT_TO_ACT_RE.search(head) or _INTENT_TO_ACT_RE.search(tail))


# --- Grounding across turns -------------------------------------------------
# conversation_history carries only user + assistant TEXT (tool results are
# dropped — see history._coerce_history). So facts the agent learned via tools
# in an earlier turn (which files exist, what a function is, a command's output)
# vanish, and a later factual follow-up gets answered from priors → confabulation
# (empirically: "add()" → invented "calc.py/multiply/test_calc.py"). We capture a
# compact digest of what the discovery tools revealed this run and hand it back
# next turn as an authoritative context block, so the model grounds instead of
# guessing. Read/inspect tools only — pure actions add no facts worth carrying.
_GROUNDING_FACT_TOOLS = frozenset({
    "project_map", "glob", "grep", "read_file", "run_bash", "run_server",
    "web_search", "web_fetch", "http_api", "browser", "recall", "write_file", "edit_file",
    # Remote work grounds facts too — a remote read/check/write must survive into
    # the next turn's digest, not vanish because it happened over SSH.
    "ssh_run", "ssh_read", "ssh_write", "ssh_run_ps",
    # FIX-4: verifier verdicts are grounded facts AND real progress — a fresh
    # verdict (criterion transition) resets the stuck streak; a repeated identical
    # verdict collapses to the same fact-shape and does NOT count as progress.
    "ssh_assert_contains", "ssh_assert_not_contains", "ssh_port_check", "ssh_exists", "ssh_not_exists",
})
# Enumeration tools reveal the COMPLETE set of files/structure. Truncating their
# result to a short snippet was the residual grounding leak (live: the model had a
# partial file list and invented setup.py/requirements.txt/test_main.py on top).
# Carry their listing in full so "what files exist / list all files" is grounded
# authoritatively and the model stops padding the set with plausible inventions.
_ENUM_FACT_TOOLS = frozenset({"project_map", "glob"})
# Verifier tools whose FAILED verdict (verifier=True, ok=False) is STILL grounded
# knowledge — a criterion transitioning unconfirmed→failed is a useful state change
# and progress toward the next fix (rule 9). An ERROR-branch return (verifier
# absent, e.g. bad host) is NOT a verdict and still grounds nothing.
_VERIFIER_GROUNDING_TOOLS = frozenset({
    "ssh_assert_contains", "ssh_assert_not_contains", "ssh_port_check", "ssh_exists",
    "ssh_not_exists", "http_api", "browser",
})
# Fidelity of the cross-turn grounding digest. Raised (220→400 / 900→1500 /
# 3000→6000): the effective window is resolved live from the server and is
# more of each verified tool result survives into the next turn's [ПРОВЕРЕННЫЕ
# ФАКТЫ] block, so the "compress old but keep it accurate" side of grounding loses
# less. ~6000 chars ≈ 2000 tokens — negligible against any real window.
_FACT_SNIPPET_CHARS = 400
_ENUM_FACT_SNIPPET_CHARS = 1500
_FACTS_DIGEST_CHARS = 6000  # room for one full enumeration + several read facts
FACTS_PREFIX = "[ПРОВЕРЕННЫЕ ФАКТЫ]"


def _fact_from_tool(
    name: str, arg_hint: str, text_result: str, *, ok: bool = True, verifier: bool = False,
) -> str | None:
    """One grounded-fact line from a discovery tool's result, or None when the
    tool is not fact-bearing / failed / empty. A failed VERDICT from a verifier
    (verifier=True, ok=False) is still grounded — the unconfirmed→failed transition
    is progress for the next fix (rule 9) — but a failed read/run, or a verifier
    that couldn't RUN, grounds nothing. Enumeration tools carry a larger snippet."""
    if name not in _GROUNDING_FACT_TOOLS:
        return None
    if not ok and not (verifier and name in _VERIFIER_GROUNDING_TOOLS):
        return None
    cap = _ENUM_FACT_SNIPPET_CHARS if name in _ENUM_FACT_TOOLS else _FACT_SNIPPET_CHARS
    snippet = " ".join((text_result or "").split())[:cap]
    if not snippet:
        return None
    hint = (arg_hint or "").strip()
    return f"{name}({hint}): {snippet}" if hint else f"{name}: {snippet}"


def _facts_digest(facts: list[str]) -> str:
    """Order-preserving de-duplicated digest of this run's grounded facts,
    capped so it can never dominate the context window. Empty string if none."""
    seen: set[str] = set()
    lines: list[str] = []
    for f in facts:
        if not f or f in seen:
            continue
        seen.add(f)
        lines.append(f"- {f}")
    body = "\n".join(lines)
    return body[:_FACTS_DIGEST_CHARS]


# Verbatim "recent tool outputs" carried into the NEXT turn — the last few
# grounding-tool results in full-ish, not just the fact summary. Complements the
# facts digest: the summary covers ALL turns compactly; this gives the immediately-
# prior turn's raw output so a follow-up ("что там в файле про X?") reads the real
# text, not a 400-char snippet. Bounded so it never dominates the window.
RECENT_TOOLS_PREFIX = "[РЕЗУЛЬТАТЫ ИНСТРУМЕНТОВ ПРОШЛОГО ХОДА]"
_RECENT_TOOL_ENTRY_CHARS = 2500   # per single tool output
_RECENT_TOOL_KEEP = 6             # last N grounding-tool results
_RECENT_TOOL_DIGEST_CHARS = 9000  # total cap (~3000 tokens)


def _recent_tool_snippet(name: str, arg_hint: str, text_result: str) -> str | None:
    """A fuller (still bounded) record of ONE grounding tool's output for the
    verbatim recent-outputs buffer. None when the tool isn't grounding-bearing."""
    if name not in _GROUNDING_FACT_TOOLS:
        return None
    text = (text_result or "").strip()
    if not text:
        return None
    if len(text) > _RECENT_TOOL_ENTRY_CHARS:
        text = text[:_RECENT_TOOL_ENTRY_CHARS] + " …[обрезано]"
    hint = (arg_hint or "").strip()
    head = f"{name}({hint})" if hint else name
    return f"### {head}\n{text}"


def _recent_tools_digest(entries: list[str]) -> str:
    """Join the last few recent-tool snippets (most-recent last), capped total."""
    if not entries:
        return ""
    body = "\n\n".join(entries[-_RECENT_TOOL_KEEP:])
    if len(body) > _RECENT_TOOL_DIGEST_CHARS:
        body = body[-_RECENT_TOOL_DIGEST_CHARS:]  # keep the most-recent tail
    return body


# --- Ungrounded-file nudge (residual grounding leak) ------------------------
# established_facts carries what tools GROUNDED, but when the model is asked about
# something no tool has fetched yet (a file never read), it can still name files
# from priors ("README describes test_main.py/setup.py" — live-observed). This
# detector flags file names in the model's answer that NOTHING in the run grounds
# (no tool result, no verified-facts block, not from the user), so the loop can
# nudge it to verify via a tool before stating them. Deliberately narrow: it only
# fires on an actual ungrounded FILE claim, so normal answers never see it.
_GROUNDING_NUDGE_MAX = 2
_ANSWER_FILE_RE = re.compile(
    r"[\w.\-/\\]+\.(?:py|js|ts|tsx|jsx|json|md|txt|ya?ml|toml|cfg|ini|sh|bash|"
    r"rs|go|java|kt|c|cpp|h|hpp|sql|html|css|scss|env|lock|rsc|conf|service)\b",
    re.IGNORECASE,
)


def _basename(path: str) -> str:
    return re.split(r"[\\/]", path)[-1]


def _ungrounded_files(answer: str, messages: list[dict], established_facts: list[str]) -> list[str]:
    """File names the answer states that nothing in the run grounds. Grounding =
    a tool result, a [ПРОВЕРЕННЫЕ ФАКТЫ] block, or the user's own message — NOT the
    system prompt (rule 20 lists calc.py/test_calc.py as NEGATIVE examples) and NOT
    the model's own prior prose (else a confabulation self-grounds)."""
    claimed = {_basename(m.group(0)).lower() for m in _ANSWER_FILE_RE.finditer(answer or "")}
    if not claimed:
        return []
    parts = list(established_facts or [])
    # Skip messages[0] (the system prompt); keep facts/summary (system), tool, user.
    for m in (messages or [])[1:]:
        if isinstance(m, dict) and m.get("role") in ("tool", "user", "system"):
            c = m.get("content")
            if isinstance(c, str):
                parts.append(c)
    hay = " ".join(parts).lower()
    return sorted({b for b in claimed if b not in hay})


# Anti-confabulation for generated documents (.docx / .xlsx / .pdf). The run tracks
# the filenames a VERIFIED file_gen actually produced (its download_name, already
# existence-checked and backed by a real file). If the final answer presents a document
# as ready whose EXACT name is not in that list, no real file_gen backs it and the model
# is passing off draft text as a file. write_file/edit_file are DELIBERATELY excluded: a
# plain write_file can emit a UTF-8 file literally named report.docx / report.pdf that is
# not a real Word/PDF document, so a written path is never proof of a real doc.
# Deliberately narrow: only the formats file_gen emits, exact-basename match.
_DOCGEN_NUDGE_MAX = 1
_ANSWER_DOC_RE = re.compile(r"[\w.\-/\\]+\.(?:docx|xlsx|pdf)\b", re.IGNORECASE)
_DOCGEN_READY_RE = re.compile(
    r"(?:\bвот\b|\bготов(?:о|а|ы)?\b|\bсоздан(?:о|а|ы)?\b|"
    r"\bсгенерирован(?:о|а|ы)?\b|\bподготовлен(?:о|а|ы)?\b|"
    r"\bсохран(?:ен|ён)(?:о|а|ы)?\b|\bскачать\b|"
    r"\b(?:here(?:'s| is)|ready|created|generated|saved|download)\b)",
    re.IGNORECASE,
)
_DOCGEN_EXISTING_RE = re.compile(
    r"(?:\bсуществу\w*\b|\bнаход\w*\b|\bнайден\w*\b|\bимеется\b|"
    r"\bна месте\b|\bпредыдущ\w*\s+прогон\w*\b|\bранее\b|"
    r"\bдо этого\b|\b(?:already existed|previous run|exists|found)\b|"
    r"\bне\s+(?:был[оаи]?\s+)?(?:создан|сгенерирован|подготовлен|сохран[её]н)\w*\b)",
    re.IGNORECASE,
)


def _doc_claim_context(answer: str, start: int, end: int) -> str:
    """Return the sentence/line containing one filename without splitting its extension."""
    left = 0
    for marker in ("\n", "! ", "? ", ". "):
        pos = answer.rfind(marker, 0, start)
        if pos >= 0:
            left = max(left, pos + len(marker))
    right = len(answer)
    for marker in ("\n", "! ", "? ", ". "):
        pos = answer.find(marker, end)
        if pos >= 0:
            right = min(right, pos)
    return answer[left:right]


def _unbacked_docgen_claim(answer: str, generated_docs: list[str]) -> list[str]:
    """Document filenames (.docx/.xlsx/.pdf) the answer presents as ready that were NOT
    produced by a verified file_gen this run. `generated_docs` is the list of file_gen
    download_name values (each existence-checked). EXACT basename match — a produced
    actual.docx does not back a claimed report.docx. write_file/edit_file paths are not
    passed in (a written report.pdf may be plain text, not a real PDF). Returns the
    unbacked claimed basenames; [] when every claimed doc was really generated / none
    claimed."""
    claimed: set[str] = set()
    for match in _ANSWER_DOC_RE.finditer(answer or ""):
        context = _doc_claim_context(answer, match.start(), match.end())
        # The guard is about a document presented as newly delivered by THIS run.
        # Reporting an existing/previously-created project file is not generation.
        if _DOCGEN_READY_RE.search(context) and not _DOCGEN_EXISTING_RE.search(context):
            claimed.add(_basename(match.group(0)).lower())
    if not claimed:
        return []
    produced = {_basename(p).lower() for p in (generated_docs or [])}
    return sorted({b for b in claimed if b not in produced})


# --- Near-duplicate loop detection ------------------------------------------
# The exact-fingerprint loop-guard misses a model that spams ONE tool with
# slightly-varying args — `ping -n 1 X`, `ping -n 2 X`, `recall "192.1 88"`,
# `recall "192.169 88"`… Each variant is a DISTINCT fingerprint, so the per-
# fingerprint count is spread thin and the run burns dozens of steps before the
# hard limit trips (observed live: a ping loop ran to step 50 / ~59 calls). We
# collapse near-duplicates: normalise numbers to "N", tokenise, and treat same-
# tool calls with high token overlap as the SAME churning loop — nudge to change
# approach, then stop fast. Legit work (read_file over DIFFERENT files) has low
# overlap and is never flagged.
_NEAR_DUP_JACCARD = 0.6
_NEAR_DUP_NUDGE_AT = 3
_NEAR_DUP_LIMIT = 5
_NEAR_DUP_WINDOW = 8


def _arg_tokens(parsed_args: dict | None) -> frozenset[str]:
    """Coarse token set of a call's args. Whole number/IP/version tokens collapse
    to a single "N" (so `192.1` and `192.169.88.2` match, and `ping -n 1 X` /
    `ping -n 2 X` collapse to the same shape); embedded digits in words are also
    normalised. Capped so a huge `content` arg (write_file) stays cheap and never
    dominates. File paths stay whole tokens, so different files don't collapse."""
    text = " ".join(str(v) for v in (parsed_args or {}).values())[:400].lower()
    toks: set[str] = set()
    for t in text.split():
        if re.fullmatch(r"[\d.:_\-]+", t):  # pure number / IP / version / flag-number
            toks.add("N")
        else:
            toks.add(re.sub(r"\d+", "N", t))
    return frozenset(toks)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _is_near_dup(name: str, tokens: frozenset[str], recent: list[tuple[str, frozenset[str]]]) -> bool:
    """True if this call closely mirrors a recent call to the SAME tool."""
    return any(rn == name and _jaccard(tokens, rt) >= _NEAR_DUP_JACCARD for rn, rt in recent)


# Progress control (the strategy router) lives in code_agent.progress. This file
# keeps only the DETERMINISTIC final report used when the router / repetition
# guards force a stop — built from the journal, never a retelling by the stuck
# model. See docs/AGENT_RUNTIME_PLAN.md.


# UNIVERSAL task-completion claims the model must not make while the deterministic
# verifier says the task is NOT confirmed. Targets status markers + universal
# "all/every ... passed", "no unverified", "все … критерии подтверждены" phrasings
# (with intervening words like "все frontend и SSH критерии …"). It deliberately
# does NOT match a SINGULAR factual statement ("критерий build подтверждён",
# "прочитал файл") — only the sweeping "everything passed" claims are neutralised.
_COMPLETION_CLAIM_RE = re.compile(
    r"\bCOMPLETED\b"
    r"|completion[_\s]?status\s*[:=]\s*(?:confirmed|done|complete)"
    r"|\btask\s+(?:is\s+)?(?:complete|completed|fully\s+done|verified)\b"
    # EN universal: "all [frontend and SSH] criteria/checks are passed/confirmed"
    r"|\ball\s+(?:the\s+)?(?:\w+\s+(?:and|or|,|/)?\s*){0,5}(?:criteria|checks?|tests?|requirements)"
    r"\s+(?:are\s+|were\s+)?(?:passed|pass|met|green|confirmed|verified|ok)"
    r"|\bevery\s+(?:criterion|check|test)\s+(?:passed|met|confirmed|green|verified)"
    r"|\bno\s+(?:unverified|failed|unconfirmed|pending)\b"
    # RU universal: "все [frontend и SSH] критерии подтверждены/выполнены/пройдены"
    r"|все\s+(?:\w+\s+(?:и|или|,|/)?\s*){0,5}критери\w*"
    r"\s+(?:подтвержд\w+|выполнен\w+|пройден\w+|проверен\w+|зелён\w+|соблюден\w+|met|passed|ok)"
    # RU universal: "все [verifier] проверки/checks прошли/пройдены"
    r"|все\s+(?:\w+\s+){0,4}(?:проверки|verifier[\s'\w-]*checks?|checks?)"
    r"\s+(?:прошл\w+|пройден\w+|passed|зелён\w+|met|ok|подтвержд\w+)"
    r"|нет\s+(?:не\s*подтвержд\w+|unverified|failed|провален\w+|unconfirmed|незакрыт\w+)"
    r"|(?:unverified|failed|не\s*подтвержд\w+|провален\w+)\s+нет\b"
    r"|(?:задача|работа)\s+(?:полностью\s+)?(?:выполнена|завершена|решена|готова)"
    r"|полностью\s+готов\w*",
    re.IGNORECASE,
)
_COMPLETION_CLAIM_MASK = "(не подтверждено verifier'ом — см. панель проверки)"


def gate_completion_claims(text: str, completion_status: str) -> str:
    """Guard: when the deterministic completion is NOT `confirmed`, the model's final
    text may not assert the task is COMPLETED / all criteria passed. Such claims are
    neutralised so the model's word can never contradict the verifier — the structured
    criteria (done event) + readiness panel remain the source of truth."""
    if completion_status == "confirmed" or not text:
        return text
    return _COMPLETION_CLAIM_RE.sub(_COMPLETION_CLAIM_MASK, text)


# ── Delivery-session ownership registry ─────────────────────────────────────
# Lives in this LEAF module (not in delivery_session) so the core agent loop
# can consult it without an import cycle: a session-level Stop that lands while
# NO slice is registered (between slices / during continuation build) must
# cancel the NEXT slice the moment it registers its run.
_SESSION_LOCK = threading.Lock()
_SESSION_CANCEL: dict[str, threading.Event] = {}


class DeliverySessionActiveError(RuntimeError):
    """A live delivery session already owns this run_id's cancel token."""


def request_session_cancel(run_id: str) -> bool:
    """Mark the delivery session for `run_id` cancelled. Returns True when a
    live session was found."""
    with _SESSION_LOCK:
        ev = _SESSION_CANCEL.get(run_id)
    if ev is None:
        return False
    ev.set()
    return True


def session_cancel_requested(run_id: str) -> bool:
    """True when a live delivery session for `run_id` has a PENDING cancel —
    consulted by the core loop right after _register_run so a Stop that landed
    in the inter-slice window cancels the new slice before any model call."""
    with _SESSION_LOCK:
        ev = _SESSION_CANCEL.get(run_id)
    return ev.is_set() if ev is not None else False


def session_active(run_id: str) -> bool:
    with _SESSION_LOCK:
        return run_id in _SESSION_CANCEL


def register_session(run_id: str) -> threading.Event:
    """Atomic ownership claim. A live session's cancel token is NEVER
    overwritten — a duplicate stream/resume of the same run_id raises
    DeliverySessionActiveError and the original stays the sole owner."""
    with _SESSION_LOCK:
        if run_id in _SESSION_CANCEL:
            raise DeliverySessionActiveError(run_id)
        ev = threading.Event()
        _SESSION_CANCEL[run_id] = ev
    return ev


def unregister_session(run_id: str, ev: threading.Event) -> None:
    """Release ownership — identity-guarded: only the session that registered
    `ev` may remove the entry, so a failed duplicate can never evict the
    original owner's token."""
    with _SESSION_LOCK:
        if _SESSION_CANCEL.get(run_id) is ev:
            _SESSION_CANCEL.pop(run_id, None)


def tool_state_changed(tool_name: str, tool_meta: dict, *, exec_ok: bool) -> bool:
    """Server-owned MUTATION signal for one executed tool call: True only when
    the execution succeeded, reported a real touched target, AND the ToolSpec
    registry declares the tool side-effectful. No hardcoded tool-name list —
    the registry's `side_effect` flag is the single seam, so read-only tools
    (read_file/ssh_read/glob/…) yield False by their own spec, blocked/error
    calls fail the exec_ok gate, and a no-op (e.g. ssh_replace without a match)
    reports no touched_path. Unknown/unregistered specs default to False —
    conservative: never counts as delivery progress."""
    if not exec_ok:
        return False
    meta = tool_meta or {}
    if not str(meta.get("touched_path") or "").strip():
        return False
    # Proven NO-OP: when the tool itself reports the before/after content
    # (write_file/edit_file), identical bytes mean nothing changed — an
    # "overwrite with the same content" is not a mutation.
    old, new = meta.get("old_content"), meta.get("new_content")
    if isinstance(old, str) and isinstance(new, str) and old == new:
        return False
    try:
        from app.application.tool_registry.runtime import get_tool

        spec = get_tool(tool_name) or {}
    except Exception:
        logger.warning("tool_state_changed: spec lookup failed for %s", tool_name, exc_info=True)
        return False
    return bool(spec.get("side_effect"))


# ── Structural task-state digest ───────────────────────────────────────────
# The protected [СОСТОЯНИЕ ЗАДАЧИ] block is rebuilt from typed durable state
# and carried verbatim through compaction. It is intentionally bounded prompt
# context, not a lossless store; the complete event/evidence record remains in
# RunJournal.

def build_task_state_block(
    *,
    goal: str = "",
    constraints: list[str] | None = None,
    criteria_rows: list[dict] | None = None,
    checklist_items: list[dict] | None = None,
    mutated_files: list[str] | None = None,
    verifications: list[str] | None = None,
    failed_attempts: list[str] | None = None,
    next_step: str = "",
) -> str:
    """Deterministic, bounded digest of the run's typed state. Every input is
    runtime/durable-store data — never model prose (criteria evidence comes from
    verifier tool output, checklist rows from task_planner.db)."""
    lines: list[str] = []
    if goal.strip():
        lines.append(f"Задача: {goal.strip()[:400]}")
    if next_step.strip():
        # Keep the immediate continuation target near the front so the global
        # digest cap cannot remove it after a large criteria/checklist section.
        lines.append(f"Следующий шаг: {next_step.strip()[:300]}")
    for c in (constraints or [])[:8]:
        lines.append(f"Ограничение: {str(c).strip()[:200]}")
    rows = criteria_rows or []
    if rows:
        confirmed = sum(1 for r in rows if r.get("status") == "confirmed")
        lines.append(f"Критерии ({confirmed}/{len(rows)} подтверждено):")
        for r in rows[:18]:
            mark = {"confirmed": "✓", "failed": "✗"}.get(str(r.get("status")), "·")
            ev = str(r.get("evidence") or "").strip().replace("\n", " ")[:120]
            lines.append(f"  {mark} {str(r.get('text'))[:160]}"
                         + (f" [{r.get('verifier')}: {ev}]" if ev else ""))
    items = checklist_items or []
    if items:
        done = sum(1 for it in items if str(it.get("status")) == "completed")
        lines.append(f"Чеклист ({done}/{len(items)}):")
        lines.append(format_checklist_state(items, max_items=20))
    mutated = list(dict.fromkeys(mutated_files or []))
    if mutated:
        shown = ", ".join(mutated[:20])
        more = f" (+{len(mutated) - 20})" if len(mutated) > 20 else ""
        lines.append(f"Изменённые файлы ({len(mutated)}): {shown}{more}")
    for v in (verifications or [])[-10:]:
        lines.append(f"Проверка: {str(v)[:180]}")
    for f in (failed_attempts or [])[-6:]:
        lines.append(f"Неудачная попытка: {str(f)[:160]}")
    return "\n".join(lines)[:6000]


def upsert_task_state_message(
    messages: list[dict[str, Any]], block_text: str
) -> list[dict[str, Any]]:
    """Replace (or insert right after the leading system prompt) the ONE
    protected task-state message. Assistant-role like the rolling summary, so
    strict chat templates that reject a second system message stay happy."""
    from app.application.context.compaction import TASK_STATE_PREFIX

    if not block_text.strip():
        return messages
    out = [
        m for m in messages
        if not (m.get("role") in ("system", "assistant")
                and str(m.get("content") or "").startswith(TASK_STATE_PREFIX))
    ]
    insert_at = 1 if out and out[0].get("role") == "system" else 0
    out.insert(insert_at, {"role": "assistant", "content": TASK_STATE_PREFIX + block_text})
    return out


def _norm_checklist_text(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def format_checklist_state(items: list[dict], *, max_items: int = 30) -> str:
    """Deterministic one-line-per-item digest of the durable run checklist,
    built from the task_planner rows (server truth, not model prose)."""
    lines: list[str] = []
    for item in items[:max_items]:
        status = str(item.get("status") or "pending")
        mark = "x" if status == "completed" else ("~" if status == "in_progress" else " ")
        lines.append(f"- [{mark}] ({item.get('id')}) [{status}] {item.get('text')}")
    if len(items) > max_items:
        lines.append(f"- … ещё {len(items) - max_items} пунктов")
    return "\n".join(lines)


def resume_checklist_guard(run_id: str, parsed_args: dict) -> str | None:
    """Delivery (C): on a RESUMED slice the durable checklist is the plan of
    record — `todo_update` may extend it, re-send it verbatim or flip statuses,
    but may NOT replace existing items with a different plan. Covers BOTH write
    channels of update_checklist: `items` (position/id upsert) and `updates`
    (by-id mutation, which can also rewrite `text`). Returns the redirect text
    (carrying the REAL checklist with ids) when a replacement is detected, None
    when the call is safe (read / status-or-blocker-only updates / pure
    extension / same-text re-send).

    Mirrors update_checklist's landing semantics: explicit id wins, else the
    row already holding the explicit position; items without id/position land on
    fresh slots (extension) and are always allowed.
    """
    raw_items = parsed_args.get("items")
    raw_updates = parsed_args.get("updates")
    has_items = isinstance(raw_items, list) and raw_items
    has_updates = isinstance(raw_updates, list) and raw_updates
    if not has_items and not has_updates:
        return None
    try:
        from app.application.task_planner.service import list_checklist

        existing = list((list_checklist(run_id) or {}).get("items") or [])
    except Exception:
        logger.warning("resume_checklist_guard: checklist read failed for %s", run_id, exc_info=True)
        return None
    if not existing:
        return None
    by_id = {str(row.get("id") or ""): row for row in existing}
    by_pos = {int(row.get("position") or 0): row for row in existing}

    def _replaces(landing: dict | None, raw: dict) -> bool:
        if landing is None:
            return False
        new_text = _norm_checklist_text(raw.get("text"))
        return bool(new_text) and new_text != _norm_checklist_text(landing.get("text"))

    violation = False
    for raw in (raw_items or []) if has_items else []:
        if not isinstance(raw, dict):
            continue
        raw_id = str(raw.get("id") or raw.get("item_id") or "").strip()
        landing = by_id.get(raw_id) if raw_id else None
        if landing is None and not raw_id and raw.get("position") is not None:
            try:
                landing = by_pos.get(int(raw.get("position")))
            except (TypeError, ValueError):
                landing = None
        # landing None → fresh slot: extension, allowed
        if _replaces(landing, raw):
            violation = True
            break
    if not violation and has_updates:
        for raw in raw_updates:
            if not isinstance(raw, dict):
                continue
            raw_id = str(raw.get("id") or raw.get("item_id") or "").strip()
            # updates are by-id only; status/blocker-only entries carry no text
            # and stay allowed — that is the advertised legitimate channel.
            if _replaces(by_id.get(raw_id) if raw_id else None, raw):
                violation = True
                break
    if not violation:
        return None
    done = sum(1 for row in existing if str(row.get("status")) == "completed")
    return (
        "[план уже существует] Это продолжение прогона: durable-чеклист создан "
        f"ранее ({done}/{len(existing)} выполнено) и НЕ пересоздаётся другим планом.\n"
        f"{format_checklist_state(existing)}\n"
        "Продолжай с первого открытого пункта. Статусы меняй через "
        "todo_update(updates=[{id, status}]) — без замены текста пунктов; новые "
        "шаги добавляй отдельными items, не переписывая существующие."
    )


def _deterministic_stop_summary(
    reason: str,
    call_log: list[str],
    touched_files: list[str],
    established_facts: list[str],
    *,
    exhausted_strategies: list[str] | None = None,
    next_step: str | None = None,
) -> str:
    """Engineering report for a controller-forced stop (loop / no progress). Built
    deterministically from what ACTUALLY happened — never a retelling by the stuck
    model — so it can't confabulate success. Structured as an incomplete-work
    report (done / not done / why / next), the way a normal runtime closes a run.
    LLM prose (if any) is an optional layer AFTER this, not the source of truth."""
    uniq = list(dict.fromkeys(f for f in (touched_files or []) if f))
    digest = _facts_digest(established_facts)

    lines = ["⛔ **Не завершено** — прогон остановлен контроллером.", ""]

    lines.append("**Что сделано (по журналу):**")
    if uniq:
        shown = ", ".join(uniq[:12])
        more = f" (+{len(uniq) - 12})" if len(uniq) > 12 else ""
        lines.append(f"- Изменённые файлы: {shown}{more}")
    else:
        lines.append("- Файлы не изменены")
    if digest:
        lines.append("- Проверенные факты:\n" + digest[:900])

    if exhausted_strategies:
        lines.append("")
        lines.append("**Что не удалось (исчерпанные стратегии):**")
        for s in exhausted_strategies[:8]:
            lines.append(f"- {s}")

    lines.append("")
    lines.append("**Почему остановлено:**")
    lines.append(f"- {reason}. Вызовов инструментов: {len(call_log)}.")

    lines.append("")
    lines.append("**Следующий безопасный шаг:**")
    lines.append(f"- {next_step or 'уточни путь или спроси пользователя, затем продолжи следующим сообщением'}")

    lines.append("")
    lines.append("_Детерминированный итог из журнала прогона (не пересказ модели)._")
    return "\n".join(lines)


# R2 Server Lifecycle: dev-server commands must go through run_server (owned,
# stoppable, real URL) — in run_bash they block until the shell timeout and leak.
# HEURISTIC by design: this feeds a bounded REDIRECT (worst case = one bad hint),
# never a verdict (map invariant №10). Review-hardened against the costly false
# positives: quoted mentions (commit messages, printf), --help/--version probes,
# one-shot subcommands (`vite build`), and dev-prefixed script names (dev-build).
_QUOTED_SPAN_RE = re.compile(r'"[^"]*"|\'[^\']*\'')
_WORD_END = r"(?=\s|$|&|\||;)"
_DEV_SERVER_CMD_RE = re.compile(
    r"(?:^|&&|;)\s*(?:"
    # dev-token scripts: word must END there — `npm run dev-build`/`dev:build` are one-shot
    r"(?:npm|yarn|pnpm|bun)\s+(?:run\s+)?(?:dev|start|serve|preview)" + _WORD_END +
    # bare `vite` / `vite --port …` / `vite dev|serve|preview` serve; `vite build` does NOT
    r"|(?:npx\s+)?vite(?=\s*$|\s+--|\s+(?:dev|serve|preview)" + _WORD_END + r")"
    r"|(?:npx\s+)?(?:next|nuxt|astro|remix)\s+dev\b"
    r"|ng\s+serve\b"
    r"|python3?\s+-m\s+http\.server\b"
    r"|(?:python3?\s+)?manage\.py\s+runserver\b"
    # uvicorn only with an app path (module:attr) — `uvicorn --version` is a probe
    r"|uvicorn\s+[\w./\\]+:[\w.]+"
    r"|flask\s+run" + _WORD_END +
    r"|(?:npx\s+)?(?:http-server|live-server|serve)" + _WORD_END +
    r"|rails\s+s(?:erver)?\b"
    r"|php\s+-S\s"
    r")",
    re.IGNORECASE,
)


def _looks_like_dev_server_command(command: str) -> bool:
    """A run_bash command that starts a long-lived dev server (guard heuristic)."""
    cmd = (command or "").strip()
    low = cmd.lower()
    if "--help" in low or "--version" in low or "<<" in low:
        return False   # diagnostics / heredoc file-writes are not server launches
    # a QUOTED mention (commit message, printf/echo payload) is not a launch
    cmd = _QUOTED_SPAN_RE.sub(" ", cmd)
    return bool(_DEV_SERVER_CMD_RE.search(cmd))


def _mark_approval_approved(approval_id: str) -> bool:
    """Programmatically grant an approval row (for non-'ask' permission modes)."""
    try:
        from app.application.monitoring import runtime as _mon
        _mon.update_approval_status(approval_id, status="approved")
        return True
    except Exception:
        return False


def _mark_approval_expired(approval_id: str) -> bool:
    """Expire an approval row the runtime ABANDONS (auto-verifier pass skipping a
    call that would park on a human) — otherwise a dead pending card lingers in the
    approvals panel for a run that has already moved on."""
    try:
        from app.application.monitoring import runtime as _mon
        _mon.update_approval_status(approval_id, status="expired")
        return True
    except Exception:
        return False


def _flatten_for_summary(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """F3.1: convert in-loop messages (assistant with tool_calls, role="tool"
    results) into plain text turns the summarizer keeps. Without this,
    mid-run compaction summarized almost nothing: tool messages and
    empty-content assistant turns (typical while tool-calling) were dropped
    from the summarizer input by `_coerce_history`, so the agent forgot
    everything it had read in the run.
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        text = content if isinstance(content, str) else ""
        if role == "tool":
            name = str(m.get("name") or "tool")
            excerpt = text[:400]
            out.append({"role": "assistant", "content": f"[tool result {name}] {excerpt}"})
            continue
        if role == "assistant":
            parts: list[str] = []
            if text:
                parts.append(text)
            calls = m.get("tool_calls") or []
            if calls:
                names: list[str] = []
                for c in calls[:12]:
                    fn = (c or {}).get("function") or {}
                    cname = str(fn.get("name") or "?")
                    args = fn.get("arguments")
                    hint = _short_arg_hint(args) if isinstance(args, dict) else ""
                    names.append(f"{cname}({hint})")
                parts.append("[tools called] " + "; ".join(names))
            if parts:
                out.append({"role": "assistant", "content": "\n".join(parts)})
            continue
        out.append(m)
    return out


class ContextBudgetError(RuntimeError):
    """Protected/recent context cannot fit the active server window."""


def _prepare_messages_for_llm(
    messages: list[dict[str, Any]],
    *,
    num_ctx: int,
    model: str,
    chat_fn: Callable[..., dict[str, Any]],
    context_profile: dict[str, Any],
    audit_sink: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[list[dict[str, Any]], bool, dict[str, Any]]:
    """Compact at policy thresholds and enforce the effective request budget."""
    from app.application.context.compaction import maybe_compact
    from app.application.context.usage import get_context_usage

    usage_kwargs = {
        "ctx_size": num_ctx,
        "reserved_output_tokens": int(context_profile["reserved_output_tokens"]),
        "reserved_system_tokens": int(context_profile.get("reserved_system_tokens") or 4096),
        "safety_margin_tokens": int(context_profile.get("safety_margin_tokens") or 2048),
    }
    usage = get_context_usage(messages, **usage_kwargs)
    compacted = False
    # Adaptive context: thresholds come from the resolved profile as FRACTIONS
    # of the effective window — identical percentages at 32K, 128K or 1M. No
    # absolute "compact at 64K" marks anywhere; the fallbacks below only guard
    # a caller that passed a profile without thresholds.
    _thresholds = context_profile.get("compaction_thresholds") or {}
    compact_threshold = float((_thresholds.get("auto") or {}).get("percent")
                              or (60.0 if num_ctx < 16_384 else 75.0))
    strong_threshold = float((_thresholds.get("strong") or {}).get("percent")
                             or (80.0 if num_ctx < 16_384 else 90.0))
    critical_threshold = float((_thresholds.get("critical") or {}).get("percent")
                               or (90.0 if num_ctx < 16_384 else 95.0))
    should_compact = float(usage["percent"]) >= compact_threshold
    if not should_compact and num_ctx < 16_384 and len(messages) > 2:
        should_compact = True

    if should_compact:
        strong = float(usage["percent"]) >= strong_threshold
        messages, changed = maybe_compact(
            messages,
            num_ctx,
            model,
            chat_fn,
            summarize_fn=summarize_history,
            threshold=0.0,
            keep_pairs=1 if strong else 4,
            fallback_keep=2 if strong else 8,
            prepare_messages=_flatten_for_summary,
            audit_sink=audit_sink,
            trigger_reason="strong_compression" if strong else "auto_compression",
        )
        compacted = compacted or changed
        usage = get_context_usage(messages, **usage_kwargs)

    if float(usage["percent"]) >= strong_threshold:
        messages, changed = maybe_compact(
            messages,
            num_ctx,
            model,
            chat_fn,
            summarize_fn=summarize_history,
            threshold=0.0,
            keep_pairs=1,
            fallback_keep=2,
            prepare_messages=_flatten_for_summary,
            audit_sink=audit_sink,
            trigger_reason="strong_compression",
        )
        compacted = compacted or changed
        usage = get_context_usage(messages, **usage_kwargs)

    safe_input_budget = int(context_profile.get("safe_input_budget") or 0)
    if float(usage["percent"]) >= critical_threshold or (
        safe_input_budget > 0 and int(usage["current_tokens"]) > safe_input_budget
    ):
        raise ContextBudgetError(
            "Контекст остаётся критически заполненным после сжатия: "
            f"{usage['current_tokens']} входных токенов, окно {usage['ctx_size']}. "
            "Начните новый чат или оставьте только необходимые материалы."
        )
    return messages, compacted, usage


def _wrap_up_text(
    chat: Callable[..., dict[str, Any]],
    model: str,
    num_ctx: int,
    messages: list[dict[str, Any]],
    call_log: list[str],
    reason: str,
) -> str:
    """F2: one best-effort no-tools LLM call to summarize an interrupted run
    (max_steps / deadline) — files on disk are already changed, the user must
    get «что сделано / что осталось». Falls back to a deterministic summary
    built from the run's call log. Deliberately outside inference telemetry:
    it is a single bounded closing call, not part of the tool loop.
    """
    try:
        response = chat(
            model=model,
            messages=messages + [{
                "role": "user",
                "content": WRAP_UP_PROMPT.format(reason=reason),
            }],
            options={"num_ctx": int(num_ctx), "active_context_limit": int(num_ctx)},
        )
        text = (((response or {}).get("message") or {}).get("content") or "").strip()
        # A degenerate model can emit raw <tool_call>…</tool_call> as the wrap-up
        # content (live: it leaked into the answer after the loop-guard fired).
        # Strip it; if nothing meaningful is left, fall through to the call-log
        # summary rather than showing an empty or markup-only answer.
        from app.application.code_agent.inline_tool_calls import _strip_tool_call_markup
        text = _strip_tool_call_markup(text)
        if text:
            return text
    except Exception as exc:
        logger.warning("wrap-up summary call failed: %s", exc)
    if call_log:
        shown = "; ".join(call_log[:20])
        more = f" (+{len(call_log) - 20})" if len(call_log) > 20 else ""
        return (
            f"Прогон остановлен: {reason}. Выполнено вызовов: {len(call_log)} — "
            f"{shown}{more}. Изменения уже на диске; продолжи следующим сообщением."
        )
    return f"Прогон остановлен: {reason} — до первого вызова инструмента."


def _is_throwaway_project(project_root: Path) -> bool:
    """True when the project lives under the OS temp dir — a disposable
    sandbox from a smoke/experimental run, not a real user project.

    We must not persist agent_turn memories for these: they flood RAG with
    noise like ``[agent_turn project=tmpXXXX] task: hello | outcome: Hello
    world`` that never matches a real future query but still dilutes recall.
    (pytest is already isolated via ELIRA_DATA_DIR; this guards the *real*
    canonical DB against manual/smoke runs over temp dirs.)
    """
    try:
        import tempfile

        root = project_root.expanduser().resolve()
        tmp = Path(tempfile.gettempdir()).resolve()
        return root == tmp or tmp in root.parents
    except Exception:
        return False


def _try_remember_turn(*, user_message: str, response_text: str, project_root: Path) -> None:
    """Fire-and-forget: write a short summary of a successful agent turn
    to RAG so future `recall(query)` can surface it. Failures are logged
    but never raised.
    """
    if _is_throwaway_project(project_root):
        return
    try:
        from app.application.rag_memory.service import add_to_rag
    except Exception:
        return
    user = (user_message or "").strip()
    answer = (response_text or "").strip()
    if not user or not answer:
        return
    if len(user) > 300:
        user = user[:300] + " [...]"
    if len(answer) > 600:
        answer = answer[:600] + " [...]"
    project_name = project_root.name or str(project_root)
    scope_id = project_scope_id(project_root)
    summary = f"[agent_turn project={project_name}] task: {user} | outcome: {answer}"
    try:
        # Pass project= so the entry is scoped to this project and
        # recall() from a different project doesn't pull it up.
        add_to_rag(text=summary, category="agent_turn", importance=3, project=scope_id)
    except Exception as exc:
        logger.debug("auto-remember failed: %s", exc)


def _record_code_route_metric(run_id: str, decision: Any, effective_num_ctx: int, *, agent_id: str = "code-agent") -> None:
    """Best-effort routing provenance for code-agent (no schema change)."""
    try:
        from app.application.monitoring.runtime import record_metric

        record_metric(
            metric_type="model.routed",
            agent_id=agent_id or "code-agent",
            run_id=run_id,
            ok=True,
            details={
                "model": decision.model,
                "provider": decision.provider,
                "profile_id": decision.profile_id,
                "route": decision.route,
                "role": decision.role,
                "routing_source": decision.source,
                "requested_model": decision.requested_model,
                "effective_num_ctx": int(effective_num_ctx),
                "fallback_reason": decision.fallback_reason,
                "cloud_skipped": decision.cloud_skipped,
            },
        )
    except Exception as exc:
        logger.debug("model route metric recording failed", exc_info=exc)


_TOOL_SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "tool_search",
        "description": (
            "Search for more tools by keyword and activate the relevant ones for "
            "THIS task. Use it whenever you need a capability you don't currently "
            "have (e.g. web search, http, sql, run a command). Eligible matches "
            "become callable on your next step."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keywords describing the capability you need.",
                }
            },
            "required": ["query"],
        },
    },
}


# ask_user is handled INLINE by the loop (like tool_search) — it pauses the run
# and waits for a human answer, so it is never dispatched via the executor. The
# schema is appended to every step so the model can always reach it.
_ASK_USER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ask_user",
        "description": (
            "Ask the user ONE short clarifying question and wait for the answer, "
            "then continue the SAME run. Use only when the task is genuinely "
            "ambiguous (which host / file / option among several). Provide "
            "`options` (a list of choices) when the answer is one of a few known "
            "values — the UI renders them as buttons. Do NOT ask for anything you "
            "can find yourself with read_file/glob/grep/config; ask sparingly."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The question to ask the user."},
                "options": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Optional list of concrete answer choices.",
                },
            },
            "required": ["question"],
        },
    },
}


# ssh_request_host is handled INLINE by the loop (like ask_user): the agent
# CANNOT add hosts to the SSH allowlist itself (that is the security boundary),
# so it calls this to ask the user to approve one host with a single click. On
# approval the loop adds the host to the allowlist and ssh_run works for it.
_SSH_REQUEST_HOST_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ssh_request_host",
        "description": (
            "Ask the user to add ONE host to the SSH integration (the allowlist "
            "that enables ssh_run). You CANNOT edit that list yourself — it is the "
            "user's security boundary. This pauses the run and shows the user an "
            "Approve/Deny button; on approve the host is added and ssh_run works "
            "for it in this same run. Call it AFTER the host is set up (key "
            "installed on the server, ~/.ssh/config alias created). `host` MUST be "
            "the exact alias/token from the ~/.ssh/config Host entry (e.g. "
            "'elira-ai-server'), not a bare IP, so ssh_run resolves the right key."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "host": {
                    "type": "string",
                    "description": "Exact host alias/token to allow (must match the ~/.ssh/config Host entry).",
                },
                "reason": {
                    "type": "string",
                    "description": "Short reason, shown to the user in the approval card.",
                },
            },
            "required": ["host"],
        },
    },
}


def _schema_tool_name(schema: dict) -> str:
    return str((schema.get("function") or {}).get("name") or "")
