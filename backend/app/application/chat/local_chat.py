from __future__ import annotations

import re

from app.core.persona_defaults import (
    AUTO_PROFILE,
    DEFAULT_PROFILE,
    LEGACY_PROFILE_TO_MODE,
    PERSONA_MODES,
)


def normalize_profile(name: str) -> str:
    """Resolve a stored/requested name to a real persona mode.

    Accepts a current mode, a legacy profile name ("Программист" -> "Инженерный"),
    or "default"/empty -> DEFAULT_PROFILE. "Авто" is NOT resolved here (it needs
    the message text — see resolve_persona_mode)."""
    if not name or name.lower() == "default":
        return DEFAULT_PROFILE
    if name in PERSONA_MODES:
        return name
    return LEGACY_PROFILE_TO_MODE.get(name, DEFAULT_PROFILE)


def resolve_profile_name(name: str | None) -> str:
    """Pick the effective persona mode for a request WITHOUT message context.

    Used by background paths (autopipeline) that have no live user message to
    classify. An explicit mode is honored; "default"/empty falls back to the
    saved `agent_profile`; "Авто" (no text to classify here) resolves to the
    neutral DEFAULT_PROFILE so background runs stay deterministic.
    """
    if name and name.lower() != "default":
        if name == AUTO_PROFILE:
            return DEFAULT_PROFILE
        return normalize_profile(name)
    # Lazy import: settings pulls in storage and would create an import cycle
    # if loaded at module top alongside the persona machinery.
    from app.application.elira_memory.settings import get_settings

    stored = ""
    try:
        stored = str(get_settings().get("agent_profile") or "")
    except Exception:
        stored = ""
    if stored == AUTO_PROFILE:
        return DEFAULT_PROFILE
    return normalize_profile(stored)


# ── Auto-mode heuristic (Living Persona, step A) ─────────────────────────────
# When the picker is on "Авто", classify each message into a mode by simple,
# fast signals — no extra model call. Engineering signals win first (so a code
# request always reaches its tools), then personal/companion signals, else the
# neutral Баланс.
_CODE_SIGNALS = re.compile(
    r"(?:\b(?:баг|ошибк|исключени|traceback|стек\s*трейс|функци|класс|метод|"
    r"рефактор|патч|коммит|деплой|компил|собери|собрать|запусти|запуск|"
    r"тест|линт|дебаг|отлад|почини|исправ|перепиши|напиши\s+код|реализуй|"
    r"python|питон|скрипт\w*|"  # language markers so code wins over science math terms
    r"имплемент|merge|pull\s*request|pr\b|api\b|endpoint|роутинг|миграци|"
    r"bug|fix|refactor|implement|deploy|compile|build|debug|stack\s*trace|"
    r"exception|commit|function|class\b|код\b)"
    r"|\b(?:unity|blender|github)\b"
    r"|\.(?:py|ts|tsx|js|jsx|go|rs|java|c|cpp|h|sql|json|yaml|yml|sh|toml)\b"
    r"|(?<![A-Za-z0-9])[A-Za-z]:[\\/]"  # Windows path, not the tail of https:/
    r"|`[^`]+`)",
    re.IGNORECASE,
)
_PERSONAL_SIGNALS = re.compile(
    r"\b(?:устал\w*|грустн\w*|тяжело|одиноко|спасибо|"
    r"запомн\w*|вспомн\w*|долговременн\w*\s+памят\w*|"
    r"как\s+(?:ты|дела|сама)|поговор\w*|посоветуй\s+по\s+жизни|"
    r"пережива\w*|тревож\w*|скуч\w*|люблю|нравишься|"
    r"поддерж\w*|обним\w*|расскажи\s+о\s+себе)\b",
    re.IGNORECASE,
)
# Business signals (Деловой): documents, counterparties, marketing copy, money.
# Checked AFTER personal ones, so «напиши письмо маме» stays Личный while
# «составь коммерческое предложение» routes to Деловой. Markers are deliberately
# specific (деловое письмо / КП / БИН / оффер), not just «письмо».
# Stems (no trailing \b) so Russian inflections match («контрагентА», «маржУ»),
# mirroring _CODE_SIGNALS; only the short abbreviations keep strict boundaries.
_BUSINESS_SIGNALS = re.compile(
    r"(?:\b(?:коммерческ\w*\s+предложени|деловое\s+письмо|бизнес.?план|"
    r"договор|контракт|контрагент|поставщик|инвойс|счёт[- ]фактур|счет[- ]фактур|"
    r"протокол\s+встречи|переговор|прайс|смет[аоуые]|бюджет|маржинальн|марж[ауеи]|"
    r"юнит.?экономик|рентабельн|прибыль|прибыли|выручк|"
    r"оффер|лендинг|рассылк|воронк|реклам|маркетинг|отч[её]т|презентац|"
    r"учредител|юридическ)"
    r"|\b(?:бин|иин|инн|огрн|кп)\b)",
    re.IGNORECASE,
)

# Infra signals (Инфраструктура): networking / servers / sysadmin. Specific
# markers (роутер/firewall/ssh/systemctl/RouterOS/nginx) so generic words like
# «сервер» alone don't steal code tasks («напиши сервер на Flask» stays code).
# Checked AFTER code — a coding request that merely mentions nginx stays Инженерный.
_INFRA_SIGNALS = re.compile(
    r"(?:\b(?:роутер|router|mikrotik|routeros|cisco|коммутатор|свитч|"
    r"firewall|брандмауэр|iptables|nftables|подсет|маршрутизаци|"
    r"systemctl|systemd|демон|nginx|apache|proxmox|traceroute|nslookup|"
    r"инфраструктур|сетев|порт\w*|просканир\w*\s+сет|настро\w*\s+сервер)"
    r"|\b(?:tcp|udp|icmp|ping|tracert|dig|vlan|dns|dhcp|nat|ssh|rdp|smb|snmp)\b)",
    re.IGNORECASE,
)
_INFRA_ACTION_SIGNALS = re.compile(
    r"(?:подключ\w*|зайд\w*|проверь|диагност\w*|настро\w*|выполн\w*|"
    r"запусти|просканир\w*).{0,160}(?:\bssh\b|\brdp\b|\bsmb\b|\bsnmp\b|"
    r"\btcp\b|\budp\b|\bicmp\b|роутер|router|firewall|подсет|порт\w*)",
    re.IGNORECASE | re.DOTALL,
)


# Medical signals (Медицина): health/clinical — symptoms, treatment, doses,
# conditions, imaging. Kept SEPARATE from science and checked BEFORE it so a
# health question ("что при высоком давлении") routes to Медицина, not Научный.
# Deliberately clinical (not bare "белок"/"клетка", which stay Научный biology).
_MEDICAL_SIGNALS = re.compile(
    r"(?:\b(?:симптом\w*|диагноз\w*|диагност\w*|лечени\w*|лечить|болезн\w*|"
    r"заболевани\w*|лекарств\w*|препарат\w*|дозир\w*|дозировк\w*|таблетк\w*|"
    r"антибиотик\w*|терапи\w*|температур\w*|давлени\w*|тошнот\w*|кашель|"
    r"насморк|инфекци\w*|воспалени\w*|витамин\w*|иммунитет|беременн\w*|"
    r"аллерги\w*|болит|боль\s+в\b)"
    r"|\b(?:узи|мрт|экг|кт|врач|врача|доктор)\b)",
    re.IGNORECASE,
)

# Science signals (Научный): biology / physics / math. Checked AFTER code (a
# coding request wins) and after medical (a health question wins). Uses specific
# scientific terms so generic words ("энергия"/"сила") don't misroute.
_SCIENCE_SIGNALS = re.compile(
    r"(?:\b(?:научн\w*|биолог\w*|ген\b|ген[аеовы]\w*|геном\w*|фермент\w*|белок|белк\w*|"
    r"клетк\w*|эволюци\w*|организм\w*|молекул\w*|нейрон\w*|фотосинтез|"
    r"митоз|мейоз|хромосом\w*|физик\w*|квант\w*|частиц\w*|термодинамик\w*|"
    r"энтропи\w*|релятив\w*|электромагнит\w*|гравитаци\w*|нейтрон\w*|"
    r"электрон\w*|математик\w*|интеграл\w*|производн\w*|теорем\w*|"
    r"уравнени\w*|матриц\w*|вероятност\w*|дифференциал\w*|логарифм\w*|"
    r"тригонометри\w*|"
    r"хими\w*|реакци\w*|соединени\w*|кислот\w*|щёлоч\w*|окислени\w*|"
    r"валентн\w*|катализ\w*|полимер\w*|изотоп\w*|оксид\w*)"
    r"|\b(?:днк|рнк|атф|ph)\b)",
    re.IGNORECASE,
)


_CONTINUATION_ONLY = re.compile(
    r"^\s*(?:да|ок(?:ей)?|хорошо|продолжай(?:те)?|продолжи(?:те)?|"
    r"делай(?:те)?|сделай(?:те)?|исправляй(?:те)?|дальше|поехали)\s*[.!?]*\s*$",
    re.IGNORECASE,
)


def _routing_text(
    user_input: str | None,
    conversation_history: list[dict[str, object]] | None,
) -> str:
    """Keep terse Auto follow-ups in the profile selected by the prior task."""
    current = (user_input or "").strip()
    if not _CONTINUATION_ONLY.fullmatch(current):
        return current
    for item in reversed(conversation_history or []):
        if str(item.get("role") or "").lower() != "user":
            continue
        content = item.get("content")
        if isinstance(content, str) and content.strip():
            return f"{content[-4000:]}\n{current}"
    return current


def classify_mode(
    user_input: str | None,
    conversation_history: list[dict[str, object]] | None = None,
) -> str:
    """Heuristic mode for "Авто": Инженерный / Личный / Деловой / Инфраструктура /
    Медицина / Научный / Баланс."""
    text = _routing_text(user_input, conversation_history)
    if not text:
        return DEFAULT_PROFILE
    # Explicit operations against network/remote infrastructure stay in the
    # infrastructure domain even when the request contains a quoted command.
    # Backticks alone are an engineering signal, but should not turn
    # "подключись по SSH и выполни `uname`" into a coding task.
    if _INFRA_ACTION_SIGNALS.search(text):
        return "Инфраструктура"
    if _CODE_SIGNALS.search(text):
        return "Инженерный"
    if _PERSONAL_SIGNALS.search(text):
        return "Личный"
    if _BUSINESS_SIGNALS.search(text):
        return "Деловой"
    if _INFRA_SIGNALS.search(text):
        return "Инфраструктура"
    if _MEDICAL_SIGNALS.search(text):
        return "Медицина"
    if _SCIENCE_SIGNALS.search(text):
        return "Научный"
    return DEFAULT_PROFILE


def classify_domain_policies(
    user_input: str | None,
    conversation_history: list[dict[str, object]] | None = None,
) -> tuple[str, ...]:
    """Return every relevant internal domain policy for one request.

    ``classify_mode`` still picks one dominant tone/persona overlay. Capability
    routing must not inherit that winner-takes-all behavior: a single task may
    legitimately combine code, networking, documents and current web evidence.
    """
    text = _routing_text(user_input, conversation_history)
    if not text:
        return (DEFAULT_PROFILE,)
    checks = (
        ("Инженерный", _CODE_SIGNALS),
        ("Личный", _PERSONAL_SIGNALS),
        ("Деловой", _BUSINESS_SIGNALS),
        ("Инфраструктура", _INFRA_SIGNALS),
        ("Медицина", _MEDICAL_SIGNALS),
        ("Научный", _SCIENCE_SIGNALS),
    )
    matched = tuple(name for name, pattern in checks if pattern.search(text))
    return matched or (DEFAULT_PROFILE,)


def resolve_persona_mode(
    requested: str | None,
    user_input: str | None,
    conversation_history: list[dict[str, object]] | None = None,
) -> str:
    """One live persona; legacy names remain valid wire and journal data.

    Task evidence routing is separate and never selects personality/sampling.
    """
    return DEFAULT_PROFILE
