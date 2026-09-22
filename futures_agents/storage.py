"""SQLite persistence: the journal, the news-reaction database and strategy stats.

Everything the system learns lives here. SQLite is chosen deliberately over a
pile of JSON files: the learning loop asks conditional questions ("expectancy
for this strategy on this symbol in this regime during this session"), and
those are queries, not file reads.

Schema notes:

* The journal stores both taken trades **and** NO TRADE decisions. A system
  that only journals what it traded cannot learn what it correctly avoided, or
  what it wrongly passed on.
* News reactions are stored per symbol *and* per horizon, because the useful
  question is not "was CPI bullish" but "what did MNQ do in the 1, 5, 15, 30
  and 60 minutes after a hot CPI, and how consistently".
* Every timestamp column stores an ISO-8601 Eastern Time string.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date, datetime
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from .schema import (Decision, Direction, JournalEntry, MarketRegime, NewsEvent,
                     NewsReaction, StrategyStats, VolatilityRegime, to_json)
from .timeutil import now_et, to_et, trading_day

__all__ = ["Storage", "SCHEMA"]


SCHEMA = """
CREATE TABLE IF NOT EXISTS journal (
    entry_id            TEXT PRIMARY KEY,
    date_et             TEXT NOT NULL,
    time_et             TEXT NOT NULL,
    symbol              TEXT NOT NULL,
    direction           TEXT NOT NULL,
    final_decision      TEXT NOT NULL,
    entry               REAL,
    stop                REAL,
    targets             TEXT,
    strategy            TEXT,
    strategy_group      TEXT,
    timeframes          TEXT,
    indicators          TEXT,
    confluences         TEXT,
    market_regime       TEXT,
    volatility_regime   TEXT,
    session             TEXT,
    news_environment    TEXT,
    analyst_predictions TEXT,
    confidence          REAL,
    contracts           INTEGER,
    dollar_risk         REAL,
    result              TEXT,
    exit_price          REAL,
    exit_time_et        TEXT,
    exit_reason         TEXT,
    mfe_points          REAL,
    mae_points          REAL,
    mfe_r               REAL,
    mae_r               REAL,
    profit_loss         REAL,
    realised_r          REAL,
    reward_risk_planned REAL,
    thesis_correct      INTEGER,
    what_invalidated    TEXT,
    what_happened_after TEXT,
    lessons             TEXT,
    callout_id          TEXT
);
CREATE INDEX IF NOT EXISTS idx_journal_symbol   ON journal(symbol);
CREATE INDEX IF NOT EXISTS idx_journal_strategy ON journal(strategy);
CREATE INDEX IF NOT EXISTS idx_journal_regime   ON journal(market_regime);
CREATE INDEX IF NOT EXISTS idx_journal_date     ON journal(date_et);
CREATE INDEX IF NOT EXISTS idx_journal_result   ON journal(result);

CREATE TABLE IF NOT EXISTS news_events (
    event_id        TEXT PRIMARY KEY,
    timestamp_et    TEXT NOT NULL,
    title           TEXT,
    category        TEXT,
    impact          TEXT,
    scheduled       INTEGER,
    release_time_et TEXT,
    actual          TEXT,
    forecast        TEXT,
    previous        TEXT,
    surprise_direction TEXT,
    affected_symbols   TEXT,
    source          TEXT,
    summary         TEXT
);
CREATE INDEX IF NOT EXISTS idx_news_category ON news_events(category);
CREATE INDEX IF NOT EXISTS idx_news_time     ON news_events(release_time_et);

CREATE TABLE IF NOT EXISTS news_reactions (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id           TEXT NOT NULL,
    symbol             TEXT NOT NULL,
    category           TEXT,
    surprise_direction TEXT,
    release_time_et    TEXT,
    price_at_release   REAL,
    move_1m            REAL,
    move_5m            REAL,
    move_15m           REAL,
    move_30m           REAL,
    move_60m           REAL,
    range_60m          REAL,
    atr_normalised_60m REAL,
    reverted           INTEGER,
    notes              TEXT,
    UNIQUE(event_id, symbol)
);
CREATE INDEX IF NOT EXISTS idx_reaction_lookup
    ON news_reactions(symbol, category, surprise_direction);

CREATE TABLE IF NOT EXISTS strategy_performance (
    strategy_id     TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    timeframe       INTEGER,
    regime          TEXT,
    session         TEXT,
    scope           TEXT NOT NULL DEFAULT 'backtest',
    trades          INTEGER,
    win_rate        REAL,
    profit_factor   REAL,
    expectancy_r    REAL,
    max_drawdown_r  REAL,
    sharpe          REAL,
    sortino         REAL,
    oos_trades      INTEGER,
    oos_expectancy_r REAL,
    walk_forward_efficiency REAL,
    robustness_score REAL,
    live_eligible   INTEGER,
    updated_et      TEXT,
    payload         TEXT,
    PRIMARY KEY (strategy_id, scope, regime, session)
);
CREATE INDEX IF NOT EXISTS idx_perf_symbol ON strategy_performance(symbol);
CREATE INDEX IF NOT EXISTS idx_perf_elig   ON strategy_performance(live_eligible);

CREATE TABLE IF NOT EXISTS agent_scores (
    agent_id    TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    regime      TEXT NOT NULL DEFAULT 'ALL',
    session     TEXT NOT NULL DEFAULT 'ALL',
    predictions INTEGER DEFAULT 0,
    correct     INTEGER DEFAULT 0,
    total_r     REAL DEFAULT 0,
    avg_r       REAL DEFAULT 0,
    false_signals INTEGER DEFAULT 0,
    updated_et  TEXT,
    PRIMARY KEY (agent_id, symbol, regime, session)
);

CREATE TABLE IF NOT EXISTS callouts (
    callout_id   TEXT PRIMARY KEY,
    timestamp_et TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    decision     TEXT NOT NULL,
    payload      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_callout_symbol ON callouts(symbol);

CREATE TABLE IF NOT EXISTS equity_curve (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_et TEXT NOT NULL,
    equity       REAL NOT NULL,
    peak_equity  REAL,
    daily_pnl    REAL,
    note         TEXT
);
"""


class Storage:
    """Thread-safe SQLite wrapper for every persistent store in the system."""

    def __init__(self, path: str = "data/futures_agents.sqlite3"):
        self.path = path
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ---- plumbing -----------------------------------------------------
    @contextmanager
    def _cursor(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            cur = self._conn.cursor()
            try:
                yield cur
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            finally:
                cur.close()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @staticmethod
    def _j(value: Any) -> str:
        return to_json(value) if value is not None else "null"

    # ==================================================================
    # Journal
    # ==================================================================
    def record_journal(self, entry: JournalEntry) -> str:
        with self._cursor() as cur:
            cur.execute("""
                INSERT OR REPLACE INTO journal (
                    entry_id, date_et, time_et, symbol, direction, final_decision,
                    entry, stop, targets, strategy, strategy_group, timeframes,
                    indicators, confluences, market_regime, volatility_regime,
                    session, news_environment, analyst_predictions, confidence,
                    contracts, dollar_risk, result, exit_price, exit_time_et,
                    exit_reason, mfe_points, mae_points, mfe_r, mae_r, profit_loss,
                    realised_r, reward_risk_planned, thesis_correct,
                    what_invalidated, what_happened_after, lessons, callout_id
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                entry.entry_id, entry.date_et, entry.time_et, entry.symbol,
                entry.direction.value, entry.final_decision.value, entry.entry,
                entry.stop, self._j(entry.targets), entry.strategy,
                entry.strategy_group, self._j(entry.timeframes),
                self._j(entry.indicators), self._j(entry.confluences),
                entry.market_regime.value, entry.volatility_regime.value,
                entry.session, entry.news_environment,
                self._j(entry.analyst_predictions), entry.confidence,
                entry.contracts, entry.dollar_risk, entry.result, entry.exit_price,
                entry.exit_time_et, entry.exit_reason, entry.mfe_points,
                entry.mae_points, entry.mfe_r, entry.mae_r, entry.profit_loss,
                entry.realised_r, entry.reward_risk_planned,
                None if entry.thesis_correct is None else int(entry.thesis_correct),
                entry.what_invalidated, entry.what_happened_after, entry.lessons,
                entry.callout_id,
            ))
        return entry.entry_id

    def resolve_journal(self, entry_id: str, *, result: str, exit_price: float,
                        exit_reason: str, profit_loss: float, realised_r: float,
                        mfe_r: float = 0.0, mae_r: float = 0.0,
                        mfe_points: float = 0.0, mae_points: float = 0.0,
                        thesis_correct: Optional[bool] = None,
                        what_invalidated: str = "",
                        what_happened_after: str = "",
                        lessons: str = "",
                        exit_time_et: Optional[str] = None) -> bool:
        """Fill in a journal row's outcome.

        Every outcome column the table defines is writable here. An earlier
        version omitted ``mfe_points``, ``mae_points`` and ``lessons`` from the
        UPDATE, so a caller that passed them had them silently discarded and
        had to re-write the whole row through ``record_journal`` to get them
        stored - a write that appears to succeed while dropping data is worse
        than one that fails.
        """
        with self._cursor() as cur:
            cur.execute("""
                UPDATE journal SET result=?, exit_price=?, exit_reason=?,
                    profit_loss=?, realised_r=?, mfe_r=?, mae_r=?,
                    mfe_points=?, mae_points=?,
                    thesis_correct=?, what_invalidated=?, what_happened_after=?,
                    lessons=?, exit_time_et=?
                WHERE entry_id=?
            """, (result, exit_price, exit_reason, profit_loss, realised_r, mfe_r,
                  mae_r, mfe_points, mae_points,
                  None if thesis_correct is None else int(thesis_correct),
                  what_invalidated, what_happened_after, lessons,
                  exit_time_et or to_et(now_et()).isoformat(), entry_id))
            return cur.rowcount > 0

    def journal_entries(self, *, symbol: Optional[str] = None,
                        strategy: Optional[str] = None,
                        regime: Optional[str] = None,
                        result: Optional[str] = None,
                        since: Optional[str] = None,
                        limit: int = 500) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM journal WHERE 1=1"
        args: List[Any] = []
        for col, val in (("symbol", symbol), ("strategy", strategy),
                         ("market_regime", regime), ("result", result)):
            if val:
                sql += f" AND {col}=?"
                args.append(val)
        if since:
            sql += " AND date_et >= ?"
            args.append(since)
        sql += " ORDER BY date_et DESC, time_et DESC LIMIT ?"
        args.append(limit)
        with self._cursor() as cur:
            return [dict(r) for r in cur.execute(sql, args).fetchall()]

    def journal_stats(self, *, symbol: Optional[str] = None,
                      strategy: Optional[str] = None,
                      regime: Optional[str] = None,
                      session: Optional[str] = None) -> Dict[str, Any]:
        """Live performance for a conditional slice - the learning loop's core query."""
        sql = ("SELECT COUNT(*) n, "
               "SUM(CASE WHEN realised_r > 0 THEN 1 ELSE 0 END) wins, "
               "AVG(realised_r) avg_r, SUM(realised_r) total_r, "
               "SUM(profit_loss) total_pnl, "
               "AVG(CASE WHEN realised_r > 0 THEN realised_r END) avg_win_r, "
               "AVG(CASE WHEN realised_r < 0 THEN realised_r END) avg_loss_r "
               "FROM journal WHERE result IN ('WIN','LOSS','BREAKEVEN','SCRATCH')")
        args: List[Any] = []
        for col, val in (("symbol", symbol), ("strategy", strategy),
                         ("market_regime", regime), ("session", session)):
            if val:
                sql += f" AND {col}=?"
                args.append(val)
        with self._cursor() as cur:
            row = cur.execute(sql, args).fetchone()
        n = row["n"] or 0
        return {
            "trades": n,
            "wins": row["wins"] or 0,
            "win_rate": (row["wins"] or 0) / n if n else 0.0,
            "avg_r": row["avg_r"] or 0.0,
            "total_r": row["total_r"] or 0.0,
            "total_pnl": row["total_pnl"] or 0.0,
            "avg_win_r": row["avg_win_r"] or 0.0,
            "avg_loss_r": row["avg_loss_r"] or 0.0,
        }

    # ==================================================================
    # News
    # ==================================================================
    def record_news_event(self, event: NewsEvent) -> str:
        with self._cursor() as cur:
            cur.execute("""
                INSERT OR REPLACE INTO news_events (
                    event_id, timestamp_et, title, category, impact, scheduled,
                    release_time_et, actual, forecast, previous, surprise_direction,
                    affected_symbols, source, summary
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (event.event_id, event.timestamp_et, event.title, event.category,
                  event.impact, int(event.scheduled), event.release_time_et,
                  event.actual, event.forecast, event.previous,
                  event.surprise_direction, self._j(event.affected_symbols),
                  event.source, event.summary))
        return event.event_id

    def record_news_reaction(self, reaction: NewsReaction) -> None:
        with self._cursor() as cur:
            cur.execute("""
                INSERT OR REPLACE INTO news_reactions (
                    event_id, symbol, category, surprise_direction, release_time_et,
                    price_at_release, move_1m, move_5m, move_15m, move_30m, move_60m,
                    range_60m, atr_normalised_60m, reverted, notes
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (reaction.event_id, reaction.symbol, reaction.category,
                  reaction.surprise_direction, reaction.release_time_et,
                  reaction.price_at_release, reaction.move_1m, reaction.move_5m,
                  reaction.move_15m, reaction.move_30m, reaction.move_60m,
                  reaction.range_60m, reaction.atr_normalised_60m,
                  int(reaction.reverted), reaction.notes))

    def historical_reactions(self, symbol: str, category: str,
                             surprise_direction: Optional[str] = None,
                             limit: int = 50) -> List[Dict[str, Any]]:
        sql = ("SELECT * FROM news_reactions WHERE symbol=? AND category=?")
        args: List[Any] = [symbol.upper(), category]
        if surprise_direction:
            sql += " AND surprise_direction=?"
            args.append(surprise_direction)
        sql += " ORDER BY release_time_et DESC LIMIT ?"
        args.append(limit)
        with self._cursor() as cur:
            return [dict(r) for r in cur.execute(sql, args).fetchall()]

    def reaction_profile(self, symbol: str, category: str,
                         surprise_direction: Optional[str] = None
                         ) -> Dict[str, Any]:
        """Measured answer to "how does this symbol react to this event type".

        Returns mean and directional-consistency figures per horizon. Consistency
        matters more than the mean: a +8 point average built from +60 and -44 is
        not a tradeable tendency.
        """
        rows = self.historical_reactions(symbol, category, surprise_direction, 200)
        out: Dict[str, Any] = {"symbol": symbol.upper(), "category": category,
                               "surprise_direction": surprise_direction,
                               "sample": len(rows), "horizons": {}}
        if not rows:
            out["sufficient"] = False
            return out
        for horizon, col in ((1, "move_1m"), (5, "move_5m"), (15, "move_15m"),
                             (30, "move_30m"), (60, "move_60m")):
            vals = [r[col] for r in rows if r[col] is not None]
            if not vals:
                continue
            ups = sum(1 for v in vals if v > 0)
            out["horizons"][f"{horizon}m"] = {
                "mean": round(sum(vals) / len(vals), 4),
                "median": round(sorted(vals)[len(vals) // 2], 4),
                "up_fraction": round(ups / len(vals), 4),
                "max": round(max(vals), 4),
                "min": round(min(vals), 4),
                "n": len(vals),
            }
        out["reverted_fraction"] = round(
            sum(1 for r in rows if r["reverted"]) / len(rows), 4)
        # Below ~10 comparable events, any apparent tendency is noise.
        out["sufficient"] = len(rows) >= 10
        return out

    # ==================================================================
    # Strategy performance
    # ==================================================================
    def upsert_strategy_performance(self, *, strategy_id: str, symbol: str,
                                    timeframe: Optional[int], metrics: Any,
                                    scope: str = "backtest",
                                    regime: str = "ALL", session: str = "ALL",
                                    oos_trades: int = 0,
                                    oos_expectancy_r: float = 0.0,
                                    walk_forward_efficiency: float = 0.0,
                                    robustness_score: float = 0.0,
                                    live_eligible: bool = False,
                                    payload: Any = None) -> None:
        m = metrics
        with self._cursor() as cur:
            cur.execute("""
                INSERT OR REPLACE INTO strategy_performance (
                    strategy_id, symbol, timeframe, regime, session, scope, trades,
                    win_rate, profit_factor, expectancy_r, max_drawdown_r, sharpe,
                    sortino, oos_trades, oos_expectancy_r, walk_forward_efficiency,
                    robustness_score, live_eligible, updated_et, payload
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (strategy_id, symbol.upper(), timeframe, regime, session, scope,
                  getattr(m, "trades", 0), getattr(m, "win_rate", 0.0),
                  getattr(m, "profit_factor", 0.0), getattr(m, "expectancy_r", 0.0),
                  getattr(m, "max_drawdown_r", 0.0), getattr(m, "sharpe", 0.0),
                  getattr(m, "sortino", 0.0), oos_trades, oos_expectancy_r,
                  walk_forward_efficiency, robustness_score, int(live_eligible),
                  to_et(now_et()).isoformat(), self._j(payload)))

    def top_strategies(self, symbol: str, *, limit: int = 10,
                       live_eligible_only: bool = True,
                       regime: Optional[str] = None,
                       scope: str = "backtest") -> List[Dict[str, Any]]:
        """Best strategies for a symbol, ranked by robustness then expectancy."""
        sql = "SELECT * FROM strategy_performance WHERE symbol=? AND scope=?"
        args: List[Any] = [symbol.upper(), scope]
        if live_eligible_only:
            sql += " AND live_eligible=1"
        if regime:
            sql += " AND regime IN (?, 'ALL')"
            args.append(regime)
        sql += " ORDER BY robustness_score DESC, expectancy_r DESC LIMIT ?"
        args.append(limit)
        with self._cursor() as cur:
            return [dict(r) for r in cur.execute(sql, args).fetchall()]

    def strategy_performance(self, strategy_id: str, *, regime: str = "ALL",
                             session: str = "ALL", scope: str = "backtest"
                             ) -> Optional[Dict[str, Any]]:
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT * FROM strategy_performance WHERE strategy_id=? AND scope=? "
                "AND regime=? AND session=?",
                (strategy_id, scope, regime, session)).fetchone()
        return dict(row) if row else None

    # ==================================================================
    # Agent scoring
    # ==================================================================
    def update_agent_score(self, agent_id: str, symbol: str, *, correct: bool,
                           realised_r: float, regime: str = "ALL",
                           session: str = "ALL", false_signal: bool = False) -> None:
        with self._cursor() as cur:
            cur.execute("""
                INSERT INTO agent_scores (agent_id, symbol, regime, session,
                    predictions, correct, total_r, avg_r, false_signals, updated_et)
                VALUES (?,?,?,?,1,?,?,?,?,?)
                ON CONFLICT(agent_id, symbol, regime, session) DO UPDATE SET
                    predictions = predictions + 1,
                    correct = correct + ?,
                    total_r = total_r + ?,
                    avg_r = (total_r + ?) / (predictions + 1),
                    false_signals = false_signals + ?,
                    updated_et = ?
            """, (agent_id, symbol.upper(), regime, session, int(correct),
                  realised_r, realised_r, int(false_signal),
                  to_et(now_et()).isoformat(),
                  int(correct), realised_r, realised_r, int(false_signal),
                  to_et(now_et()).isoformat()))

    def agent_scores(self, *, symbol: Optional[str] = None,
                     regime: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM agent_scores WHERE 1=1"
        args: List[Any] = []
        if symbol:
            sql += " AND symbol=?"
            args.append(symbol.upper())
        if regime:
            sql += " AND regime IN (?, 'ALL')"
            args.append(regime)
        sql += " ORDER BY avg_r DESC"
        with self._cursor() as cur:
            return [dict(r) for r in cur.execute(sql, args).fetchall()]

    def agent_accuracy(self, agent_id: str, symbol: str,
                       regime: str = "ALL") -> Dict[str, Any]:
        """One agent's measured track record in one regime.

        This is what the decision layer weighs analysts by, instead of taking a
        majority vote.
        """
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT SUM(predictions) n, SUM(correct) c, SUM(total_r) tr, "
                "SUM(false_signals) fs FROM agent_scores "
                "WHERE agent_id=? AND symbol=? AND regime IN (?, 'ALL')",
                (agent_id, symbol.upper(), regime)).fetchone()
        n = row["n"] or 0
        return {
            "agent_id": agent_id, "symbol": symbol.upper(), "regime": regime,
            "predictions": n, "correct": row["c"] or 0,
            "accuracy": (row["c"] or 0) / n if n else 0.0,
            "total_r": row["tr"] or 0.0,
            "avg_r": (row["tr"] or 0.0) / n if n else 0.0,
            "false_signals": row["fs"] or 0,
            "sufficient": n >= 20,
        }

    # ==================================================================
    # Callouts and equity
    # ==================================================================
    def record_callout(self, callout: Any) -> str:
        d = callout.to_dict() if hasattr(callout, "to_dict") else dict(callout)
        with self._cursor() as cur:
            cur.execute(
                "INSERT OR REPLACE INTO callouts (callout_id, timestamp_et, symbol,"
                " decision, payload) VALUES (?,?,?,?,?)",
                (d["callout_id"], d["timestamp_et"], d["symbol"], d["decision"],
                 self._j(d)))
        return d["callout_id"]

    def recent_callouts(self, *, symbol: Optional[str] = None,
                        limit: int = 25) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM callouts"
        args: List[Any] = []
        if symbol:
            sql += " WHERE symbol=?"
            args.append(symbol.upper())
        sql += " ORDER BY timestamp_et DESC LIMIT ?"
        args.append(limit)
        with self._cursor() as cur:
            rows = [dict(r) for r in cur.execute(sql, args).fetchall()]
        for r in rows:
            try:
                r["payload"] = json.loads(r["payload"])
            except (json.JSONDecodeError, TypeError):
                r["payload"] = {}
        return rows

    def record_equity(self, equity: float, peak_equity: float = 0.0,
                      daily_pnl: float = 0.0, note: str = "") -> None:
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO equity_curve (timestamp_et, equity, peak_equity,"
                " daily_pnl, note) VALUES (?,?,?,?,?)",
                (to_et(now_et()).isoformat(), equity, peak_equity, daily_pnl, note))

    def equity_history(self, limit: int = 500) -> List[Dict[str, Any]]:
        with self._cursor() as cur:
            return [dict(r) for r in cur.execute(
                "SELECT * FROM equity_curve ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()][::-1]

    # ==================================================================
    def counts(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        with self._cursor() as cur:
            for table in ("journal", "news_events", "news_reactions",
                          "strategy_performance", "agent_scores", "callouts",
                          "equity_curve"):
                out[table] = cur.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
        return out

    def __repr__(self) -> str:
        return f"<Storage {self.path} {self.counts()}>"
