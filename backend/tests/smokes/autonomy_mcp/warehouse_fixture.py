"""A fictional HTTP warehouse. Deliberately contains no MCP implementation."""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
from pathlib import Path
import threading
import time
from urllib.parse import parse_qs, urlsplit

PAGE_SIZE = 3
BASE_ROWS = (
    ("A-001", "Бумага белая", 1250, 5000),
    ("A-002", "Бумага кремовая", 6500, 6000),
    ("A-003", "Картон, матовый", 0, 3000),
    ("A-004", "Конверты", 8750, 8750),
    ("A-005", "Плёнка", 999, 1000),
    ("A-006", "Краска синяя", 14200, 4000),
    ("A-007", "Краска чёрная", 3125, 10000),
    ("A-008", "Клей", 50, 250),
    ("A-009", "Папки", 15000, 5000),
    ("A-010", "Пружины", 2500, 2500),
    ("Я-011", "Лента «Север»", 1001, 2000),
)


def make_truth(phase: int) -> list[dict]:
    """Integer source records, independent of both HTTP encodings and agent CSVs."""
    rows = [{"sku": sku, "name": name,
             "quantity_milli": (quantity + (phase - 1) * (index + 1) * 137) % 17000,
             "minimum_milli": minimum}
            for index, (sku, name, quantity, minimum) in enumerate(BASE_ROWS)]
    for index in range(phase - 1):
        rows.append({"sku": f"Я-{12 + index:03d}", "name": f"Новая позиция {index + 1}",
                     "quantity_milli": 333 + phase * index, "minimum_milli": 900})
    return rows


def decimal_text(milli: int) -> str:
    return f"{milli // 1000}.{milli % 1000:03d}"


def api_docs(phase: int, base_url: str) -> str:
    shared = (
        "# Демонстрационный склад\n\n"
        "Это вымышленный HTTP-сервис, не MCP. Авторизации нет. Все операции только читают данные.\n"
        f"Базовый URL: {base_url}\n"
        "Склад один. SKU уникален. Отрицательных остатков нет. Значения количества точные, "
        "допускают три десятичных знака. Нулевой остаток — реальный товар, его нельзя пропускать. "
        "Дефицит = max(минимальный запас − остаток, 0).\n\n"
    )
    if phase == 1:
        return shared + (
            "## Действующий контракт v1\n"
            "GET /v1/stock?page=1\n"
            "JSON: {items:[{sku:string,name:string,quantity:string,minimum:string}],"
            "next_page:integer|null,total:integer}. quantity/minimum заданы в обычных единицах, "
            "десятичный разделитель — точка. Читайте next_page до null; данные разбиты на страницы.\n"
        )
    return shared + (
        "## Действующий контракт v2\n"
        "GET /v2/inventory (первая страница), затем GET /v2/inventory?cursor=<next_cursor>.\n"
        "JSON: {records:[{code:string,label:string,available_milliunits:integer,"
        "minimum_milliunits:integer}],next_cursor:string|null,total:integer}. "
        "code — прежний sku, label — имя. Количества теперь в тысячных долях: "
        "1250 milliunits = 1.250 обычной единицы. Читайте все страницы до next_cursor=null.\n"
        "Старый /v1/stock возвращает HTTP 410 contract_retired. Повтор старого запроса "
        "без обновления адаптера не исправит эту ошибку.\n"
    )


class WarehouseServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, truth: dict[int, list[dict]], audit_path: Path):
        self.phase = 1
        self.truth = truth
        self.audit_path = audit_path
        self.audit_lock = threading.Lock()
        super().__init__(("127.0.0.1", port), WarehouseHandler)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server_port}"

    def record(self, value: dict) -> None:
        with self.audit_lock:
            with self.audit_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps({"time_ns": time.time_ns(), **value}, ensure_ascii=False) + "\n")


class WarehouseHandler(BaseHTTPRequestHandler):
    server: WarehouseServer

    def log_message(self, _format: str, *_args) -> None:
        return

    def do_GET(self) -> None:
        phase = self.server.phase
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query)
        status, page_index, data = 200, None, None
        content_type = "application/json; charset=utf-8"
        rows = self.server.truth[phase]
        if parsed.path in {"/", "/docs"}:
            body = api_docs(phase, self.server.base_url).encode("utf-8")
            content_type = "text/markdown; charset=utf-8"
        elif parsed.path == "/v1/stock" and phase != 1:
            status = 410
            data = {"error": "contract_retired", "message": "Read /docs for the v2 inventory API."}
        elif parsed.path == "/v1/stock":
            try:
                page_index = int(query.get("page", ["1"])[0]) - 1
                if page_index < 0 or page_index * PAGE_SIZE >= len(rows):
                    raise ValueError
                selected = rows[page_index * PAGE_SIZE:(page_index + 1) * PAGE_SIZE]
                data = {"items": [{"sku": item["sku"], "name": item["name"],
                                   "quantity": decimal_text(item["quantity_milli"]),
                                   "minimum": decimal_text(item["minimum_milli"])} for item in selected],
                        "next_page": page_index + 2 if (page_index + 1) * PAGE_SIZE < len(rows) else None,
                        "total": len(rows)}
            except (ValueError, TypeError):
                status, data = 400, {"error": "invalid_page"}
        elif parsed.path == "/v2/inventory" and phase != 1:
            try:
                cursors = {f"batch-{phase}-{index}": index for index in range(1, (len(rows) - 1) // PAGE_SIZE + 1)}
                cursor = query.get("cursor", [None])[0]
                page_index = 0 if cursor is None else cursors[cursor]
                selected = rows[page_index * PAGE_SIZE:(page_index + 1) * PAGE_SIZE]
                data = {"records": [{"code": item["sku"], "label": item["name"],
                                     "available_milliunits": item["quantity_milli"],
                                     "minimum_milliunits": item["minimum_milli"]} for item in selected],
                        "next_cursor": f"batch-{phase}-{page_index + 1}" if (page_index + 1) * PAGE_SIZE < len(rows) else None,
                        "total": len(rows)}
            except (KeyError, ValueError, TypeError):
                status, data = 400, {"error": "invalid_cursor"}
        else:
            status, data = 404, {"error": "not_found", "documentation": "/docs"}
        if data is not None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.server.record({"phase": phase, "method": "GET", "path": self.path,
                            "status": status, "page_index": page_index, "response_sha256": hashlib.sha256(body).hexdigest()})
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        self.send_error(405, "Read-only warehouse")

    do_PUT = do_PATCH = do_DELETE = do_POST


@contextmanager
def serve(port: int, truth: dict[int, list[dict]], audit_path: Path):
    server = WarehouseServer(port, truth, audit_path)
    thread = threading.Thread(target=server.serve_forever, name="acceptance-warehouse", daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if thread.is_alive():
            raise RuntimeError("Warehouse fixture did not stop")
