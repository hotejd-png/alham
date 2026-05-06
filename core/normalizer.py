import hashlib
import json

from core.utils import normalize_timestamp, safe_float, classify_action

def _to_list(payload):
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "items", "results"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []

def _calc_amount_usd(item: dict, price: float, size: float) -> float:
    direct = safe_float(
        item.get("value")
        or item.get("usdcSize")
        or item.get("usdc_size")
        or item.get("usdc")
        or item.get("notionalValue")
        or item.get("amountUsd")
        or item.get("amount_usd")
    )
    if direct > 0:
        return direct
    if price > 0 and size > 0:
        return round(price * size, 10)
    return 0.0

def _stable_hash(item: dict) -> str:
    try:
        blob = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except Exception:
        blob = str(item)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()

def _make_external_id(item: dict) -> str:
    # Best case: provider gives a real unique id.
    direct = item.get("id")
    if direct not in (None, ""):
        return str(direct)

    # Fallback: hash full payload to avoid collisions where one tx has many activity rows.
    return f"raw:{_stable_hash(item)}"

def normalize_activity_payload(payload):
    rows = []
    for item in _to_list(payload):
        raw_type = item.get("type") or item.get("activityType") or item.get("action") or ""
        price = safe_float(item.get("price") or item.get("premium"))
        size = safe_float(item.get("size") or item.get("amount") or item.get("shares"))
        amount_usd = _calc_amount_usd(item, price, size)

        rows.append({
            "external_id": _make_external_id(item),
            "timestamp": normalize_timestamp(item.get("timestamp") or item.get("createdAt") or item.get("time")),
            "activity_type": str(raw_type),
            "action_class": classify_action(str(raw_type), item),
            "market_slug": item.get("market_slug") or item.get("marketSlug") or item.get("slug") or "",
            "event_slug": item.get("event_slug") or item.get("eventSlug") or item.get("marketSlug") or "",
            "title": item.get("title") or item.get("question") or item.get("market") or "",
            "outcome": item.get("outcome") or item.get("sideLabel") or "",
            "side": item.get("side") or item.get("tradeSide") or "",
            "price": price,
            "size": size,
            "amount_usd": amount_usd,
            "asset_id": str(item.get("asset_id") or item.get("asset") or item.get("tokenID") or ""),
            "raw_json": item,
        })
    return rows

def normalize_trades_payload(payload):
    rows = []
    for item in _to_list(payload):
        price = safe_float(item.get("price") or item.get("premium"))
        size = safe_float(item.get("size") or item.get("amount") or item.get("shares"))
        amount_usd = _calc_amount_usd(item, price, size)

        rows.append({
            "external_id": _make_external_id(item),
            "timestamp": normalize_timestamp(item.get("timestamp") or item.get("createdAt") or item.get("time")),
            "market_slug": item.get("market_slug") or item.get("marketSlug") or item.get("slug") or "",
            "event_slug": item.get("event_slug") or item.get("eventSlug") or item.get("marketSlug") or "",
            "title": item.get("title") or item.get("question") or item.get("market") or "",
            "outcome": item.get("outcome") or item.get("sideLabel") or "",
            "side": str(item.get("side") or item.get("tradeSide") or ""),
            "price": price,
            "size": size,
            "amount_usd": amount_usd,
            "asset_id": str(item.get("asset_id") or item.get("asset") or item.get("tokenID") or ""),
            "raw_json": item,
        })
    return rows

def normalize_positions_payload(payload, closed: bool):
    rows = []
    for item in _to_list(payload):
        external_id = str(
            item.get("id")
            or item.get("positionId")
            or item.get("asset")
            or item.get("conditionId")
            or _stable_hash(item)
        )
        rows.append({
            "external_id": external_id,
            "snapshot_time": normalize_timestamp(item.get("timestamp") or item.get("updatedAt") or item.get("time")),
            "closed": 1 if closed else 0,
            "market_slug": item.get("market_slug") or item.get("marketSlug") or item.get("slug") or item.get("eventSlug") or "",
            "event_slug": item.get("event_slug") or item.get("eventSlug") or item.get("slug") or item.get("marketSlug") or "",
            "title": item.get("title") or item.get("question") or item.get("market") or "",
            "outcome": item.get("outcome") or "",
            "side": item.get("side") or "",
            "size": safe_float(item.get("size") or item.get("amount") or item.get("shares")),
            "avg_price": safe_float(item.get("avg_price") or item.get("averagePrice") or item.get("price")),
            "cur_price": safe_float(item.get("cur_price") or item.get("currentPrice")),
            "total_bought": safe_float(item.get("totalBought") or item.get("total_bought") or item.get("costBasis")),
            "realized_pnl": safe_float(item.get("realized_pnl") or item.get("realizedPnl")),
            "raw_json": item,
        })
    return rows
