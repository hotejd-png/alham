import json
import re
from pathlib import Path
from datetime import datetime, timezone, timedelta


def _pick_rotated_file(raw_path: Path, name: str, max_file_bytes: int) -> Path:
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    pattern = re.compile(rf"^{re.escape(name)}\.{day}\.part(\d+)\.jsonl$")

    parts = []
    for f in raw_path.glob(f"{name}.{day}.part*.jsonl"):
        m = pattern.match(f.name)
        if m:
            parts.append((int(m.group(1)), f))

    if not parts:
        return raw_path / f"{name}.{day}.part1.jsonl"

    parts.sort(key=lambda x: x[0])
    last_idx, last_file = parts[-1]
    if max_file_bytes > 0 and last_file.exists() and last_file.stat().st_size >= max_file_bytes:
        return raw_path / f"{name}.{day}.part{last_idx + 1}.jsonl"
    return last_file


def _cleanup_raw_files(raw_path: Path, retention_days: int, max_total_bytes: int) -> None:
    files = [f for f in raw_path.glob("*.jsonl") if f.is_file()]
    if not files:
        return

    # 1) Age-based cleanup
    if retention_days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        for f in files:
            mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                try:
                    f.unlink()
                except Exception:
                    pass

    # 2) Total-size cap cleanup (oldest first)
    if max_total_bytes > 0:
        files = [f for f in raw_path.glob("*.jsonl") if f.is_file()]
        files.sort(key=lambda x: x.stat().st_mtime)
        total = sum(f.stat().st_size for f in files)
        while files and total > max_total_bytes:
            victim = files.pop(0)
            try:
                size = victim.stat().st_size
                victim.unlink()
                total -= size
            except Exception:
                pass


def append_jsonl(
    raw_dir: str,
    name: str,
    payload,
    retention_days: int = 3,
    max_file_mb: int = 512,
    max_total_gb: int = 20,
) -> None:
    p = Path(raw_dir)
    p.mkdir(parents=True, exist_ok=True)
    max_file_bytes = max(0, int(max_file_mb)) * 1024 * 1024
    max_total_bytes = max(0, int(max_total_gb)) * 1024 * 1024 * 1024

    out = _pick_rotated_file(p, name, max_file_bytes)
    row = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    }
    with out.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

    _cleanup_raw_files(p, retention_days=retention_days, max_total_bytes=max_total_bytes)
