import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class BackfillCheckpoint:
    wallet: str
    next_offset: int = 0
    oldest_ts_seen: str = ""
    last_batch_size: int = 0
    completed: bool = False
    updated_at: str = ""


class BackfillCheckpointStore:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self, wallet: str) -> BackfillCheckpoint | None:
        if not self.path.exists():
            return None

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return None

        if str(raw.get("wallet", "")).lower() != wallet.lower():
            return None

        return BackfillCheckpoint(
            wallet=wallet,
            next_offset=int(raw.get("next_offset", 0) or 0),
            oldest_ts_seen=str(raw.get("oldest_ts_seen", "") or ""),
            last_batch_size=int(raw.get("last_batch_size", 0) or 0),
            completed=bool(raw.get("completed", False)),
            updated_at=str(raw.get("updated_at", "") or ""),
        )

    def save(self, checkpoint: BackfillCheckpoint) -> None:
        payload = {
            "wallet": checkpoint.wallet,
            "next_offset": int(checkpoint.next_offset),
            "oldest_ts_seen": checkpoint.oldest_ts_seen,
            "last_batch_size": int(checkpoint.last_batch_size),
            "completed": bool(checkpoint.completed),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()
