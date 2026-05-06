import csv
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def _to_float(v) -> float:
    try:
        if v in (None, ""):
            return 0.0
        return float(v)
    except Exception:
        return 0.0


def _parse_iso(v: str):
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except Exception:
        return None


def write_closed_positions_report(db, out_dir: str = "data/positions_reports", limit: int = 5000) -> tuple[Path, Path]:
    rows = db.fetch_latest_closed_positions(limit=limit)
    open_rows = db.fetch_latest_open_positions(limit=limit)
    rows = sorted(
        rows,
        key=lambda r: (
            _parse_iso(r.get("snapshot_time", "")) is None,
            _parse_iso(r.get("snapshot_time", "")) or datetime.min.replace(tzinfo=timezone.utc),
        ),
        reverse=True,
    )

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    csv_path = out_path / "closed_positions_latest.csv"
    open_csv_path = out_path / "open_positions_latest.csv"
    summary_path = out_path / "closed_positions_summary.txt"

    if rows:
        keys = [
            "snapshot_time",
            "market_slug",
            "event_slug",
            "title",
            "outcome",
            "size",
            "avg_price",
            "cur_price",
            "total_bought",
            "realized_pnl",
            "external_id",
        ]
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            for r in rows:
                writer.writerow({k: r.get(k, "") for k in keys})
    else:
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "snapshot_time",
                    "market_slug",
                    "event_slug",
                    "title",
                    "outcome",
                    "size",
                    "avg_price",
                    "cur_price",
                    "total_bought",
                    "realized_pnl",
                    "external_id",
                ]
            )

    keys = [
        "snapshot_time",
        "market_slug",
        "event_slug",
        "title",
        "outcome",
        "size",
        "avg_price",
        "cur_price",
        "total_bought",
        "realized_pnl",
        "external_id",
    ]
    if open_rows:
        with open_csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            for r in open_rows:
                writer.writerow({k: r.get(k, "") for k in keys})
    else:
        with open_csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(keys)

    total_bought = sum(_to_float(r.get("total_bought")) for r in rows)
    total_realized_pnl = sum(_to_float(r.get("realized_pnl")) for r in rows)
    total_closed = len(rows)
    wins = sum(1 for r in rows if _to_float(r.get("realized_pnl")) > 0)
    losses = sum(1 for r in rows if _to_float(r.get("realized_pnl")) < 0)
    flats = total_closed - wins - losses
    win_rate = (wins / total_closed * 100.0) if total_closed > 0 else 0.0
    roi_pct = (total_realized_pnl / total_bought * 100.0) if total_bought > 0 else 0.0
    open_total = len(open_rows)
    open_size = sum(_to_float(r.get("size")) for r in open_rows)
    open_cost_basis = sum(_to_float(r.get("total_bought")) for r in open_rows)

    by_slug = defaultdict(lambda: {"count": 0, "bought": 0.0, "pnl": 0.0})
    for r in rows:
        slug = (r.get("market_slug") or r.get("event_slug") or "").strip()
        if not slug:
            slug = "N/A"
        by_slug[slug]["count"] += 1
        by_slug[slug]["bought"] += _to_float(r.get("total_bought"))
        by_slug[slug]["pnl"] += _to_float(r.get("realized_pnl"))

    top_winners = sorted(rows, key=lambda x: _to_float(x.get("realized_pnl")), reverse=True)[:15]
    top_losers = sorted(rows, key=lambda x: _to_float(x.get("realized_pnl")))[:15]

    lines = []
    lines.append("CLOSED POSITIONS SUMMARY")
    lines.append("=" * 100)
    lines.append(f"generated_at_utc: {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"rows_total: {total_closed}")
    lines.append(f"open_rows_total: {open_total}")
    lines.append(f"open_total_size: {round(open_size, 6)}")
    lines.append(f"open_cost_basis_usd: {round(open_cost_basis, 6)}")
    lines.append(f"wins: {wins}")
    lines.append(f"losses: {losses}")
    lines.append(f"flats: {flats}")
    lines.append(f"win_rate_pct: {round(win_rate, 2)}")
    lines.append(f"total_bought_usd: {round(total_bought, 6)}")
    lines.append(f"total_realized_pnl_usd: {round(total_realized_pnl, 6)}")
    lines.append(f"roi_pct: {round(roi_pct, 4)}")
    lines.append("csv_file: closed_positions_latest.csv")
    lines.append("open_csv_file: open_positions_latest.csv")
    lines.append("")

    lines.append("LATEST CLOSED POSITIONS (DATE DESC)")
    lines.append("-" * 100)
    if rows:
        for r in rows[:100]:
            lines.append(
                f"{r.get('snapshot_time', '')} | "
                f"pnl={round(_to_float(r.get('realized_pnl')), 6):<12} | "
                f"bought={round(_to_float(r.get('total_bought')), 6):<12} | "
                f"{r.get('market_slug') or r.get('event_slug') or ''} | {r.get('outcome', '')}"
            )
    else:
        lines.append("No closed positions")
    lines.append("")

    lines.append("BY SLUG (TOP 30 BY ABS PNL)")
    lines.append("-" * 100)
    for slug, agg in sorted(by_slug.items(), key=lambda x: abs(x[1]["pnl"]), reverse=True)[:30]:
        bought = agg["bought"]
        pnl = agg["pnl"]
        slug_roi = (pnl / bought * 100.0) if bought > 0 else 0.0
        lines.append(
            f"{slug} | count={agg['count']:<4} | bought={round(bought, 6):<12} | "
            f"pnl={round(pnl, 6):<12} | roi={round(slug_roi, 4)}%"
        )
    lines.append("")

    lines.append("TOP WINNERS")
    lines.append("-" * 100)
    if top_winners:
        for r in top_winners:
            lines.append(
                f"{r.get('snapshot_time', '')} | pnl={round(_to_float(r.get('realized_pnl')), 6):<12} | "
                f"bought={round(_to_float(r.get('total_bought')), 6):<12} | "
                f"{r.get('market_slug') or r.get('event_slug') or ''} | {r.get('outcome', '')}"
            )
    else:
        lines.append("No closed positions")
    lines.append("")

    lines.append("TOP LOSERS")
    lines.append("-" * 100)
    if top_losers:
        for r in top_losers:
            lines.append(
                f"{r.get('snapshot_time', '')} | pnl={round(_to_float(r.get('realized_pnl')), 6):<12} | "
                f"bought={round(_to_float(r.get('total_bought')), 6):<12} | "
                f"{r.get('market_slug') or r.get('event_slug') or ''} | {r.get('outcome', '')}"
            )
    else:
        lines.append("No closed positions")

    with summary_path.open("w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return summary_path, csv_path
