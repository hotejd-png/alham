import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from core.utils import safe_float


@dataclass
class ReplayConfig:
    pair_cost_max: float = 0.985
    pair_cost_min: float = 0.90
    min_events: int = 30
    min_merge_count: int = 1
    min_buy_count: int = 20
    min_merge_coverage_pct: float = 40.0
    only_whale_like: bool = True
    window_keyword: str = "5m"


def _wallet_dir_name(wallet: str) -> str:
    return (wallet or "").strip().lower().replace(":", "_")


def _iter_event_csv_files(base_dir: Path) -> Iterable[Path]:
    if not base_dir.exists():
        return []
    for d in sorted(base_dir.iterdir()):
        if not d.is_dir():
            continue
        p = d / "events.csv"
        if p.exists():
            yield p


def _read_events(csv_path: Path) -> list[dict]:
    rows = []
    with csv_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)
    rows.sort(key=lambda x: (x.get("timestamp", ""), x.get("external_id", "")))
    return rows


def _is_up(outcome: str) -> bool:
    t = (outcome or "").strip().lower()
    return ("up" in t) or ("yes" in t)


def _is_down(outcome: str) -> bool:
    t = (outcome or "").strip().lower()
    return ("down" in t) or ("no" in t)


def _window_key_from_path(csv_path: Path) -> str:
    # Folder format: "<start_ts>__<market_slug>__<event_slug>".
    parts = csv_path.parent.name.split("__")
    if len(parts) >= 3:
        return f"{parts[1]} | {parts[2]}"
    return csv_path.parent.name


def _window_matches(cfg: ReplayConfig, window_key: str) -> bool:
    k = (cfg.window_keyword or "").strip().lower()
    if not k:
        return True
    return f"-{k}-" in window_key.lower()


def _analyze_window(rows: list[dict], window_key: str, cfg: ReplayConfig) -> dict:
    buy_up_shares = 0.0
    buy_down_shares = 0.0
    buy_up_usd = 0.0
    buy_down_usd = 0.0
    buy_up_vwap_num = 0.0
    buy_down_vwap_num = 0.0

    buy_count = 0
    merge_count = 0
    merge_size = 0.0
    sell_usd = 0.0
    redeem_usd = 0.0
    total_buy_usd = 0.0
    event_count = len(rows)

    for r in rows:
        action = (r.get("action_class") or "").upper()
        outcome = r.get("outcome", "")
        size = safe_float(r.get("size"))
        price = safe_float(r.get("price"))
        usd = safe_float(r.get("amount_usd"))

        if action == "BUY":
            buy_count += 1
            total_buy_usd += usd
            if _is_up(outcome):
                buy_up_shares += size
                buy_up_usd += usd
                buy_up_vwap_num += size * price
            elif _is_down(outcome):
                buy_down_shares += size
                buy_down_usd += usd
                buy_down_vwap_num += size * price
        elif action == "MERGE":
            merge_count += 1
            merge_size += size
        elif action == "SELL":
            sell_usd += usd
        elif action == "REDEEM":
            redeem_usd += usd

    paired_shares = min(buy_up_shares, buy_down_shares)
    unpaired_up = max(0.0, buy_up_shares - paired_shares)
    unpaired_down = max(0.0, buy_down_shares - paired_shares)

    avg_up = (buy_up_vwap_num / buy_up_shares) if buy_up_shares > 0 else 0.0
    avg_down = (buy_down_vwap_num / buy_down_shares) if buy_down_shares > 0 else 0.0
    pair_cost = (avg_up + avg_down) if paired_shares > 0 else 0.0

    merged_pairs = min(merge_size, paired_shares)
    merge_coverage_pct = (merged_pairs / paired_shares * 100.0) if paired_shares > 0 else 0.0
    edge_per_pair = 1.0 - pair_cost if paired_shares > 0 else 0.0
    merge_pnl = merged_pairs * edge_per_pair

    # Tail risk: worst-case value of unpaired inventory at cost.
    unpaired_cost_usd = (unpaired_up * avg_up) + (unpaired_down * avg_down)
    conservative_result_usd = merge_pnl - unpaired_cost_usd
    actual_cash_result_usd = (merge_size + sell_usd + redeem_usd) - total_buy_usd

    whale_like = (
        event_count >= cfg.min_events
        and buy_count >= cfg.min_buy_count
        and merge_count >= cfg.min_merge_count
        and pair_cost >= cfg.pair_cost_min
        and pair_cost <= cfg.pair_cost_max
        and merge_coverage_pct >= cfg.min_merge_coverage_pct
    )

    return {
        "window_key": window_key,
        "events": event_count,
        "buy_count": buy_count,
        "merge_count": merge_count,
        "pair_cost": round(pair_cost, 6),
        "edge_per_pair": round(edge_per_pair, 6),
        "paired_shares": round(paired_shares, 6),
        "merged_pairs": round(merged_pairs, 6),
        "merge_coverage_pct": round(merge_coverage_pct, 2),
        "merge_pnl_usd": round(merge_pnl, 6),
        "unpaired_up_shares": round(unpaired_up, 6),
        "unpaired_down_shares": round(unpaired_down, 6),
        "unpaired_cost_usd": round(unpaired_cost_usd, 6),
        "conservative_result_usd": round(conservative_result_usd, 6),
        "actual_cash_result_usd": round(actual_cash_result_usd, 6),
        "whale_like": whale_like,
    }


def run_whale_like_replay(
    wallet: str,
    day_kyiv: str,
    base_multi_wallet_dir: str = "data/multi_wallet",
    out_dir: str = "data/analysis",
    cfg: ReplayConfig | None = None,
) -> tuple[str, str]:
    cfg = cfg or ReplayConfig()

    wallet_dir = Path(base_multi_wallet_dir) / _wallet_dir_name(wallet)
    day_dir = wallet_dir / "window_logs" / day_kyiv
    out_base = Path(out_dir)
    out_base.mkdir(parents=True, exist_ok=True)

    txt_path = out_base / f"whale_replay_{_wallet_dir_name(wallet)}_{day_kyiv}.txt"
    csv_path = out_base / f"whale_replay_{_wallet_dir_name(wallet)}_{day_kyiv}.csv"

    windows = []
    for csv_file in _iter_event_csv_files(day_dir):
        rows = _read_events(csv_file)
        if not rows:
            continue
        window_key = _window_key_from_path(csv_file)
        if not _window_matches(cfg, window_key):
            continue
        w = _analyze_window(rows, window_key, cfg)
        if cfg.only_whale_like and not w["whale_like"]:
            continue
        windows.append(w)

    windows.sort(key=lambda x: x["window_key"])

    total_pairs = sum(float(w["paired_shares"]) for w in windows)
    total_merged = sum(float(w["merged_pairs"]) for w in windows)
    total_merge_pnl = sum(float(w["merge_pnl_usd"]) for w in windows)
    total_unpaired_risk = sum(float(w["unpaired_cost_usd"]) for w in windows)
    total_conservative = sum(float(w["conservative_result_usd"]) for w in windows)
    total_actual_cash = sum(float(w["actual_cash_result_usd"]) for w in windows)

    summary_lines = [
        "WHALE_LIKE REPLAY",
        "=" * 100,
        f"wallet: {wallet}",
        f"day_kyiv: {day_kyiv}",
        f"source_dir: {day_dir}",
        f"windows_included: {len(windows)}",
        "",
        "TOTALS",
        "-" * 100,
        f"pairs_collected_shares: {round(total_pairs, 6)}",
        f"pairs_merged_shares: {round(total_merged, 6)}",
        f"merge_pnl_usd: {round(total_merge_pnl, 6)}",
        f"tail_risk_unpaired_usd: {round(total_unpaired_risk, 6)}",
        f"final_conservative_usd: {round(total_conservative, 6)}",
        f"final_actual_cash_usd: {round(total_actual_cash, 6)}",
        "",
        "PARAMS",
        "-" * 100,
        f"pair_cost_range: [{cfg.pair_cost_min}, {cfg.pair_cost_max}]",
        f"min_events: {cfg.min_events}",
        f"min_buy_count: {cfg.min_buy_count}",
        f"min_merge_count: {cfg.min_merge_count}",
        f"min_merge_coverage_pct: {cfg.min_merge_coverage_pct}",
        f"only_whale_like: {cfg.only_whale_like}",
        "",
        "WINDOWS",
        "-" * 100,
    ]

    for w in windows:
        summary_lines.append(
            f"{w['window_key']} | pair_cost={w['pair_cost']} | paired={w['paired_shares']} | "
            f"merged={w['merged_pairs']} | merge_pnl={w['merge_pnl_usd']} | "
            f"unpaired_risk={w['unpaired_cost_usd']} | final_cons={w['conservative_result_usd']}"
        )

    txt_path.write_text("\n".join(summary_lines), encoding="utf-8")

    if windows:
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(windows[0].keys()))
            w.writeheader()
            w.writerows(windows)
    else:
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(
                [
                    "window_key",
                    "events",
                    "buy_count",
                    "merge_count",
                    "pair_cost",
                    "edge_per_pair",
                    "paired_shares",
                    "merged_pairs",
                    "merge_coverage_pct",
                    "merge_pnl_usd",
                    "unpaired_up_shares",
                    "unpaired_down_shares",
                    "unpaired_cost_usd",
                    "conservative_result_usd",
                    "actual_cash_result_usd",
                    "whale_like",
                ]
            )

    return str(txt_path), str(csv_path)
