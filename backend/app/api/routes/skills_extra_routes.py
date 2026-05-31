"""
skills_extra_routes.py — роуты дополнительных скиллов + плагины + webhook.
"""
from __future__ import annotations
from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.application.skills_extra import (
    encrypt_text, decrypt_text,
    create_zip, extract_zip,
    convert_file,
    test_regex,
    translate_text,
    analyze_csv,
    store_webhook, list_webhooks, clear_webhooks,
)
from app.application.plugins import (
    list_plugins, run_plugin, reload_plugins,
    enable_plugin, disable_plugin, get_plugin_info, update_plugin_settings,
)

router = APIRouter(prefix="/api/extra", tags=["extra-skills"])


# ── 🔐 Шифрование ──

class EncryptRequest(BaseModel):
    text: str

class DecryptRequest(BaseModel):
    token: str

@router.post("/encrypt")
def api_encrypt(p: EncryptRequest):
    return encrypt_text(p.text)

@router.post("/decrypt")
def api_decrypt(p: DecryptRequest):
    return decrypt_text(p.token)


# ── 📦 Архиватор ──

class ZipRequest(BaseModel):
    source_path: str
    output_name: str = ""

class UnzipRequest(BaseModel):
    zip_path: str
    dest: str = ""

@router.post("/zip/create")
def api_zip(p: ZipRequest):
    return create_zip(p.source_path, p.output_name)

@router.post("/zip/extract")
def api_unzip(p: UnzipRequest):
    return extract_zip(p.zip_path, p.dest)


# ── 🔄 Конвертер ──

class ConvertRequest(BaseModel):
    source_path: str
    target_format: str  # "xlsx", "csv", "docx"

@router.post("/convert")
def api_convert(p: ConvertRequest):
    return convert_file(p.source_path, p.target_format)


# ── 📐 Regex ──

class RegexRequest(BaseModel):
    pattern: str
    text: str
    flags: str = ""

@router.post("/regex")
def api_regex(p: RegexRequest):
    return test_regex(p.pattern, p.text, p.flags)


# ── 🌍 Переводчик ──

class TranslateRequest(BaseModel):
    text: str
    target_lang: str = "english"
    model: str = "qwen3:8b"

@router.post("/translate")
def api_translate(p: TranslateRequest):
    return translate_text(p.text, p.target_lang, p.model)


# ── 📈 CSV анализ ──

class CsvRequest(BaseModel):
    file_path: str
    query: str = ""

@router.post("/csv/analyze")
def api_csv(p: CsvRequest):
    return analyze_csv(p.file_path, p.query)


# ── 📡 Webhook ──

@router.post("/webhook/{source}")
async def api_webhook_receive(source: str, request: Request):
    """Принимает входящий webhook."""
    try:
        body = await request.json()
    except Exception:
        body = {"raw": (await request.body()).decode("utf-8", errors="replace")}
    return store_webhook(body, source=source)

@router.get("/webhook/list")
def api_webhook_list(limit: int = 20):
    return list_webhooks(limit)

@router.delete("/webhook/clear")
def api_webhook_clear():
    return clear_webhooks()


# ── 🔌 Плагины ──

class PluginRunRequest(BaseModel):
    name: str
    args: dict = {}

class PluginSettingsRequest(BaseModel):
    name: str
    settings: dict = {}

@router.get("/plugins/list")
def api_plugins_list():
    return list_plugins()

@router.get("/plugins/info/{name}")
def api_plugin_info(name: str):
    return get_plugin_info(name)

@router.post("/plugins/run")
def api_plugin_run(p: PluginRunRequest):
    return run_plugin(p.name, p.args)

@router.post("/plugins/enable/{name}")
def api_plugin_enable(name: str):
    return enable_plugin(name)

@router.post("/plugins/disable/{name}")
def api_plugin_disable(name: str):
    return disable_plugin(name)

@router.post("/plugins/settings")
def api_plugin_settings(p: PluginSettingsRequest):
    return update_plugin_settings(p.name, p.settings)

@router.post("/plugins/reload")
def api_plugins_reload():
    return reload_plugins()
