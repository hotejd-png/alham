from collections import defaultdict
from core.filters import is_target_market, make_window_key

def _weighted_avg(cost: float, shares: float) -> float:
    if shares <= 0:
        return 0.0
    return cost / shares

def analyze_merges(rows, settings):
    """
    Правильная логика:
    - BUY добавляют инвентарь
    - MERGE сжигает только часть пар
    - остаток inventory остается жить дальше
    """
    windows = defaultdict(list)

    for r in rows:
        if not is_target_market(r, settings):
            continue
        key = make_window_key(r)
        windows[key].append(r)

    results = []

    for key, events in windows.items():
        events.sort(key=lambda x: (x.get("timestamp", ""), x.get("external_id", "")))

        yes_shares = 0.0
        no_shares = 0.0
        yes_cost = 0.0
        no_cost = 0.0

        for e in events:
            action = e.get("action_class")
            outcome = str(e.get("outcome", "")).lower()
            price = float(e.get("price", 0) or 0)
            size = float(e.get("size", 0) or 0)

            if action == "BUY":
                if "yes" in outcome or "up" in outcome:
                    yes_shares += size
                    yes_cost += size * price
                elif "no" in outcome or "down" in outcome:
                    no_shares += size
                    no_cost += size * price

            elif action == "MERGE":
                merge_size = size
                possible_pairs = min(yes_shares, no_shares)

                if possible_pairs <= 0:
                    results.append({
                        "window": key,
                        "timestamp": e["timestamp"],
                        "merge_size": round(merge_size, 6),
                        "pairs_before_merge": 0.0,
                        "pairs_used": 0.0,
                        "avg_yes": 0.0,
                        "avg_no": 0.0,
                        "edge": 0.0,
                        "profit": 0.0,
                        "yes_left": round(yes_shares, 6),
                        "no_left": round(no_shares, 6),
                    })
                    continue

                pairs_used = min(merge_size, possible_pairs)

                avg_yes = _weighted_avg(yes_cost, yes_shares)
                avg_no = _weighted_avg(no_cost, no_shares)
                edge = 1 - (avg_yes + avg_no)
                profit = pairs_used * edge

                # списываем только использованные пары
                yes_shares -= pairs_used
                no_shares -= pairs_used

                yes_cost -= pairs_used * avg_yes
                no_cost -= pairs_used * avg_no

                # защита от микроминусов из-за float
                if yes_shares < 1e-9:
                    yes_shares = 0.0
                    yes_cost = 0.0
                if no_shares < 1e-9:
                    no_shares = 0.0
                    no_cost = 0.0

                results.append({
                    "window": key,
                    "timestamp": e["timestamp"],
                    "merge_size": round(merge_size, 6),
                    "pairs_before_merge": round(possible_pairs, 6),
                    "pairs_used": round(pairs_used, 6),
                    "avg_yes": round(avg_yes, 6),
                    "avg_no": round(avg_no, 6),
                    "edge": round(edge, 6),
                    "profit": round(profit, 6),
                    "yes_left": round(yes_shares, 6),
                    "no_left": round(no_shares, 6),
                })

    results.sort(key=lambda x: (x["timestamp"], x["window"]))
    return results