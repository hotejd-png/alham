from collections import defaultdict
from core.filters import is_target_market, make_window_key
from core.utils import (
    seconds_between,
    seconds_to_window_end,
    seconds_after_window_end,
    extract_market_end_ts,
    extract_window_duration_seconds,
    iso_from_unix,
    parse_dt,
    format_window_labels,
)

def activity_counts(rows: list[dict]) -> list[dict]:
    grouped = defaultdict(lambda: {"count": 0, "usd": 0.0, "shares": 0.0})
    for r in rows:
        key = r.get("action_class", "OTHER")
        grouped[key]["count"] += 1
        grouped[key]["usd"] += float(r.get("amount_usd", 0.0))
        grouped[key]["shares"] += float(r.get("size", 0.0))
    out = []
    for key, val in grouped.items():
        out.append({
            "action_class": key,
            "count": val["count"],
            "usd": round(val["usd"], 4),
            "shares": round(val["shares"], 4),
        })
    out.sort(key=lambda x: (-x["count"], x["action_class"]))
    return out

def _build_window_buckets(rows: list[dict], settings):
    grouped = defaultdict(list)
    for r in rows:
        if not is_target_market(r, settings):
            continue
        key = make_window_key(r)
        grouped[key].append(r)

    for key in grouped:
        grouped[key].sort(key=lambda x: (x.get("timestamp", ""), x.get("external_id", "")))
    return grouped

def _build_cycles(events: list[dict]) -> list[dict]:
    """
    Цикл = серия BUY до очередного MERGE.
    После MERGE начинается новый цикл.
    """
    cycles = []
    current = {
        "buy_count": 0,
        "buy_usd": 0.0,
        "buy_up_shares": 0.0,
        "buy_down_shares": 0.0,
        "merge_count": 0,
        "merge_size": 0.0,
        "first_ts": "",
        "last_ts": "",
        "closed_by_merge": False,
    }

    def push_current():
        nonlocal current
        if (
            current["buy_count"] > 0
            or current["merge_count"] > 0
            or current["merge_size"] > 0
        ):
            if current["first_ts"] and current["last_ts"]:
                current["duration_sec"] = seconds_between(current["first_ts"], current["last_ts"])
            else:
                current["duration_sec"] = 0
            cycles.append(current)

        current = {
            "buy_count": 0,
            "buy_usd": 0.0,
            "buy_up_shares": 0.0,
            "buy_down_shares": 0.0,
            "merge_count": 0,
            "merge_size": 0.0,
            "first_ts": "",
            "last_ts": "",
            "closed_by_merge": False,
        }

    for e in events:
        ts = e.get("timestamp", "")
        action = e.get("action_class", "OTHER")
        outcome = str(e.get("outcome", "")).lower()
        shares = float(e.get("size", 0.0))
        usd = float(e.get("amount_usd", 0.0))

        if not current["first_ts"]:
            current["first_ts"] = ts
        current["last_ts"] = ts

        if action == "BUY":
            current["buy_count"] += 1
            current["buy_usd"] += usd

            if "yes" in outcome or "up" in outcome:
                current["buy_up_shares"] += shares
            elif "no" in outcome or "down" in outcome:
                current["buy_down_shares"] += shares

        elif action == "MERGE":
            current["merge_count"] += 1
            current["merge_size"] += shares
            current["closed_by_merge"] = True
            push_current()

    push_current()
    return cycles

def window_summary(rows: list[dict], settings) -> list[dict]:
    grouped = _build_window_buckets(rows, settings)
    out = []
    grace_before_start_sec = 10
    grace_after_end_sec = 30

    for key, events in grouped.items():
        window_duration_sec = extract_window_duration_seconds(key)
        market_start_unix = extract_market_end_ts(key)
        market_start_unix = (market_start_unix - window_duration_sec) if market_start_unix else None
        market_end_unix = extract_market_end_ts(key)
        stats_start_unix = (market_start_unix - grace_before_start_sec) if market_start_unix else None
        stats_end_unix = (market_end_unix + grace_after_end_sec) if market_end_unix else None

        stats_events = []
        before_window_events = 0
        after_window_events = 0
        for e in events:
            dt = parse_dt(e.get("timestamp", ""))
            if dt is None or stats_start_unix is None or stats_end_unix is None:
                stats_events.append(e)
                continue
            unix_ts = int(dt.timestamp())
            if stats_start_unix <= unix_ts <= stats_end_unix:
                stats_events.append(e)
            elif unix_ts < stats_start_unix:
                before_window_events += 1
            elif unix_ts > stats_end_unix:
                after_window_events += 1

        if not stats_events:
            stats_events = list(events)

        event_count = len(stats_events)
        buy_count = sum(1 for e in stats_events if e.get("action_class") == "BUY")
        sell_count = sum(1 for e in stats_events if e.get("action_class") == "SELL")
        merge_count = sum(1 for e in stats_events if e.get("action_class") == "MERGE")

        first_ts = stats_events[0].get("timestamp", "")
        last_ts = stats_events[-1].get("timestamp", "")
        last_action = stats_events[-1].get("action_class", "OTHER")
        duration_sec = seconds_between(first_ts, last_ts)
        late_action = duration_sec >= 240

        gross_usd = sum(float(e.get("amount_usd", 0.0)) for e in stats_events)

        yes_shares = 0.0
        no_shares = 0.0
        yes_cost = 0.0
        no_cost = 0.0

        first_merge_ts = ""
        first_merge_index = None
        max_merge_size = 0.0
        total_merge_size = 0.0

        for idx, e in enumerate(stats_events):
            action = e.get("action_class", "OTHER")
            outcome = str(e.get("outcome", "")).lower()
            shares = float(e.get("size", 0.0))
            price = float(e.get("price", 0.0))

            if action == "MERGE":
                if first_merge_index is None:
                    first_merge_index = idx
                    first_merge_ts = e.get("timestamp", "")
                if shares > max_merge_size:
                    max_merge_size = shares
                total_merge_size += shares

            if action == "BUY":
                if "yes" in outcome or "up" in outcome:
                    yes_shares += shares
                    yes_cost += shares * price
                elif "no" in outcome or "down" in outcome:
                    no_shares += shares
                    no_cost += shares * price

        avg_yes = yes_cost / yes_shares if yes_shares else 0.0
        avg_no = no_cost / no_shares if no_shares else 0.0
        pairs = min(yes_shares, no_shares)
        edge_per_share = 1.0 - (avg_yes + avg_no) if pairs > 0 else 0.0
        estimated_merge_pnl = pairs * edge_per_share if edge_per_share > 0 else 0.0
        pair_cost_per_share = avg_yes + avg_no if pairs > 0 else 0.0
        merged_pairs_est_shares = min(total_merge_size, pairs)
        merge_coverage_pct = (merged_pairs_est_shares / pairs * 100.0) if pairs > 0 else 0.0
        unpaired_yes_shares = max(yes_shares - pairs, 0.0)
        unpaired_no_shares = max(no_shares - pairs, 0.0)
        unpaired_shares = unpaired_yes_shares + unpaired_no_shares
        total_buy_shares = yes_shares + no_shares
        unpaired_ratio_pct = (unpaired_shares / total_buy_shares * 100.0) if total_buy_shares > 0 else 0.0

        # Conservative pnl guard:
        # If unpaired tail goes against us, max loss is its buy cost.
        unpaired_cost_usd = (unpaired_yes_shares * avg_yes) + (unpaired_no_shares * avg_no)
        merged_pair_pnl_est_usd = merged_pairs_est_shares * edge_per_share if edge_per_share > 0 else 0.0
        pnl_floor_usd = merged_pair_pnl_est_usd - unpaired_cost_usd
        invested_usd = yes_cost + no_cost
        pnl_floor_pct = (pnl_floor_usd / invested_usd * 100.0) if invested_usd > 0 else 0.0

        if (
            pair_cost_per_share <= 0.985
            and merge_coverage_pct >= 85.0
            and unpaired_ratio_pct <= 10.0
            and pnl_floor_pct >= 0.5
        ):
            window_quality = "GOOD"
        elif (
            pair_cost_per_share <= 0.995
            and merge_coverage_pct >= 55.0
            and unpaired_ratio_pct <= 25.0
            and pnl_floor_pct >= 0.0
        ):
            window_quality = "MID"
        else:
            window_quality = "WEAK"

        buys_before_first_merge = 0
        buys_after_first_merge = 0
        merges_after_first = 0
        had_buy_after_merge = False

        if first_merge_index is not None:
            for idx, e in enumerate(stats_events):
                action = e.get("action_class", "OTHER")
                if idx < first_merge_index and action == "BUY":
                    buys_before_first_merge += 1
                elif idx > first_merge_index and action == "BUY":
                    buys_after_first_merge += 1
                    had_buy_after_merge = True
                elif idx > first_merge_index and action == "MERGE":
                    merges_after_first += 1
        else:
            buys_before_first_merge = buy_count

        seconds_until_first_merge = seconds_between(first_ts, first_merge_ts) if first_merge_ts else None

        last_buy_ts = ""
        for e in reversed(stats_events):
            if e.get("action_class") == "BUY":
                last_buy_ts = e.get("timestamp", "")
                break

        market_end_iso = iso_from_unix(market_end_unix) if market_end_unix else ""
        market_start_iso = iso_from_unix(market_start_unix) if market_start_unix else ""
        window_et_label, window_local_label = format_window_labels(key)

        first_sec_from_start = None
        last_sec_from_start = None

        first_dt = parse_dt(first_ts)
        last_dt = parse_dt(last_ts)
        if market_start_unix is not None and first_dt is not None:
            first_sec_from_start = int(first_dt.timestamp()) - market_start_unix
        if market_start_unix is not None and last_dt is not None:
            last_sec_from_start = int(last_dt.timestamp()) - market_start_unix

        last_buy_after_end_sec = None
        if last_buy_ts:
            last_buy_after_end_sec = seconds_after_window_end(last_buy_ts, key)

        cycles = _build_cycles(stats_events)
        cycle_count = len(cycles)
        max_cycle_buy_count = max((c["buy_count"] for c in cycles), default=0)
        max_cycle_merge_size = max((c["merge_size"] for c in cycles), default=0.0)
        avg_cycle_buy_count = round(sum(c["buy_count"] for c in cycles) / cycle_count, 2) if cycle_count else 0.0

        out.append({
            "window_key": key,
            "event_count": event_count,
            "buy_count": buy_count,
            "sell_count": sell_count,
            "merge_count": merge_count,
            "gross_usd": round(gross_usd, 4),
            "yes_shares": round(yes_shares, 4),
            "no_shares": round(no_shares, 4),
            "avg_yes": round(avg_yes, 6),
            "avg_no": round(avg_no, 6),
            "pairs": round(pairs, 4),
            "pair_cost_per_share": round(pair_cost_per_share, 6),
            "edge_per_share": round(edge_per_share, 6),
            "estimated_merge_pnl": round(estimated_merge_pnl, 4),
            "merged_pairs_est_shares": round(merged_pairs_est_shares, 6),
            "merged_pair_pnl_est_usd": round(merged_pair_pnl_est_usd, 6),
            "unpaired_cost_usd": round(unpaired_cost_usd, 6),
            "pnl_floor_usd": round(pnl_floor_usd, 6),
            "invested_usd": round(invested_usd, 6),
            "pnl_floor_pct": round(pnl_floor_pct, 4),
            "merge_coverage_pct": round(merge_coverage_pct, 2),
            "unpaired_yes_shares": round(unpaired_yes_shares, 6),
            "unpaired_no_shares": round(unpaired_no_shares, 6),
            "unpaired_shares": round(unpaired_shares, 6),
            "unpaired_ratio_pct": round(unpaired_ratio_pct, 2),
            "window_quality": window_quality,
            "first_ts": first_ts,
            "last_ts": last_ts,
            "duration_sec": duration_sec,
            "late_action": late_action,
            "last_action": last_action,
            "first_merge_ts": first_merge_ts,
            "seconds_until_first_merge": seconds_until_first_merge if seconds_until_first_merge is not None else "",
            "buys_before_first_merge": buys_before_first_merge,
            "buys_after_first_merge": buys_after_first_merge,
            "merges_after_first": merges_after_first,
            "had_buy_after_merge": had_buy_after_merge,
            "max_merge_size": round(max_merge_size, 6),
            "market_start_iso": market_start_iso,
            "market_end_iso": market_end_iso,
            "window_et_label": window_et_label,
            "window_local_label": window_local_label,
            "first_sec_from_start": first_sec_from_start if first_sec_from_start is not None else "",
            "last_sec_from_start": last_sec_from_start if last_sec_from_start is not None else "",
            "last_buy_ts": last_buy_ts,
            "last_buy_after_end_sec": last_buy_after_end_sec if last_buy_after_end_sec is not None else "",
            "cycle_count": cycle_count,
            "max_cycle_buy_count": max_cycle_buy_count,
            "avg_cycle_buy_count": avg_cycle_buy_count,
            "max_cycle_merge_size": round(max_cycle_merge_size, 6),
            "before_window_events": before_window_events,
            "after_window_events": after_window_events,
            "stats_grace_before_start_sec": grace_before_start_sec,
            "stats_grace_after_end_sec": grace_after_end_sec,
        })

    out.sort(key=lambda x: (-x["event_count"], -x["merge_count"], x["window_key"]))
    return out

def timeline(rows: list[dict], settings) -> list[dict]:
    filtered = [r for r in rows if is_target_market(r, settings)]
    filtered.sort(key=lambda x: (x.get("timestamp", ""), x.get("external_id", "")))
    out = []
    cycle_no_by_window = defaultdict(int)
    seen_merge_in_window = defaultdict(bool)

    for r in filtered:
        key = make_window_key(r)
        action = r.get("action_class", "OTHER")

        if cycle_no_by_window[key] == 0:
            cycle_no_by_window[key] = 1

        out.append({
            "timestamp": r.get("timestamp", ""),
            "window_key": key,
            "cycle_no": cycle_no_by_window[key],
            "action_class": action,
            "outcome": r.get("outcome", ""),
            "price": r.get("price", 0.0),
            "size": r.get("size", 0.0),
            "amount_usd": r.get("amount_usd", 0.0),
            "secs_to_end": seconds_to_window_end(r.get("timestamp", ""), key),
            "secs_after_end": seconds_after_window_end(r.get("timestamp", ""), key),
            "market_slug": r.get("market_slug", ""),
            "title": r.get("title", ""),
        })

        if action == "MERGE":
            seen_merge_in_window[key] = True
            cycle_no_by_window[key] += 1

    return out
