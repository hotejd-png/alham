from core.utils import text_blob
import re


def _match_window_keyword(blob: str, keyword: str) -> bool:
    k = (keyword or "").strip().lower()
    if not k:
        return False

    # Prevent "5m" matching "15m".
    if re.fullmatch(r"\d+m", k):
        pattern = rf"(?<!\d){re.escape(k)}(?!\d)"
        return re.search(pattern, blob) is not None

    return k in blob

def is_target_market(record: dict, settings) -> bool:
    blob = text_blob(
        record.get("market_slug"),
        record.get("event_slug"),
        record.get("title"),
        record.get("question"),
        record.get("market"),
        record.get("outcome"),
        record.get("asset_id"),
    )
    has_any = any(k.lower() in blob for k in settings.filters.keywords_any)
    if not has_any:
        return False

    has_window = any(_match_window_keyword(blob, k) for k in settings.filters.keywords_window)

    # Legacy mode: strict 5m profile.
    if settings.filters.only_five_minute_crypto:
        return has_window

    # Generic window mode (e.g. 5m/15m/1h), still bounded by keywords_window.
    if settings.filters.keywords_window:
        return has_window

    # Fallback: if no window keywords configured, allow all crypto matches.
    return True

def make_window_key(record: dict) -> str:
    return " | ".join([
        str(record.get("market_slug") or record.get("market") or "unknown"),
        str(record.get("event_slug") or record.get("market_slug") or ""),
    ])
