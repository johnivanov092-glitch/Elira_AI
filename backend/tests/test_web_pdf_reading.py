from io import BytesIO
from types import SimpleNamespace

from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
import pytest

from app.application.code_agent.tools import _web


@pytest.fixture(autouse=True)
def native_pdf_only(monkeypatch):
    # These fixtures contain native text. OCR has separate tests and must not
    # contact the developer's server during this extraction/agent contract test.
    monkeypatch.setattr("app.application.pdf.runtime._server_ocr_pages",
                        lambda *a, **k: pytest.fail("Native PDF fixture must not require OCR"))


def pdf_bytes(texts=None):
    writer = PdfWriter()
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    font_ref = writer._add_object(font)
    for text in texts or ["Measured strain from gravitational waves."]:
        text += " Native text is embedded in this document for extraction without optical character recognition."
        page = writer.add_blank_page(width=600, height=800)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})})
        stream = DecodedStreamObject()
        stream.set_data(("BT /F1 12 Tf 30 700 Td (" + text + ") Tj ET").encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_find_reads_late_pdf_fact_without_corpus_storage(monkeypatch, tmp_path):
    from app.application.code_agent.agent_loop import stream_code_agent
    from app.application.web_evidence import corpus
    from unittest.mock import Mock

    data = pdf_bytes(["Unrelated introduction. " * 90] * 8 + ["Late result: false alarm rate below one event per 203000 years."])
    monkeypatch.setattr("app.application.web.ssrf_guard.check_ssrf", lambda *a, **k: None)
    download = Mock(side_effect=lambda url, **kwargs: SimpleNamespace(content=data, status_code=200, url=url,
        headers={"Content-Type": "application/pdf"}, close=lambda: None))
    monkeypatch.setattr("requests.get", download)
    monkeypatch.setattr(corpus, "ingest", lambda *a, **k: pytest.fail("No persistent corpus in this run"))
    turns = []
    def chat(**kwargs):
        turns.append(1)
        if len(turns) == 1:
            return {"message": {"tool_calls": [{"id": "read", "function": {"name": "web_fetch",
                "arguments": {"url": "https://example.org/long.pdf", "find": "false alarm rate"}}}]}}
        shown = next(m["content"] for m in reversed(kwargs["messages"]) if m["role"] == "tool")
        assert "203000 years" in shown
        import re
        source = re.search(r"\[\[source:[^\]]+\]\]", shown).group()
        return {"message": {"content": "Частота ложных срабатываний ниже одного события за 203000 лет. " + source}}
    events = list(stream_code_agent(user_message="Найди частоту ложных срабатываний в документе.", project_root=tmp_path,
        chat_fn=chat, permission_mode="bypass", auto_remember=False, num_ctx=65536, base_tools=["web_fetch"]))
    final = next(e for e in events if e["type"] == "final_response")
    assert final["answer_status"] == "complete"
    matching = [s for s in final["sources"] if "203000 years" in s.get("quote", "")]
    assert matching and all(s["offset"] > 6000 for s in matching)
    download.assert_called_once()


@pytest.mark.parametrize("fragment", ["", "#page=2"])
def test_real_pdf_extracts_text_never_binary_or_browser(monkeypatch, fragment):
    data = pdf_bytes()
    monkeypatch.setattr("app.application.web.ssrf_guard.check_ssrf", lambda *a, **k: None)
    monkeypatch.setattr("requests.get", lambda url, **kwargs: SimpleNamespace(
        content=data, text=data.decode("latin1"), status_code=200, url=url,
        headers={"Content-Type": "application/pdf"}, close=lambda: None))
    monkeypatch.setattr(_web, "_render_fallback", lambda *a: pytest.fail("PDF must not enter HTML browser fallback"))
    result = _web.tool_web_fetch(url="https://example.org/paper.pdf" + fragment)
    assert "%PDF" not in result["text"] and "xref" not in result["text"]
    if fragment:
        assert result["pages"][0]["fragment_found"] is False
        assert "а не запрошенная страница" in result["text"]
    assert result["ok"] and "Measured strain" in result["text"]
    assert all("#" not in source["url"] for source in result["sources"])
    assert any("Measured strain" in s.get("quote", "") for s in result["sources"])


def test_pdf_anchor_aliases_recover_in_real_coordinator_without_repeated_download(monkeypatch, tmp_path):
    from app.application.code_agent.agent_loop import stream_code_agent
    from app.application.code_agent.run_journal import RunJournal
    from unittest.mock import Mock
    data = pdf_bytes()
    monkeypatch.setattr("app.application.web.ssrf_guard.check_ssrf", lambda *a, **k: None)
    download = Mock(side_effect=lambda url, **kwargs: SimpleNamespace(content=data, status_code=200, url=url,
        headers={"Content-Type": "application/pdf"}, close=lambda: None))
    monkeypatch.setattr("requests.get", download)
    turns = []
    def chat(**kwargs):
        if not kwargs["tools"]:
            return {"message": {"content": "Прочитан исходный документ; якоря страницы не поддерживаются."}}
        turns.append(1)
        assert len(turns) < 8
        return {"message": {"tool_calls": [{"id": str(len(turns)), "function": {"name": "web_fetch",
            "arguments": {"url": f"https://example.org/paper.pdf#page={len(turns)}"}}}]}}
    events = list(stream_code_agent(user_message="Найди первичную публикацию и объясни результат.", project_root=tmp_path,
        chat_fn=chat, permission_mode="bypass", auto_remember=False, num_ctx=65536, base_tools=["web_fetch"]))
    # A web question answers from what was read instead of a «Задача не завершена» dead end.
    assert events[-1]["stop_reason"] == "answer"
    download.assert_called_once()
    state = RunJournal.load(events[-1]["run_id"]).state
    assert state["command_progress"]["whole_documents"] == ["https://example.org/paper.pdf"]
