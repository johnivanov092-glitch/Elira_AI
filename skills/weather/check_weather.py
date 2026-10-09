# -*- coding: utf-8 -*-
"""check_weather.py — содержательная проверка результатов skill_check.
Аргументы: <out_dir> <report_path>. Читает реальные артефакты из out_dir
и пишет report JSON. out_dir должен быть ВНЕ каталога кандидата.
"""
import json
import re
import sys
from pathlib import Path


def read_text(p):
    """Читает файл, пробуя utf-8, затем cp866 (редирект cmd.exe пишет в коду консоли)."""
    b = p.read_bytes()
    for enc in ("utf-8", "cp866"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", "replace")


def main():
    out = Path(sys.argv[1])
    report = Path(sys.argv[2])
    checks = []

    def add(name, ok):
        checks.append({"name": name, "passed": bool(ok)})

    # 1) прогноз: 14 дней, таймзона, полнота
    try:
        fc = json.loads((out / "forecast.json").read_text("utf-8"))
        d = fc["daily"]
        ok = (len(d["time"]) == 14 and fc.get("timezone", "").startswith("Asia")
              and all(len(d[k]) == 14 for k in
                      ("temperature_2m_min", "temperature_2m_max", "rain_sum", "snowfall_sum"))
              and all(v is not None for v in d["temperature_2m_min"] + d["temperature_2m_max"]))
        add("forecast_14d_valid", ok)
    except Exception:
        add("forecast_14d_valid", False)

    # 2) архив: почасовая таблица на весь диапазон (3 дня = ~72 записи)
    try:
        t = json.loads((out / "weather_table.json").read_text("utf-8"))
        ok = (len(t) >= 70
              and all(set(x) >= {"t", "wind_ms", "gust_ms", "dir", "temp_c", "hpa"} for x in t[:5])
              and t[0]["t"].startswith("2026-09-23") and t[-1]["t"].startswith("2026-09-25"))
        add("archive_table_full", ok)
    except Exception:
        add("archive_table_full", False)

    # 3) график PNG валидный
    try:
        p = out / "weather_kyzylkum.png"
        ok = p.exists() and p.stat().st_size > 10 * 1024
        add("archive_png_valid", ok)
    except Exception:
        add("archive_png_valid", False)

    # 4) SOURCES.md: URL, ERA5, оговорка о сетке
    try:
        s = (out / "SOURCES.md").read_text("utf-8")
        ok = ("archive-api.open-meteo.com" in s and "ERA5" in s and "31 км" in s)
        add("sources_complete", ok)
    except Exception:
        add("sources_complete", False)

    # 5) PEAK_GUST из stdout согласуется с максимумом в таблице
    try:
        log = read_text(out / "archive.stdout.log")
        m = re.search(r"PEAK_GUST: (\S+) (\d+\.\d+) m/s", log)
        t = json.loads((out / "weather_table.json").read_text("utf-8"))
        mx = max(x["gust_ms"] for x in t)
        ok = m is not None and abs(float(m.group(2)) - mx) < 0.15
        add("peak_gust_consistent", ok)
    except Exception:
        add("peak_gust_consistent", False)

    # 6) сезонные маркеры напечатаны
    try:
        log = read_text(out / "archive.stdout.log")
        ok = ("FIRST_FROST:" in log and "FIRST_SNOW:" in log and "FIRST_TMAX_LE_5:" in log)
        add("markers_present", ok)
    except Exception:
        add("markers_present", False)

    report.write_text(json.dumps({"checks": checks}, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps(checks, ensure_ascii=False))
    sys.exit(0 if all(c["passed"] for c in checks) else 1)


if __name__ == "__main__":
    main()
