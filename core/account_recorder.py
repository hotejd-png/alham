import csv
import json
from pathlib import Path


class AccountRecorder:
    def __init__(self, base_dir: str = "data/account_logs"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _wallet_dir(self, wallet: str) -> Path:
        safe = wallet.lower().replace(":", "_")
        path = self.base_dir / safe
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _jsonl_path(self, wallet: str) -> Path:
        return self._wallet_dir(wallet) / "activity_all.jsonl"

    def _csv_path(self, wallet: str) -> Path:
        return self._wallet_dir(wallet) / "activity_all.csv"

    def record_rows(self, wallet: str, rows: list[dict]) -> int:
        if not rows:
            return 0

        jsonl_path = self._jsonl_path(wallet)
        csv_path = self._csv_path(wallet)

        with jsonl_path.open("a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

        csv_rows = []
        for r in rows:
            csv_rows.append(
                {
                    "wallet": wallet,
                    "external_id": r.get("external_id", ""),
                    "timestamp": r.get("timestamp", ""),
                    "activity_type": r.get("activity_type", ""),
                    "action_class": r.get("action_class", ""),
                    "market_slug": r.get("market_slug", ""),
                    "event_slug": r.get("event_slug", ""),
                    "title": r.get("title", ""),
                    "outcome": r.get("outcome", ""),
                    "side": r.get("side", ""),
                    "price": r.get("price", 0),
                    "size": r.get("size", 0),
                    "amount_usd": r.get("amount_usd", 0),
                    "asset_id": r.get("asset_id", ""),
                }
            )

        file_exists = csv_path.exists()
        with csv_path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()))
            if not file_exists:
                writer.writeheader()
            writer.writerows(csv_rows)

        return len(rows)

