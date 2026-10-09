# -*- coding: utf-8 -*-
"""
weather.py — единый погодный инструмент (Open-Meteo).

Режимы:
  forecast — прогноз на 1..16 дней: дневная таблица, маркеры
             (ночная минусовая, день <=+5, снег, гололёд), forecast.json.
             matplotlib НЕ требуется.
  archive  — почасовой архив (ERA5 reanalysis): weather_table.json,
             график weather_<label>.png (matplotlib), SOURCES.md,
             опционально --markers: первые заморозки/снег/день <=+5 за период.

Оба режима принимают --lat --lon --timezone --out-dir.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

UA = {"User-Agent": "Elira-weather/1.0"}


def fetch(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def compass(deg):
    dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return dirs[int((deg + 11.25) // 22.5) % 16]


# ---------------------------------------------------------------- forecast

def cmd_forecast(a):
    url = ("https://api.open-meteo.com/v1/forecast?"
           f"latitude={a.lat}&longitude={a.lon}"
           "&daily=temperature_2m_max,temperature_2m_min,apparent_temperature_max,"
           "apparent_temperature_min,precipitation_sum,rain_sum,snowfall_sum,"
           "precipitation_probability_max,wind_speed_10m_max,wind_gusts_10m_max,"
           "weather_code"
           "&hourly=temperature_2m,precipitation_probability,precipitation,"
           "rain,snowfall,freezing_level_height,weather_code"
           f"&forecast_days={a.days}&timezone={urllib.parse.quote(a.timezone)}")
    try:
        if getattr(a, "input_json", None):
            with open(a.input_json, encoding="utf-8-sig") as source:
                d = json.load(source)
        else:
            d = fetch(url)
    except urllib.error.HTTPError as e:
        print("HTTP", e.code, e.read().decode("utf-8", "replace")[:500])
        raise SystemExit(1)

    os.makedirs(a.out_dir, exist_ok=True)
    jpath = os.path.join(a.out_dir, "forecast.json")
    if getattr(a, "input_json", None):
        if Path(a.input_json).resolve() != Path(jpath).resolve():
            shutil.copyfile(a.input_json, jpath)
    else:
        with open(jpath, "w", encoding="utf-8", newline="\n") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)

    from datetime import date
    weekdays = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
    daily = d["daily"]
    print("=== DAILY (%s) ===" % a.timezone)
    units = d.get("daily_units") or {}
    columns = (("Tmin", "temperature_2m_min"), ("Tmax", "temperature_2m_max"),
               ("fTmin", "apparent_temperature_min"), ("fTmax", "apparent_temperature_max"),
               ("rain", "rain_sum"), ("snow", "snowfall_sum"),
               ("prob", "precipitation_probability_max"), ("gust", "wind_gusts_10m_max"))
    labels = [f"{label}[{units.get(field) or 'не указана'}]" for label, field in columns]
    print("date weekday " + " ".join(labels) + " code")
    missing = [field for _, field in columns if not units.get(field)]
    if missing:
        print("UNIT_WARNING: источник не указал единицы для " + ", ".join(missing))
    for i, dt in enumerate(daily["time"]):
        print(f"{dt} {weekdays[date.fromisoformat(dt).weekday()]} {daily['temperature_2m_min'][i]:5.1f} "
              f"{daily['temperature_2m_max'][i]:5.1f} "
              f"{daily['apparent_temperature_min'][i]:5.1f} "
              f"{daily['apparent_temperature_max'][i]:5.1f} "
              f"{daily['rain_sum'][i]:6.1f} {daily['snowfall_sum'][i]:6.1f} "
              f"{daily['precipitation_probability_max'][i]:5.0f} "
              f"{daily['wind_gusts_10m_max'][i]:5.1f} {daily['weather_code'][i]}")

    print("\n=== MARKERS ===")
    first_frost = None
    for i, dt in enumerate(daily["time"]):
        tmin, tmax = daily["temperature_2m_min"][i], daily["temperature_2m_max"][i]
        rain, snow = daily["rain_sum"][i], daily["snowfall_sum"][i]
        tags = []
        if tmin is not None and tmin < 0:
            tags.append("НОЧЬ_МИНУС")
            if first_frost is None:
                first_frost = dt
        if tmax is not None and tmax <= 5:
            tags.append("ДЕНЬ_<=+5")
        if tmax is not None and tmax <= 0:
            tags.append("ДЕНЬ_МИНУС")
        if snow and snow > 0.1:
            tags.append("СНЕГ")
        if rain and rain > 0.5 and tmin is not None and tmin <= 1:
            tags.append("ГОЛОЛЁД_РИСК")
        if tags:
            print(f"{dt}: {', '.join(tags)}")

    tmins = [v for v in daily["temperature_2m_min"] if v is not None]
    tmaxs = [v for v in daily["temperature_2m_max"] if v is not None]
    print(f"MIN_TMIN: {min(tmins):.1f} C")
    print(f"MAX_TMAX: {max(tmaxs):.1f} C")
    print(f"FIRST_FROST: {first_frost or 'нет за период'}")

    h = d["hourly"]
    print("\n=== HOURLY 48h (холодные/мокрые часы) ===")
    for i, t in enumerate(h["time"][:48]):
        temp = h["temperature_2m"][i]
        rain = h["rain"][i] or 0
        snow = h["snowfall"][i] or 0
        prob = h["precipitation_probability"][i] or 0
        if temp <= 2 or (rain > 0.1 and temp <= 3) or snow > 0.05:
            print(f"{t}  T={temp:5.1f}  rain={rain:4.1f}  snow={snow:4.1f}  prob={prob:3.0f}%")

    print(f"OK forecast={jpath}")


# ----------------------------------------------------------------- archive

def cmd_archive(a):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    url = ("https://archive-api.open-meteo.com/v1/archive"
           f"?latitude={a.lat}&longitude={a.lon}"
           f"&start_date={a.start}&end_date={a.end}"
           "&hourly=wind_speed_10m,wind_gusts_10m,wind_direction_10m,temperature_2m,pressure_msl"
           f"&timezone={urllib.parse.quote(a.timezone)}")
    try:
        d = fetch(url)
    except urllib.error.HTTPError as e:
        print("HTTP", e.code, e.read().decode("utf-8", "replace")[:500])
        raise SystemExit(1)

    h = d["hourly"]
    times = [datetime.fromisoformat(t) for t in h["time"]]
    ws = [v / 3.6 for v in h["wind_speed_10m"]]   # km/h -> m/s
    wg = [v / 3.6 for v in h["wind_gusts_10m"]]
    wd = h["wind_direction_10m"]
    temp = h["temperature_2m"]
    pres = h["pressure_msl"]

    os.makedirs(a.out_dir, exist_ok=True)

    # 1) таблица
    table = [{"t": t.isoformat(), "wind_ms": round(ws[i], 1),
              "gust_ms": round(wg[i], 1), "dir": compass(wd[i]),
              "temp_c": temp[i], "hpa": pres[i]} for i, t in enumerate(times)]
    tpath = os.path.join(a.out_dir, "weather_table.json")
    with open(tpath, "w", encoding="utf-8") as f:
        json.dump(table, f, ensure_ascii=False, indent=1)

    # 2) график
    incident = datetime.fromisoformat(a.incident) if a.incident else None
    fig, axes = plt.subplots(3, 1, figsize=(13, 10), sharex=True)
    fig.suptitle(f"Погода: {a.label} ({a.lat}N, {a.lon}E) {a.start}..{a.end}\n"
                 f"Open-Meteo Archive (ERA5), {a.timezone}", fontsize=12)

    ax = axes[0]
    ax.plot(times, ws, color="#1f77b4", lw=1.6, label="средний ветер")
    ax.plot(times, wg, color="#d62728", lw=1.2, ls="--", label="порывы")
    if a.storm_band:
        ax.axhspan(a.storm_band[0], a.storm_band[1], color="orange", alpha=0.15)
        note = a.storm_note or f"штормовое: порывы {a.storm_band[0]}-{a.storm_band[1]} м/с"
        ax.text(times[1], a.storm_band[0] + 0.3, note, fontsize=8, color="#b35900")
    if incident:
        ax.axvline(incident, color="black", lw=1, ls=":")
        ax.text(incident, ax.get_ylim()[1] * 0.95, "инцидент", fontsize=8,
                rotation=90, va="top")
    ax.set_ylabel("м/с")
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(alpha=0.3)

    ax = axes[1]
    ax.plot(times, temp, color="#ff7f0e", lw=1.4)
    ax.set_ylabel("°C")
    ax.grid(alpha=0.3)

    ax = axes[2]
    ax.plot(times, pres, color="#2ca02c", lw=1.4)
    ax.set_ylabel("hPa")
    ax.grid(alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m %H:%M"))
    fig.autofmt_xdate()
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    ppath = os.path.join(a.out_dir, f"weather_{a.label}.png")
    fig.savefig(ppath, dpi=130)

    # 3) SOURCES.md
    src = [
        "# Источники: погодная база",
        "",
        f"- Точка: {a.label} ({a.lat}N, {a.lon}E)"
        + (f" — источник координат: {a.point_source}" if a.point_source else ""),
        f"- Диапазон: {a.start} .. {a.end}, таймзона {a.timezone}",
        f"- Данные: Open-Meteo Archive API (ERA5 reanalysis, почасовой), сетка ~31 км — оценка, не полевой замер.",
        f"- Запрос: {url}",
        f"- Атрибуция: Open-Meteo.com (CC BY 4.0), модель ERA5 (Copernicus ECMWF).",
        f"- Файлы: weather_table.json, weather_{a.label}.png",
    ]
    if incident:
        src.append(f"- Время инцидента: {incident.isoformat()}")
    if a.storm_note:
        src.append(f"- Штормовое (нанесено на график): {a.storm_note}")
    src.append("- Штормовые предупреждения госорганов искать отдельно и добавлять сюда вручную.")
    spath = os.path.join(a.out_dir, "SOURCES.md")
    with open(spath, "w", encoding="utf-8") as f:
        f.write("\n".join(src) + "\n")

    # 4) ключевые числа в stdout
    if incident:
        i = min(range(len(times)), key=lambda k: abs((times[k] - incident).total_seconds()))
        print(f"INCIDENT_HOUR: {times[i].isoformat()} wind {ws[i]:.1f} gust {wg[i]:.1f} dir {compass(wd[i])}")
    pk = max(range(len(wg)), key=lambda k: wg[k])
    print(f"PEAK_GUST: {times[pk].isoformat()} {wg[pk]:.1f} m/s")

    # 5) опциональные сезонные маркеры (дневной запрос)
    if a.markers:
        durl = ("https://archive-api.open-meteo.com/v1/archive"
                f"?latitude={a.lat}&longitude={a.lon}"
                f"&start_date={a.start}&end_date={a.end}"
                "&daily=temperature_2m_min,temperature_2m_max,rain_sum,snowfall_sum"
                f"&timezone={urllib.parse.quote(a.timezone)}")
        dd = fetch(durl)["daily"]
        first_frost = first_snow = first_tmax5 = None
        for i, dt in enumerate(dd["time"]):
            tmin, tmax = dd["temperature_2m_min"][i], dd["temperature_2m_max"][i]
            snow = dd["snowfall_sum"][i] or 0
            if tmin is not None and tmin < 0 and first_frost is None:
                first_frost = dt
            if snow > 0.1 and first_snow is None:
                first_snow = dt
            if tmax is not None and tmax <= 5 and first_tmax5 is None:
                first_tmax5 = dt
        print(f"FIRST_FROST: {first_frost or 'нет за период'}")
        print(f"FIRST_SNOW: {first_snow or 'нет за период'}")
        print(f"FIRST_TMAX_LE_5: {first_tmax5 or 'нет за период'}")

    print(f"OK table={tpath} chart={ppath} sources={spath}")


# ------------------------------------------------------------------- main

def main():
    p = argparse.ArgumentParser(description="Погода: прогноз (Forecast) и архив (ERA5), Open-Meteo")
    sub = p.add_subparsers(dest="mode", required=True)

    pf = sub.add_parser("forecast", help="прогноз на N дней (1..16)")
    pf.add_argument("--lat", type=float, required=True)
    pf.add_argument("--lon", type=float, required=True)
    pf.add_argument("--timezone", default="UTC", help="IANA, напр. Asia/Almaty")
    pf.add_argument("--input-json", help="Read saved forecast JSON without network access")
    pf.add_argument("--days", type=int, default=14)
    pf.add_argument("--out-dir", default=".")
    pf.set_defaults(func=cmd_forecast)

    pa = sub.add_parser("archive", help="почасовой архив ERA5")
    pa.add_argument("--lat", type=float, required=True)
    pa.add_argument("--lon", type=float, required=True)
    pa.add_argument("--start", required=True, help="YYYY-MM-DD")
    pa.add_argument("--end", required=True, help="YYYY-MM-DD")
    pa.add_argument("--timezone", default="UTC", help="IANA, напр. Asia/Almaty")
    pa.add_argument("--label", default="point", help="метка точки для заголовков/файлов")
    pa.add_argument("--out-dir", default=".")
    pa.add_argument("--incident", default=None, help="ISO время инцидента, напр. 2026-09-24T11:20")
    pa.add_argument("--storm-band", nargs=2, type=float, default=None,
                    help="диапазон порывов штормового, м/с (min max)")
    pa.add_argument("--storm-note", default=None, help="подпись штормового на графике")
    pa.add_argument("--point-source", default="", help="источник координат (в SOURCES.md)")
    pa.add_argument("--markers", action="store_true",
                    help="добавить FIRST_FROST / FIRST_SNOW / FIRST_TMAX_LE_5 за период")
    pa.set_defaults(func=cmd_archive)

    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
