
import httpx
from bs4 import BeautifulSoup

ENGINES = {
    "duckduckgo": "https://duckduckgo.com/html/?q=",
    "bing": "https://www.bing.com/search?q=",
    "google": "https://www.google.com/search?q=",
    "yahoo": "https://search.yahoo.com/search?p=",
    "yandex": "https://yandex.com/search/?text=",
}

async def search_multi(query: str):
    results = []

    async with httpx.AsyncClient(timeout=10) as client:
        for name, url in ENGINES.items():
            try:
                r = await client.get(url + query)
                soup = BeautifulSoup(r.text, "html.parser")

                links = soup.select("a")[:8]

                for a in links:
                    href = a.get("href")
                    if href and href.startswith("http"):
                        results.append({
                            "engine": name,
                            "url": href,
                            "title": a.text.strip()
                        })

            except Exception:
                pass

    return results
