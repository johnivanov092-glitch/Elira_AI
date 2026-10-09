"""Source fidelity instructions are delivered by the mutable Web skill."""
from pathlib import Path
from app.application.code_agent.task_guidance import task_guidance_blocks


def test_retrieval_guidance_keeps_source_fidelity_and_operational_contracts():
    guidance = (Path(__file__).resolve().parents[2] / "skills/web-research/SKILL.md").read_text(encoding="utf-8")
    for requirement in (
        "API либо локального файла", "предмет, группу, условия применимости",
        "Сохраняй отрицания и силу вывода",
        "Дата публикации, индексации или чтения не доказывает дату события",
        "обнаружение, а не содержание", "[[source:id]]", "первичные источники",
        "текущим официальным индексом нужного канала", "в той же ветке и компоненте",
        "учитывай backport", "предупреждения поисковых движков",
        "fetch --store", "query", "--no-cache", "только по прямому запросу",
        "работает для HTML и текста", "используй `browser`", "PDF читай навыком document-read",
    ):
        assert requirement in guidance
    assert "web" not in task_guidance_blocks({"read_file", "run_bash"})
