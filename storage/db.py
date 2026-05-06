import json
import sqlite3
from pathlib import Path


class Database:
    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row

    def init(self):
        cur = self.conn.cursor()
        cur.executescript(
            '''
            CREATE TABLE IF NOT EXISTS activity (
                external_id TEXT PRIMARY KEY,
                timestamp TEXT,
                activity_type TEXT,
                action_class TEXT,
                market_slug TEXT,
                event_slug TEXT,
                title TEXT,
                outcome TEXT,
                side TEXT,
                price REAL,
                size REAL,
                amount_usd REAL,
                asset_id TEXT,
                raw_json TEXT
            );

            CREATE TABLE IF NOT EXISTS trades (
                external_id TEXT PRIMARY KEY,
                timestamp TEXT,
                market_slug TEXT,
                event_slug TEXT,
                title TEXT,
                outcome TEXT,
                side TEXT,
                price REAL,
                size REAL,
                amount_usd REAL,
                asset_id TEXT,
                raw_json TEXT
            );

            CREATE TABLE IF NOT EXISTS positions (
                external_id TEXT,
                snapshot_time TEXT,
                closed INTEGER,
                market_slug TEXT,
                event_slug TEXT,
                title TEXT,
                outcome TEXT,
                side TEXT,
                size REAL,
                avg_price REAL,
                cur_price REAL,
                total_bought REAL,
                realized_pnl REAL,
                raw_json TEXT,
                PRIMARY KEY (external_id, snapshot_time, closed)
            );

            CREATE INDEX IF NOT EXISTS idx_activity_timestamp ON activity(timestamp);
            CREATE INDEX IF NOT EXISTS idx_activity_market ON activity(market_slug, event_slug);
            CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp);
            CREATE INDEX IF NOT EXISTS idx_positions_snapshot ON positions(snapshot_time);
            '''
        )
        self._ensure_positions_columns()
        self.conn.commit()

    def _ensure_positions_columns(self):
        cur = self.conn.execute("PRAGMA table_info(positions)")
        cols = {row["name"] for row in cur.fetchall()}
        if "total_bought" not in cols:
            self.conn.execute("ALTER TABLE positions ADD COLUMN total_bought REAL")

    def insert_activity(self, rows: list[dict]) -> tuple[int, list[dict]]:
        cur = self.conn.cursor()
        total = 0
        new_rows = []

        for r in rows:
            cur.execute(
                '''
                INSERT OR IGNORE INTO activity
                (external_id, timestamp, activity_type, action_class, market_slug, event_slug, title, outcome, side, price, size, amount_usd, asset_id, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    r["external_id"],
                    r["timestamp"],
                    r["activity_type"],
                    r["action_class"],
                    r["market_slug"],
                    r["event_slug"],
                    r["title"],
                    r["outcome"],
                    r["side"],
                    r["price"],
                    r["size"],
                    r["amount_usd"],
                    r["asset_id"],
                    json.dumps(r["raw_json"], ensure_ascii=False),
                )
            )
            if cur.rowcount > 0:
                total += 1
                new_rows.append(r)

        self.conn.commit()
        return total, new_rows

    def insert_trades(self, rows: list[dict]) -> int:
        cur = self.conn.cursor()
        total = 0
        for r in rows:
            cur.execute(
                '''
                INSERT OR IGNORE INTO trades
                (external_id, timestamp, market_slug, event_slug, title, outcome, side, price, size, amount_usd, asset_id, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    r["external_id"],
                    r["timestamp"],
                    r["market_slug"],
                    r["event_slug"],
                    r["title"],
                    r["outcome"],
                    r["side"],
                    r["price"],
                    r["size"],
                    r["amount_usd"],
                    r["asset_id"],
                    json.dumps(r["raw_json"], ensure_ascii=False),
                )
            )
            total += cur.rowcount
        self.conn.commit()
        return total

    def insert_positions(self, rows: list[dict]) -> int:
        cur = self.conn.cursor()
        total = 0
        for r in rows:
            cur.execute(
                '''
                INSERT OR IGNORE INTO positions
                (external_id, snapshot_time, closed, market_slug, event_slug, title, outcome, side, size, avg_price, cur_price, total_bought, realized_pnl, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    r["external_id"],
                    r["snapshot_time"],
                    r["closed"],
                    r["market_slug"],
                    r["event_slug"],
                    r["title"],
                    r["outcome"],
                    r["side"],
                    r["size"],
                    r["avg_price"],
                    r["cur_price"],
                    r.get("total_bought", 0.0),
                    r["realized_pnl"],
                    json.dumps(r["raw_json"], ensure_ascii=False),
                )
            )
            total += cur.rowcount
        self.conn.commit()
        return total

    def fetch_positions(self, closed: int | None = None, limit: int = 1000) -> list[dict]:
        if closed is None:
            query = '''
                SELECT external_id, snapshot_time, closed, market_slug, event_slug, title, outcome, side,
                       size, avg_price, cur_price, total_bought, realized_pnl
                FROM positions
                ORDER BY snapshot_time DESC
                LIMIT ?
            '''
            cur = self.conn.execute(query, (limit,))
            return [dict(r) for r in cur.fetchall()]

        query = '''
            SELECT external_id, snapshot_time, closed, market_slug, event_slug, title, outcome, side,
                   size, avg_price, cur_price, total_bought, realized_pnl
            FROM positions
            WHERE closed = ?
            ORDER BY snapshot_time DESC
            LIMIT ?
        '''
        cur = self.conn.execute(query, (closed, limit))
        return [dict(r) for r in cur.fetchall()]

    def fetch_latest_closed_positions(self, limit: int = 5000) -> list[dict]:
        query = '''
            WITH ranked AS (
                SELECT
                    external_id, snapshot_time, closed, market_slug, event_slug, title, outcome, side,
                    size, avg_price, cur_price, total_bought, realized_pnl,
                    ROW_NUMBER() OVER (
                        PARTITION BY external_id
                        ORDER BY snapshot_time DESC
                    ) as rn
                FROM positions
                WHERE closed = 1
            )
            SELECT
                external_id, snapshot_time, closed, market_slug, event_slug, title, outcome, side,
                size, avg_price, cur_price, total_bought, realized_pnl
            FROM ranked
            WHERE rn = 1
            ORDER BY snapshot_time DESC
            LIMIT ?
        '''
        cur = self.conn.execute(query, (limit,))
        return [dict(r) for r in cur.fetchall()]

    def fetch_latest_open_positions(self, limit: int = 5000) -> list[dict]:
        query = '''
            WITH ranked AS (
                SELECT
                    external_id, snapshot_time, closed, market_slug, event_slug, title, outcome, side,
                    size, avg_price, cur_price, total_bought, realized_pnl,
                    ROW_NUMBER() OVER (
                        PARTITION BY external_id
                        ORDER BY snapshot_time DESC
                    ) as rn
                FROM positions
                WHERE closed = 0
            )
            SELECT
                external_id, snapshot_time, closed, market_slug, event_slug, title, outcome, side,
                size, avg_price, cur_price, total_bought, realized_pnl
            FROM ranked
            WHERE rn = 1
            ORDER BY snapshot_time DESC
            LIMIT ?
        '''
        cur = self.conn.execute(query, (limit,))
        return [dict(r) for r in cur.fetchall()]

    def fetch_closed_positions_first_seen(self) -> list[dict]:
        query = '''
            SELECT
                TRIM(COALESCE(market_slug, '')) as market_slug,
                TRIM(COALESCE(event_slug, '')) as event_slug,
                MIN(snapshot_time) as first_closed_ts
            FROM positions
            WHERE closed = 1
            GROUP BY TRIM(COALESCE(market_slug, '')), TRIM(COALESCE(event_slug, ''))
            HAVING first_closed_ts IS NOT NULL AND first_closed_ts != ''
        '''
        cur = self.conn.execute(query)
        return [dict(r) for r in cur.fetchall()]

    def fetch_latest_closed_positions_for_slugs(
        self,
        market_slug: str,
        event_slug: str,
        limit: int = 200,
    ) -> list[dict]:
        query = '''
            WITH ranked AS (
                SELECT
                    external_id, snapshot_time, closed, market_slug, event_slug, title, outcome, side,
                    size, avg_price, cur_price, total_bought, realized_pnl,
                    ROW_NUMBER() OVER (
                        PARTITION BY external_id
                        ORDER BY snapshot_time DESC
                    ) as rn
                FROM positions
                WHERE closed = 1
                  AND (
                    market_slug = ? OR event_slug = ?
                    OR market_slug = ? OR event_slug = ?
                  )
            )
            SELECT
                external_id, snapshot_time, closed, market_slug, event_slug, title, outcome, side,
                size, avg_price, cur_price, total_bought, realized_pnl
            FROM ranked
            WHERE rn = 1
            ORDER BY snapshot_time DESC
            LIMIT ?
        '''
        cur = self.conn.execute(
            query,
            (market_slug, market_slug, event_slug, event_slug, limit),
        )
        return [dict(r) for r in cur.fetchall()]

    def fetch_recent_activity(self, limit: int = 50000) -> list[dict]:
        cur = self.conn.execute(
            '''
            SELECT external_id, timestamp, activity_type, action_class, market_slug, event_slug, title, outcome, side, price, size, amount_usd, asset_id
            FROM activity
            ORDER BY timestamp ASC
            LIMIT ?
            ''',
            (limit,)
        )
        return [dict(r) for r in cur.fetchall()]

    def fetch_all_activity(self) -> list[dict]:
        cur = self.conn.execute(
            '''
            SELECT external_id, timestamp, activity_type, action_class, market_slug, event_slug, title, outcome, side, price, size, amount_usd, asset_id
            FROM activity
            ORDER BY timestamp ASC
            '''
        )
        return [dict(r) for r in cur.fetchall()]

    def fetch_activity_by_day(self) -> list[dict]:
        cur = self.conn.execute(
            '''
            SELECT
                substr(timestamp, 1, 10) as day,
                COUNT(*) as event_count,
                SUM(CASE WHEN action_class='BUY' THEN 1 ELSE 0 END) as buy_count,
                SUM(CASE WHEN action_class='SELL' THEN 1 ELSE 0 END) as sell_count,
                SUM(CASE WHEN action_class='MERGE' THEN 1 ELSE 0 END) as merge_count,
                ROUND(SUM(amount_usd), 6) as total_usd,
                ROUND(SUM(size), 6) as total_shares
            FROM activity
            GROUP BY substr(timestamp, 1, 10)
            ORDER BY day ASC
            '''
        )
        return [dict(r) for r in cur.fetchall()]

    def fetch_activity_for_window(self, window_key: str) -> list[dict]:
        """
        window_key формат:
        btc-updown-5m-1774110900 | btc-updown-5m-1774110900

        Мы фильтруем по market_slug/event_slug.
        """
        left = window_key.split("|")[0].strip()
        right = window_key.split("|")[1].strip() if "|" in window_key else left

        cur = self.conn.execute(
            '''
            SELECT external_id, timestamp, activity_type, action_class, market_slug, event_slug, title, outcome, side, price, size, amount_usd, asset_id
            FROM activity
            WHERE market_slug = ? OR event_slug = ? OR market_slug = ? OR event_slug = ?
            ORDER BY timestamp ASC
            ''',
            (left, left, right, right)
        )
        return [dict(r) for r in cur.fetchall()]

    def fetch_distinct_windows(self) -> list[str]:
        cur = self.conn.execute(
            '''
            SELECT DISTINCT
                TRIM(COALESCE(market_slug, '')) as market_slug,
                TRIM(COALESCE(event_slug, '')) as event_slug
            FROM activity
            WHERE COALESCE(market_slug, '') != ''
            ORDER BY market_slug ASC, event_slug ASC
            '''
        )

        out = []
        for r in cur.fetchall():
            market_slug = r["market_slug"]
            event_slug = r["event_slug"] or market_slug
            out.append(f"{market_slug} | {event_slug}")
        return out
