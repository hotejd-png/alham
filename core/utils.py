from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import re


def safe_float(value, default=0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def normalize_timestamp(value):
    if value in (None, ""):
        return ""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).astimezone(timezone.utc).isoformat()
    except Exception:
        return str(value)


def text_blob(*parts) -> str:
    return " ".join([str(x) for x in parts if x is not None]).lower()


def classify_action(raw_type: str, raw_item: dict) -> str:
    blob = text_blob(
        raw_type,
        raw_item.get("type"),
        raw_item.get("side"),
        raw_item.get("status"),
        raw_item.get("action"),
    )
    if "merge" in blob:
        return "MERGE"
    if "sell" in blob:
        return "SELL"
    if "buy" in blob:
        return "BUY"
    if "redeem" in blob:
        return "REDEEM"
    if "split" in blob:
        return "SPLIT"
    return "OTHER"


def parse_dt(ts: str):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def seconds_between(ts1: str, ts2: str) -> int:
    d1 = parse_dt(ts1)
    d2 = parse_dt(ts2)
    if not d1 or not d2:
        return 0
    return int((d2 - d1).total_seconds())


def extract_window_start_ts(window_key: str):
    if not window_key:
        return None
    left = window_key.split("|")[0].strip()
    parts = left.split("-")
    if not parts:
        return None
    last = parts[-1]
    try:
        return int(last)
    except Exception:
        return None


def extract_market_end_ts(window_key: str):
    # Backward-compatible helper: infer window duration from slug (e.g. 5m, 15m).
    start_ts = extract_window_start_ts(window_key)
    if start_ts is None:
        return None
    return start_ts + extract_window_duration_seconds(window_key)


def extract_window_duration_seconds(window_key: str) -> int:
    # Try to parse "<number>m" or "<number>h" inside slug,
    # e.g. "...-5m-...", "...-15m-...", "...-1h-..."
    left = (window_key or "").split("|")[0].strip().lower()
    m = re.search(r"-(\d+)([mh])-", left)
    if m:
        try:
            value = int(m.group(1))
            unit = m.group(2)
            if value > 0 and unit == "m":
                return value * 60
            if value > 0 and unit == "h":
                return value * 3600
        except Exception:
            pass
    # Fallback to 5 minutes for old/unknown slugs.
    return 300


def iso_from_unix(ts: int):
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except Exception:
        return ""


def _fmt_12h(dt: datetime) -> str:
    return dt.strftime("%I:%M").lstrip("0") or "0:00"


def _fmt_24h(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def _last_sunday_day(year: int, month: int) -> int:
    # returns day number for last Sunday of given month
    if month == 12:
        next_month = datetime(year + 1, 1, 1)
    else:
        next_month = datetime(year, month + 1, 1)
    d = next_month - timedelta(days=1)
    while d.weekday() != 6:  # Sunday
        d -= timedelta(days=1)
    return d.day


def _first_sunday_day(year: int, month: int) -> int:
    d = datetime(year, month, 1)
    while d.weekday() != 6:  # Sunday
        d += timedelta(days=1)
    return d.day


def _second_sunday_day(year: int, month: int) -> int:
    return _first_sunday_day(year, month) + 7


def _is_kyiv_dst_utc(dt_utc: datetime) -> bool:
    # EU-style DST window in UTC:
    # starts last Sunday in March at 01:00 UTC, ends last Sunday in October at 01:00 UTC
    y = dt_utc.year
    start = datetime(y, 3, _last_sunday_day(y, 3), 1, 0, 0, tzinfo=timezone.utc)
    end = datetime(y, 10, _last_sunday_day(y, 10), 1, 0, 0, tzinfo=timezone.utc)
    return start <= dt_utc < end


def _is_newyork_dst_utc(dt_utc: datetime) -> bool:
    # US DST window in UTC:
    # starts second Sunday in March at 07:00 UTC (2:00 local standard),
    # ends first Sunday in November at 06:00 UTC (2:00 local daylight)
    y = dt_utc.year
    start = datetime(y, 3, _second_sunday_day(y, 3), 7, 0, 0, tzinfo=timezone.utc)
    end = datetime(y, 11, _first_sunday_day(y, 11), 6, 0, 0, tzinfo=timezone.utc)
    return start <= dt_utc < end


def _fallback_kyiv_tz_for_utc(dt_utc: datetime):
    return timezone(timedelta(hours=3 if _is_kyiv_dst_utc(dt_utc) else 2))


def _fallback_et_tz_for_utc(dt_utc: datetime):
    return timezone(timedelta(hours=-4 if _is_newyork_dst_utc(dt_utc) else -5))


def format_window_labels(window_key: str, local_tz_name: str = "Europe/Kyiv") -> tuple[str, str]:
    start_ts = extract_window_start_ts(window_key)
    end_ts = extract_market_end_ts(window_key)
    if start_ts is None or end_ts is None:
        return "", ""

    start_utc = datetime.fromtimestamp(start_ts, tz=timezone.utc)
    end_utc = datetime.fromtimestamp(end_ts, tz=timezone.utc)

    try:
        et_tz = ZoneInfo("America/New_York")
        start_et = start_utc.astimezone(et_tz)
        end_et = end_utc.astimezone(et_tz)
    except ZoneInfoNotFoundError:
        # Fallback with DST rules.
        start_et = start_utc.astimezone(_fallback_et_tz_for_utc(start_utc))
        end_et = end_utc.astimezone(_fallback_et_tz_for_utc(end_utc))

    try:
        local_tz = ZoneInfo(local_tz_name)
        start_local = start_utc.astimezone(local_tz)
        end_local = end_utc.astimezone(local_tz)
    except ZoneInfoNotFoundError:
        # Fallback with DST rules for Kyiv.
        if local_tz_name == "Europe/Kyiv":
            start_local = start_utc.astimezone(_fallback_kyiv_tz_for_utc(start_utc))
            end_local = end_utc.astimezone(_fallback_kyiv_tz_for_utc(end_utc))
        else:
            local_tz = timezone(timedelta(hours=0))
            start_local = start_utc.astimezone(local_tz)
            end_local = end_utc.astimezone(local_tz)

    ampm = end_et.strftime("%p")
    et_label = f"{_fmt_12h(start_et)}-{_fmt_12h(end_et)} {ampm} ET"
    local_label = f"{_fmt_24h(start_local)}-{_fmt_24h(end_local)} {local_tz_name}"
    return et_label, local_label


def window_local_date(window_key: str, local_tz_name: str = "Europe/Kyiv") -> str:
    start_ts = extract_window_start_ts(window_key)
    if start_ts is None:
        return "unknown_date"

    start_utc = datetime.fromtimestamp(start_ts, tz=timezone.utc)
    try:
        local_tz = ZoneInfo(local_tz_name)
        local_dt = start_utc.astimezone(local_tz)
    except ZoneInfoNotFoundError:
        if local_tz_name == "Europe/Kyiv":
            local_dt = start_utc.astimezone(_fallback_kyiv_tz_for_utc(start_utc))
        else:
            local_dt = start_utc.astimezone(timezone.utc)

    return local_dt.strftime("%Y-%m-%d")


def seconds_to_window_end(event_ts: str, window_key: str):
    d = parse_dt(event_ts)
    end_ts = extract_market_end_ts(window_key)
    if not d or end_ts is None:
        return None
    end_dt = datetime.fromtimestamp(end_ts, tz=timezone.utc)
    return int((end_dt - d).total_seconds())


def seconds_after_window_end(event_ts: str, window_key: str):
    d = parse_dt(event_ts)
    end_ts = extract_market_end_ts(window_key)
    if not d or end_ts is None:
        return None
    end_dt = datetime.fromtimestamp(end_ts, tz=timezone.utc)
    return int((d - end_dt).total_seconds())
