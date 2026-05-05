"""
debug.py — диагностический роутер.

GET /api/debug/search?q=test — тестирует ddgs напрямую и показывает ошибки.
GET /api/debug/library — показывает что бекенд-библиотека инжектирует в LLM.

Открой в браузере: http://127.0.0.1:8000/api/debug/search?q=курс+доллара+к+тенге
"""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/debug", tags=["debug"])


@router.get("/search")
def debug_search(q: str = "курс доллара к тенге 2025"):
    """Тестирует ddgs поиск напрямую."""
    results = {
        "query": q,
        "ddgs_available": False,
        "duckduckgo_search_available": False,
        "results": [],
        "errors": [],
    }

    # Проверяем какой пакет установлен
    DDGS = None
    try:
        from ddgs import DDGS
        results["ddgs_available"] = True
        results["package"] = "ddgs"
    except ImportError:
        try:
            from duckduckgo_search import DDGS
            results["duckduckgo_search_available"] = True
            results["package"] = "duckduckgo_search"
        except ImportError:
            results["errors"].append("НИ ОДИН пакет не установлен. Выполни: pip install duckduckgo-search")
            return JSONResponse(content=results)

    # Попытка 1: text с region
    try:
        with DDGS() as ddgs:
            raw = list(ddgs.text(q, max_results=5, region="wt-wt"))
        results["attempt1_region"] = {"ok": True, "count": len(raw), "sample": raw[:2] if raw else []}
        if raw:
            results["results"] = [{"title": r.get("title",""), "url": r.get("href",""), "snippet": r.get("body","")} for r in raw[:5]]
    except Exception as e:
        results["attempt1_region"] = {"ok": False, "error": str(e), "type": type(e).__name__}

    # Попытка 2: text без region
    if not results["results"]:
        try:
            with DDGS() as ddgs:
                raw = list(ddgs.text(q, max_results=5))
            results["attempt2_no_region"] = {"ok": True, "count": len(raw), "sample": raw[:2] if raw else []}
            if raw:
                results["results"] = [{"title": r.get("title",""), "url": r.get("href",""), "snippet": r.get("body","")} for r in raw[:5]]
        except Exception as e:
            results["attempt2_no_region"] = {"ok": False, "error": str(e), "type": type(e).__name__}

    # Попытка 3: answers
    try:
        with DDGS() as ddgs:
            if hasattr(ddgs, 'answers'):
                ans = list(ddgs.answers(q))
                results["attempt3_answers"] = {"ok": True, "count": len(ans), "sample": ans[:2] if ans else []}
    except Exception as e:
        results["attempt3_answers"] = {"ok": False, "error": str(e), "type": type(e).__name__}

    # Попытка 4: через requests напрямую
    try:
        import requests
        resp = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": q},
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=10,
        )
        results["attempt4_html"] = {
            "ok": resp.status_code == 200,
            "status": resp.status_code,
            "length": len(resp.text),
            "has_results": "result__body" in resp.text or "result__snippet" in resp.text,
        }
        if resp.status_code == 200 and not results["results"]:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, "html.parser")
            for item in soup.select(".result"):
                a = item.select_one("a.result__a")
                snip = item.select_one(".result__snippet")
                if a:
                    results["results"].append({
                        "title": a.get_text(strip=True),
                        "url": a.get("href", ""),
                        "snippet": snip.get_text(strip=True) if snip else "",
                        "source": "html_fallback",
                    })
            results["attempt4_html"]["parsed_count"] = len(results["results"])
    except Exception as e:
        results["attempt4_html"] = {"ok": False, "error": str(e), "type": type(e).__name__}

    return JSONResponse(content=results)


@router.get("/library")
def debug_library():
    """Показывает что бекенд-библиотека инжектирует."""
    try:
        from app.application.library.runtime import list_library_files, build_library_context

        files = list_library_files()
        context = build_library_context()
        return {
            "files": files,
            "context_preview": str(context.get("context", ""))[:500],
            "used_files": context.get("used_files", []),
            "active_count": context.get("active_count", 0),
        }
    except Exception as e:
        return {"error": str(e)}
