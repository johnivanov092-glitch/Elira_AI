"""CSV overview for the code-agent `csv` tool: shape, columns, head and stats."""
from __future__ import annotations

from pathlib import Path

from app.core.config import DATA_DIR, GENERATED_DIR, UPLOAD_DIR

OUTPUT_DIR = GENERATED_DIR
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
WORKSPACE = DATA_DIR / "workspace"
WORKSPACE.mkdir(parents=True, exist_ok=True)
BACKEND_UPLOADS = UPLOAD_DIR


def analyze_csv(file_path: str, query: str = "") -> dict:
    """Анализирует CSV файл: статистика, первые строки, агрегации."""
    fp = Path(file_path)
    if not fp.exists():
        fp = WORKSPACE / file_path
        if not fp.exists():
            fp = BACKEND_UPLOADS / file_path
    if not fp.exists():
        return {"ok": False, "error": f"Не найден: {file_path}"}

    try:
        import pandas as pd
        df = pd.read_csv(fp, encoding="utf-8", on_bad_lines="skip")

        result = {
            "ok": True,
            "filename": fp.name,
            "shape": {"rows": len(df), "columns": len(df.columns)},
            "columns": list(df.columns),
            "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
            "head": df.head(5).to_dict(orient="records"),
            "describe": {},
            "nulls": df.isnull().sum().to_dict(),
        }

        # Статистика по числовым колонкам
        num_cols = df.select_dtypes(include=["int64", "float64"]).columns
        if len(num_cols) > 0:
            desc = df[num_cols].describe()
            result["describe"] = desc.to_dict()

        # A question is never executed as code: `eval` here let a read-only
        # tool run arbitrary Python without approval (fixed 2026-10-06).
        # Exact filtering/aggregation uses calculation.table via tool_csv.
        if query.strip():
            result["query_note"] = (
                "Запрос не исполняется как код. Для отбора и подсчётов используй параметры "
                "filters / group_by / aggregate инструмента csv."
            )

        return result
    except ImportError:
        return {"ok": False, "error": "pip install pandas"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
