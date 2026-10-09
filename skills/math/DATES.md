# Дата, время и длительности

`operation=date_info, expression="2026-10-09,2026-10-12"` — дни недели ISO-дат (до31).
Остальные операции: `operation=dates` с action и полями:

| action | Поля |
| --- | --- |
| difference | start,end YYYY-MM-DD → знаковая разница календарных дней, интервал≤36600 дней. |
| shift | start, days -36600..36600 (0), months -12000..12000 (0), years -1000..1000 (0). Сначала месяцы/годы, потом дни. |
| recurrence | start,count1..1000,interval_days0..3660 (0),interval_months0..120 (0), интервал>0. Включает start, каждое повторение привязано к исходной дате. |
| business_days | start,end и calendar. Рабочие дни [start,end), знаковый результат. |
| business_add | start,days -10000..10000,calendar; исходная дата не считается. |
| duration | start,end ISO datetime (с Z/смещением либо zone), fold при неоднозначном локальном времени; фактическая длительность через UTC. |
| timezone | start ISO datetime,zone при локальной записи, target_zone IANA, fold при необходимости. |
| overlap | intervals: 1–50 {start,end,zone?,fold?} с конкретными датами. Пересечение всех интервалов UTC. |

shift/recurrence: month_end=reject (по умолчанию) или clamp для последнего дня короткого месяца.
Локальное несуществующее время при DST отвергается; неоднозначное требует fold0/1
либо явного UTC offset. IANA-зоны, например Asia/Almaty, Europe/Berlin; данные tzdata в .venv.

calendar: {country:"KZ",years:[2026],holidays:["2026-01-01",...],weekdays:[1,2,3,4,5],working_dates:[]}.
Страна и годы обязательны, все праздники/выходные и переносы задаются извне;
пустой holidays означает явно заданное отсутствие праздников, а не официальный календарь.
Навык не получает календарь страны автоматически. Выход за years отвергается.
weekdays — ISO-дни1..7 (по умолчанию пн–пт). working_dates — явные рабочие даты
для перенесённых рабочих выходных; пересечение с holidays отвергается.

Пример: `{"operation":"dates","action":"timezone","start":"2026-10-09T09:00:00","zone":"Asia/Almaty","target_zone":"UTC"}`.
