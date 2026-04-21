from __future__ import annotations

import html
import re
import time
import uuid
from typing import Any
from urllib import parse as urlparse
from urllib import request as urlrequest

from fastapi import HTTPException

from app.application.project_brain.files import (
    read_reference_context,
    read_text_file,
    resolve_project_file,
)
from app.application.project_brain.ollama import (
    call_ollama_json,
    fetch_ollama_tags,
    pick_model,
)
from app.application.project_brain.state import ATTACHMENT_INDEX, CHAT_SESSIONS


def route_task(
    text: str,
    mode: str,
    attachments: list[dict[str, Any]],
    selected_project_paths: list[str],
    web_enabled: bool,
) -> dict[str, Any]:
    if mode and mode != "auto":
        return {"mode": mode, "reason": "manual"}

    low = (text or "").lower()
    if any(x in low for x in ["Р Р…Р В°РЎР‚Р С‘РЎРѓРЎС“Р в„–", "Р С‘Р В·Р С•Р В±РЎР‚Р В°Р В¶", "image", "Р С”Р В°РЎР‚РЎвЂљР С‘Р Р…", "png", "sdxl", "flux"]):
        return {"mode": "image", "reason": "image markers"}
    if selected_project_paths or any(
        item.get("suffix") in {".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".css", ".html"}
        for item in attachments
    ):
        if any(x in low for x in ["Р С‘РЎРѓР С—РЎР‚Р В°Р Р†", "fix", "refactor", "patch", "endpoint", "Р С”Р С•Р Т‘", "api", "РЎвЂћРЎС“Р Р…Р С”РЎвЂ ", "Р С•РЎв‚¬Р С‘Р В±Р С”", "bug", "Р Т‘Р С•Р В±Р В°Р Р†РЎРЉ"]):
            return {"mode": "code", "reason": "code markers"}
    if any(x in low for x in ["Р С—Р В»Р В°Р Р…", "roadmap", "РЎв‚¬Р В°Р С–", "Р В°РЎР‚РЎвЂ¦Р С‘РЎвЂљР ВµР С”РЎвЂљ", "strategy", "РЎРѓРЎвЂљРЎР‚Р В°РЎвЂљР ВµР С–"]):
        return {"mode": "plan", "reason": "plan markers"}
    if attachments:
        return {"mode": "analyze", "reason": "attachments present"}
    if web_enabled and any(x in low for x in ["Р С”РЎвЂљР С•", "Р С”Р С•Р С–Р Т‘Р В°", "Р Р…Р С•Р Р†Р С•РЎРѓРЎвЂљ", "latest", "Р Р…Р В°Р в„–Р Т‘Р С‘", "search", "Р Р† Р С‘Р Р…РЎвЂљР ВµРЎР‚Р Р…Р ВµРЎвЂљР Вµ", "Р Т‘Р С•Р С”РЎС“Р СР ВµР Р…РЎвЂљР В°РЎвЂ ", "web", "Р Р†Р ВµР В±", "research"]):
        return {"mode": "research", "reason": "research markers"}
    return {"mode": "chat", "reason": "default"}


def clean_html_text(raw_html: str) -> str:
    text = re.sub(r"<script.*?</script>", " ", raw_html, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def search_web(query: str, limit: int = 5) -> list[dict[str, str]]:
    url = f"https://html.duckduckgo.com/html/?q={urlparse.quote_plus(query[:300])}"
    req = urlrequest.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlrequest.urlopen(req, timeout=20) as response:
            html_text = response.read().decode("utf-8", errors="ignore")
    except Exception:
        return []

    pattern = re.compile(
        r'<a[^>]*class="result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>.*?'
        r'<a[^>]*class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
        re.S,
    )
    results: list[dict[str, str]] = []
    for match in pattern.finditer(html_text):
        href = html.unescape(match.group("href"))
        title = clean_html_text(match.group("title"))
        snippet = clean_html_text(match.group("snippet"))
        if href.startswith("//"):
            href = "https:" + href
        if "duckduckgo.com/l/?uddg=" in href:
            parsed = urlparse.urlparse(href)
            query_params = urlparse.parse_qs(parsed.query)
            href = urlparse.unquote(query_params.get("uddg", [href])[0])
        if href.startswith("http"):
            results.append({"title": title, "url": href, "snippet": snippet})
        if len(results) >= limit:
            break
    return results


def fetch_web_page_text(url: str) -> str:
    req = urlrequest.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlrequest.urlopen(req, timeout=20) as response:
            content_type = response.headers.get("Content-Type", "")
            if "text/html" not in content_type and "text/plain" not in content_type:
                return ""
            raw = response.read().decode("utf-8", errors="ignore")
            text = clean_html_text(raw)
            return text[:6000]
    except Exception:
        return ""


def collect_web_context(query: str) -> list[dict[str, str]]:
    results = search_web(query, limit=4)
    enriched: list[dict[str, str]] = []
    for item in results[:3]:
        page_text = fetch_web_page_text(item["url"])
        enriched.append(
            {
                "title": item["title"],
                "url": item["url"],
                "snippet": item["snippet"],
                "page_text": page_text,
            }
        )
    return enriched


def build_code_prompt(
    goal: str,
    selected_path: str,
    selected_content: str,
    refs: list[dict[str, str]],
) -> str:
    refs_blob = "\n\n".join(f"FILE: {item['path']}\n{item['content']}" for item in refs)
    return (
        f"TASK:\n{goal}\n\n"
        f"TARGET FILE:\n{selected_path}\n\n"
        f"CURRENT CONTENT:\n{selected_content[:40000]}\n\n"
        f"REFERENCE FILES:\n{refs_blob[:30000]}\n\n"
        "Return a concrete update for the target file only."
    )


def build_chat_prompt(
    message: str,
    route_mode: str,
    attachments: list[dict[str, Any]],
    project_refs: list[dict[str, str]],
    web_results: list[dict[str, str]],
) -> str:
    attachment_blob = "\n\n".join(
        f"ATTACHMENT: {item['name']}\n{item.get('text', '')[:5000]}"
        for item in attachments
        if item.get("text_available")
    )
    refs_blob = "\n\n".join(
        f"PROJECT FILE: {item['path']}\n{item['content'][:5000]}"
        for item in project_refs
    )
    web_blob = "\n\n".join(
        f"WEB RESULT: {item['title']}\nURL: {item['url']}\nSNIPPET: {item['snippet']}\nPAGE: {item['page_text'][:3000]}"
        for item in web_results
    )
    return (
        f"USER TASK:\n{message}\n\n"
        f"ROUTE MODE: {route_mode}\n\n"
        f"ATTACHMENTS:\n{attachment_blob[:20000]}\n\n"
        f"PROJECT REFERENCES:\n{refs_blob[:20000]}\n\n"
        f"WEB RESULTS:\n{web_blob[:12000]}\n\n"
        "Respond for the chosen mode and keep it practical."
    )


def resolve_attachments(attachment_ids: list[str]) -> list[dict[str, Any]]:
    return [
        ATTACHMENT_INDEX[item_id]
        for item_id in attachment_ids
        if item_id in ATTACHMENT_INDEX
    ]


def send_chat_message(
    *,
    message: str,
    model_name: str | None,
    mode: str,
    web_enabled: bool,
    session_id: str | None,
    attachment_ids: list[str],
    selected_project_paths: list[str],
) -> dict[str, Any]:
    tags = fetch_ollama_tags()
    model = pick_model(model_name, tags)

    attachments = resolve_attachments(attachment_ids)
    route = route_task(
        message,
        mode,
        attachments,
        selected_project_paths,
        web_enabled,
    )
    project_refs = read_reference_context(selected_project_paths)
    web_results = (
        collect_web_context(message)
        if web_enabled and route["mode"] in {"research", "chat", "analyze", "plan"}
        else []
    )

    session_key = session_id or uuid.uuid4().hex[:12]
    session = CHAT_SESSIONS.setdefault(session_key, {"messages": []})

    if route["mode"] == "code":
        target_attachment = None
        for item in attachments:
            if item.get("source") == "project" and item.get("project_path"):
                target_attachment = item
                break
        if target_attachment is None and selected_project_paths:
            try:
                full_path, rel_path = resolve_project_file(selected_project_paths[0])
                content, _, _ = read_text_file(full_path)
                target_attachment = {
                    "project_path": str(rel_path).replace("\\", "/"),
                    "text": content,
                }
            except HTTPException:
                target_attachment = None

        if target_attachment is not None:
            refs = read_reference_context(
                selected_project_paths,
                selected_path=target_attachment["project_path"],
            )
            system_prompt = (
                "You are Elira coder agent. Return JSON only with keys: answer, plan, target_path, updated_content, notes. "
                "updated_content must contain the full replacement file content for target_path. "
                "Do not return markdown."
            )
            result = call_ollama_json(
                model,
                system_prompt,
                build_code_prompt(
                    message,
                    target_attachment["project_path"],
                    target_attachment["text"],
                    refs,
                ),
            )
            response = {
                "status": "ok",
                "session_id": session_key,
                "model": model,
                "route": route,
                "answer": str(result.get("answer", "")),
                "plan": result.get("plan") if isinstance(result.get("plan"), list) else [],
                "attachment_summaries": [
                    {
                        "id": item["id"],
                        "name": item["name"],
                        "size": item["size"],
                        "suffix": item["suffix"],
                        "source": item["source"],
                        "text_available": item["text_available"],
                        "preview": item.get("text", "")[:1200],
                    }
                    for item in attachments
                ],
                "selected_project_paths": selected_project_paths,
                "web_results": web_results,
                "agents_used": ["coder_agent", "reflection_v2"],
                "code_suggestion": {
                    "target_path": str(result.get("target_path") or target_attachment["project_path"]),
                    "updated_content": str(result.get("updated_content") or ""),
                    "notes": str(result.get("notes") or ""),
                },
            }
            session["messages"].append(
                {"role": "user", "content": message, "ts": time.time()}
            )
            session["messages"].append(
                {
                    "role": "assistant",
                    "content": response["answer"],
                    "ts": time.time(),
                    "route": route,
                }
            )
            return response

        route = {"mode": "analyze", "reason": "no project file attached for code mode"}

    system_prompt = (
        "You are Elira chat-first local agent. Return JSON only with keys: answer, plan, sources_note, suggested_agent, image_prompt. "
        "For plan mode, plan should be a list of short steps. For image requests, answer briefly and fill image_prompt."
    )
    result = call_ollama_json(
        model,
        system_prompt,
        build_chat_prompt(message, route["mode"], attachments, project_refs, web_results),
    )
    answer = str(result.get("answer") or "")
    plan = result.get("plan") if isinstance(result.get("plan"), list) else []
    if not answer:
        answer = "Р СњР Вµ РЎС“Р Т‘Р В°Р В»Р С•РЎРѓРЎРЉ Р С—Р С•Р В»РЎС“РЎвЂЎР С‘РЎвЂљРЎРЉ РЎРѓР С•Р Т‘Р ВµРЎР‚Р В¶Р В°РЎвЂљР ВµР В»РЎРЉР Р…РЎвЂ№Р в„– Р С•РЎвЂљР Р†Р ВµРЎвЂљ Р С•РЎвЂљ Р СР С•Р Т‘Р ВµР В»Р С‘."

    response = {
        "status": "ok",
        "session_id": session_key,
        "model": model,
        "route": route,
        "answer": answer,
        "plan": plan,
        "sources_note": str(result.get("sources_note") or ""),
        "suggested_agent": str(result.get("suggested_agent") or ""),
        "image_prompt": str(result.get("image_prompt") or ""),
        "attachment_summaries": [
            {
                "id": item["id"],
                "name": item["name"],
                "size": item["size"],
                "suffix": item["suffix"],
                "source": item["source"],
                "text_available": item["text_available"],
                "preview": item.get("text", "")[:1200],
            }
            for item in attachments
        ],
        "selected_project_paths": selected_project_paths,
        "web_results": web_results,
        "agents_used": [
            "planner_agent" if route["mode"] == "plan" else "chat_agent",
            "browser_agent" if web_results else None,
        ],
    }
    response["agents_used"] = [item for item in response["agents_used"] if item]

    session["messages"].append({"role": "user", "content": message, "ts": time.time()})
    session["messages"].append(
        {"role": "assistant", "content": answer, "ts": time.time(), "route": route}
    )
    return response


def run_local_agent_plan(
    *,
    goal: str,
    selected_path: str,
    selected_content: str,
    model_name: str | None,
) -> dict[str, Any]:
    tags = fetch_ollama_tags()
    model = pick_model(model_name, tags)
    system_prompt = "Return JSON only with keys: summary, steps, risks, selected_path. steps must be a list of short strings."
    user_prompt = (
        f"TASK:\n{goal}\n\nFILE:\n{selected_path}\n\nCONTENT:\n{selected_content[:30000]}"
    )
    result = call_ollama_json(model, system_prompt, user_prompt)
    return {
        "status": "ok",
        "provider": "ollama",
        "model": model,
        "summary": str(result.get("summary") or ""),
        "steps": result.get("steps") if isinstance(result.get("steps"), list) else [],
        "risks": result.get("risks") if isinstance(result.get("risks"), list) else [],
        "selected_path": selected_path,
    }


def run_local_agent(
    *,
    goal: str,
    selected_path: str,
    selected_content: str,
    model_name: str | None,
    project_files: list[str],
    mode: str,
) -> dict[str, Any]:
    tags = fetch_ollama_tags()
    model = pick_model(model_name, tags)
    refs = read_reference_context(project_files, selected_path=selected_path)
    system_prompt = (
        "You are Elira local coder agent. Return JSON only with keys: answer, plan, target_path, updated_content, notes. "
        "updated_content must be the full file content."
    )
    result = call_ollama_json(
        model,
        system_prompt,
        build_code_prompt(goal, selected_path, selected_content, refs),
    )
    return {
        "status": "ok",
        "provider": "ollama",
        "model": model,
        "mode": mode,
        "answer": str(result.get("answer") or ""),
        "plan": result.get("plan") if isinstance(result.get("plan"), list) else [],
        "target_path": str(result.get("target_path") or selected_path),
        "updated_content": str(result.get("updated_content") or ""),
        "notes": str(result.get("notes") or ""),
        "references_used": [item["path"] for item in refs],
    }

