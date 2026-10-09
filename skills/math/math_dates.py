"""Dates and durations with explicit holiday calendars and DST handling."""
import calendar
from decimal import Decimal

from elira_common.numbers import plain
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from math_common import fields, integer, outcome


def seconds(delta):
    return plain(Decimal(delta.days*86400+delta.seconds)+Decimal(delta.microseconds)/1_000_000)


def iso_date(value):
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError("дата: YYYY-MM-DD")
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("дата: YYYY-MM-DD")
    return parsed


def moment(value, zone=None, fold=None):
    if not isinstance(value, str):
        raise ValueError("момент: ISO datetime")
    parsed = datetime.fromisoformat(value[:-1]+"+00:00" if value.endswith("Z") else value)
    if parsed.tzinfo is not None:
        return parsed.astimezone(ZoneInfo(zone)) if zone else parsed
    if not zone:
        raise ValueError("для локального времени нужен часовой пояс IANA")
    tz = ZoneInfo(zone)
    options = [parsed.replace(tzinfo=tz, fold=f) for f in (0, 1)]
    valid = [item for item in options if item.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) == parsed]
    if not valid:
        raise ValueError("локальное время не существует из-за перехода DST")
    ambiguous = len({item.utcoffset() for item in valid}) > 1
    if ambiguous and fold is None:
        raise ValueError("неоднозначное локальное время DST: укажите fold 0 или 1")
    if fold is not None and (type(fold) is not int or fold not in (0, 1)):
        raise ValueError("fold: 0 или 1")
    return options[fold or 0]


def _holiday_calendar(raw):
    if not isinstance(raw, dict):
        raise ValueError("calendar: нужны country, years, holidays; календарь праздников передаётся явно")
    fields(raw, "country years holidays weekdays working_dates", "country years holidays")
    if not isinstance(raw["country"], str) or not raw["country"].strip():
        raise ValueError("calendar.country: страна обязательна")
    if not isinstance(raw["years"], list) or not raw["years"]:
        raise ValueError("calendar.years: перечислите годы покрытия")
    years = {integer(y, "year", 1, 9999) for y in raw["years"]}
    if not isinstance(raw["holidays"], list) or len(raw["holidays"]) > 5000:
        raise ValueError("calendar.holidays: список ISO-дат")
    holidays = {iso_date(d) for d in raw["holidays"]}
    overrides = raw.get("working_dates", [])
    if not isinstance(overrides, list) or len(overrides) > 5000:
        raise ValueError("working_dates: список ISO-дат до5000")
    working_dates = {iso_date(d) for d in overrides}
    if holidays & working_dates:
        raise ValueError("дата одновременно в holidays и working_dates")
    if any(d.year not in years for d in holidays | working_dates):
        raise ValueError("праздник вне годов покрытия")
    days = raw.get("weekdays", [1, 2, 3, 4, 5])
    if not isinstance(days, list) or not days:
        raise ValueError("calendar.weekdays: непустой список ISO-дней недели")
    weekdays = {integer(d, "weekday", 1, 7) for d in days}
    def working(day):
        if day.year not in years:
            raise ValueError(f"календарь праздников не покрывает {day.year}")
        return day in working_dates or day.isoweekday() in weekdays and day not in holidays
    return working


def dates(p, places=None):
    allowed = {"difference": "start end", "shift": "start days months years month_end", "business_days": "start end calendar",
               "business_add": "start days calendar", "recurrence": "start count interval_days interval_months month_end",
               "duration": "start end zone fold", "timezone": "start zone fold target_zone", "overlap": "intervals"}
    fields(p, "action " + allowed.get(p.get("action"), ""), "action")
    action = p["action"]
    if action in {"difference", "shift", "business_days", "business_add", "recurrence"}:
        start = iso_date(p.get("start"))
        if action in {"difference", "business_days"}:
            end = iso_date(p.get("end"))
            days = (end-start).days
            if abs(days) > 36600:
                raise ValueError("интервал: не более 36600 дней")
            if action == "difference":
                result = {"days": days}
            else:
                work = _holiday_calendar(p.get("calendar"))
                low, high = min(start, end), max(start, end)
                count = sum(work(low+timedelta(days=i)) for i in range((high-low).days))
                result = {"days": count if end >= start else -count, "interval": "[start,end)"}
        elif action == "business_add":
            count = integer(p.get("days"), "days", -10000, 10000)
            work = _holiday_calendar(p.get("calendar"))
            work(start)
            day, done = start, 0
            direction = 1 if count >= 0 else -1
            for _ in range(36600):
                if done == abs(count):
                    break
                day += timedelta(days=direction)
                done += int(work(day))
            else:
                raise ValueError("срок не найден в пределах 36600 дней")
            result = {"date": day.isoformat(), "start_counted": False}
        else:
            policy = p.get("month_end", "reject")
            if policy not in {"reject", "clamp"}:
                raise ValueError("month_end: reject или clamp")
            def shift(months, days):
                total = start.year*12+start.month-1+months
                year, month = total//12, total%12+1
                if not 1 <= year <= 9999:
                    raise ValueError("год за пределами 1–9999")
                last = calendar.monthrange(year, month)[1]
                if start.day > last and policy == "reject":
                    raise ValueError("день отсутствует в целевом месяце; month_end=clamp разрешает последний день")
                return date(year, month, min(start.day, last))+timedelta(days=days)
            if action == "shift":
                months = integer(p.get("months", 0), "months", -12000, 12000)+12*integer(p.get("years", 0), "years", -1000, 1000)
                days = integer(p.get("days", 0), "days", -36600, 36600)
                result = {"date": shift(months, days).isoformat()}
            else:
                count = integer(p.get("count"), "count", 1, 1000)
                months = integer(p.get("interval_months", 0), "interval_months", 0, 120)
                days = integer(p.get("interval_days", 0), "interval_days", 0, 3660)
                if months+days == 0:
                    raise ValueError("интервал повторения должен быть положительным")
                result = {"dates": [shift(i*months, i*days).isoformat() for i in range(count)], "anchored_to_start": True}
    elif action in {"duration", "timezone"}:
        start = moment(p.get("start"), p.get("zone"), p.get("fold"))
        if action == "timezone":
            result = {"datetime": start.astimezone(ZoneInfo(p["target_zone"])).isoformat()}
        else:
            end = moment(p.get("end"), p.get("zone"), p.get("fold"))
            result = {"seconds": seconds(end.astimezone(timezone.utc)-start.astimezone(timezone.utc))}
    elif action == "overlap":
        intervals = p.get("intervals")
        if not isinstance(intervals, list) or not 1 <= len(intervals) <= 50:
            raise ValueError("intervals: от 1 до 50 рабочих интервалов с датами")
        starts, ends = [], []
        for interval in intervals:
            if not isinstance(interval, dict):
                raise ValueError("interval: JSON object")
            fields(interval, "start end zone fold", "start end")
            start = moment(interval["start"], interval.get("zone"), interval.get("fold")).astimezone(timezone.utc)
            end = moment(interval["end"], interval.get("zone"), interval.get("fold")).astimezone(timezone.utc)
            if end <= start:
                raise ValueError("конец рабочего интервала должен быть позже начала")
            starts.append(start); ends.append(end)
        start, end = max(starts), min(ends)
        result = {"overlap": end > start, "start_utc": start.isoformat() if end > start else None,
                  "end_utc": end.isoformat() if end > start else None, "seconds": seconds(end-start) if end > start else "0"}
    else:
        raise ValueError("неизвестная операция даты/времени")
    return outcome(result, f"{action}; Gregorian dates; elapsed durations use UTC", p, units={"days": "day", "seconds": "s"})
