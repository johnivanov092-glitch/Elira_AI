# Источники: weather

## API
- Open-Meteo Forecast API — https://open-meteo.com/en/docs (прогноз 1..16 дней)
- Open-Meteo Archive API (ERA5 reanalysis, Copernicus ECMWF) —
  https://open-meteo.com/en/docs/archive-api
- Атрибуция: Open-Meteo.com, лицензия CC BY 4.0.

## Состав пакета
- `weather.py` — единый скрипт, два подкоманды: `forecast` и `archive`.
  Слит из двух разовых скриптов:
  - `almaty_forecast.py` (прогноз 14 дней, координаты Алматы захардкожены)
    → режим `forecast` с параметрами `--lat --lon --timezone --days`;
  - `almaty_history.py` (первые заморозки/снег/день <=+5 по годам)
    → режим `archive --markers`;
  - прежний отдельный скрипт архива ERA5 с графиком
    → режим `archive` (логика сохранена 1:1, добавлен `--markers`).
- matplotlib нужен только режиму `archive` (график PNG); `forecast` —
  только стандартная библиотека (urllib, json, argparse).

## Проверки
- forecast: реальный запрос Open-Meteo Forecast, 14 дней, маркеры, forecast.json.
- archive: реальный запрос Open-Meteo Archive, таблица + PNG + SOURCES.md,
  PEAK_GUST сверен с таблицей; `--markers` на осеннем периоде.
