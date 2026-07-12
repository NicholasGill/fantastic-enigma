from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from statistics import mean, median

from sqlalchemy.engine import make_url

DEFAULT_AH_CUT_BPS = 500


@dataclass(frozen=True)
class BacktestStrategy:
    lookback_runs: int = 6
    buy_discount_bps: int = 1500
    sell_markup_bps: int = 1000
    stop_loss_bps: int = 2000
    min_sell_through_bps: int = 0
    max_position_quantity: int = 20
    max_holding_runs: int = 8
    starting_cash: int = 1_000_000_000
    ah_cut_bps: int = DEFAULT_AH_CUT_BPS
    auction_duration_hours: int = 48

    def __post_init__(self) -> None:
        if self.lookback_runs <= 1:
            raise ValueError("lookback_runs must be greater than 1")
        if self.buy_discount_bps < 0:
            raise ValueError("buy_discount_bps must be greater than or equal to 0")
        if self.sell_markup_bps < 0:
            raise ValueError("sell_markup_bps must be greater than or equal to 0")
        if self.stop_loss_bps < 0:
            raise ValueError("stop_loss_bps must be greater than or equal to 0")
        if self.min_sell_through_bps < 0:
            raise ValueError("min_sell_through_bps must be greater than or equal to 0")
        if self.max_position_quantity <= 0:
            raise ValueError("max_position_quantity must be greater than 0")
        if self.max_holding_runs <= 0:
            raise ValueError("max_holding_runs must be greater than 0")
        if self.starting_cash <= 0:
            raise ValueError("starting_cash must be greater than 0")
        if not 0 <= self.ah_cut_bps < 10000:
            raise ValueError("ah_cut_bps must be between 0 and 9999")
        if self.auction_duration_hours <= 0:
            raise ValueError("auction_duration_hours must be greater than 0")


@dataclass(frozen=True)
class BacktestTrade:
    item_id: int
    name: str
    market: str
    buy_run_id: int
    buy_started_at: datetime | None
    sell_run_id: int | None
    sell_started_at: datetime | None
    quantity: int
    buy_unit_price: int
    target_unit_price: int
    sell_unit_price: int | None
    gross_profit: int
    auction_cut: int
    deposit_cost: int
    net_profit: int
    holding_runs: int
    exit_reason: str


@dataclass(frozen=True)
class BacktestResult:
    strategy: BacktestStrategy
    started_at: datetime | None
    ended_at: datetime | None
    snapshot_count: int
    item_count: int
    trade_count: int
    closed_trade_count: int
    open_position_count: int
    winning_trade_count: int
    losing_trade_count: int
    win_rate: float
    starting_cash: int
    ending_cash: int
    realized_profit: int
    unrealized_profit: int
    total_profit: int
    return_bps: int
    max_drawdown: int
    max_drawdown_bps: int
    average_holding_runs: float
    trades: list[BacktestTrade] = field(default_factory=list)


@dataclass
class _Snapshot:
    fetch_run_id: int
    started_at: datetime | None
    item_id: int
    name: str
    market: str
    listing_count: int
    total_quantity: int
    min_unit_price: int | None
    first_quartile_unit_price: int | None
    median_unit_price: int | None
    third_quartile_unit_price: int | None
    sell_through_ratio_bps: int
    sell_through_confidence: int
    vendor_sell_unit_price: int | None


@dataclass
class _Position:
    item_id: int
    name: str
    market: str
    buy_run_id: int
    buy_started_at: datetime | None
    quantity: int
    buy_unit_price: int
    target_unit_price: int
    deposit_unit_price: int
    holding_runs: int = 0


class BacktestEngine:
    def __init__(self, database_url: str, strategy: BacktestStrategy | None = None) -> None:
        self.database_path = _sqlite_database_path(database_url)
        self.strategy = strategy or BacktestStrategy()

    def run(self) -> BacktestResult:
        snapshots = self._load_snapshots()
        by_item: dict[int, list[_Snapshot]] = {}
        for snapshot in snapshots:
            by_item.setdefault(snapshot.item_id, []).append(snapshot)

        cash = self.strategy.starting_cash
        peak_equity = cash
        max_drawdown = 0
        trades: list[BacktestTrade] = []
        positions: dict[int, _Position] = {}
        histories: dict[int, list[_Snapshot]] = {}
        item_names = {snapshot.item_id: snapshot.name for snapshot in snapshots}
        latest_by_item: dict[int, _Snapshot] = {}

        for snapshot in snapshots:
            latest_by_item[snapshot.item_id] = snapshot
            history = histories.setdefault(snapshot.item_id, [])
            position = positions.get(snapshot.item_id)
            if position is not None:
                cash, trade = self._maybe_close_position(cash, position, snapshot)
                if trade is not None:
                    trades.append(trade)
                    del positions[snapshot.item_id]
                    position = None

            if position is None:
                new_position, cash = self._maybe_open_position(cash, snapshot, history)
                if new_position is not None:
                    positions[snapshot.item_id] = new_position

            history.append(snapshot)
            equity = cash + _mark_to_market_value(positions.values(), latest_by_item, self.strategy.ah_cut_bps)
            if equity > peak_equity:
                peak_equity = equity
            drawdown = peak_equity - equity
            if drawdown > max_drawdown:
                max_drawdown = drawdown

        unrealized_profit = 0
        for position in positions.values():
            latest = latest_by_item.get(position.item_id)
            mark_price = _sell_price(latest) if latest is not None else None
            if mark_price is None:
                continue
            proceeds = _net_sale_proceeds(mark_price, position.quantity, self.strategy.ah_cut_bps)
            cost = position.buy_unit_price * position.quantity
            unrealized_profit += proceeds - cost - position.deposit_unit_price * position.quantity

        realized_profit = sum(trade.net_profit for trade in trades)
        total_profit = realized_profit + unrealized_profit
        ending_cash = cash + _mark_to_market_value(positions.values(), latest_by_item, self.strategy.ah_cut_bps)
        closed_trades = [trade for trade in trades if trade.sell_run_id is not None]
        winning_trade_count = sum(1 for trade in closed_trades if trade.net_profit > 0)
        losing_trade_count = sum(1 for trade in closed_trades if trade.net_profit < 0)
        return BacktestResult(
            strategy=self.strategy,
            started_at=snapshots[0].started_at if snapshots else None,
            ended_at=snapshots[-1].started_at if snapshots else None,
            snapshot_count=len({snapshot.fetch_run_id for snapshot in snapshots}),
            item_count=len(item_names),
            trade_count=len(trades),
            closed_trade_count=len(closed_trades),
            open_position_count=len(positions),
            winning_trade_count=winning_trade_count,
            losing_trade_count=losing_trade_count,
            win_rate=winning_trade_count / len(closed_trades) if closed_trades else 0.0,
            starting_cash=self.strategy.starting_cash,
            ending_cash=ending_cash,
            realized_profit=realized_profit,
            unrealized_profit=unrealized_profit,
            total_profit=total_profit,
            return_bps=round((total_profit / self.strategy.starting_cash) * 10000),
            max_drawdown=max_drawdown,
            max_drawdown_bps=round((max_drawdown / peak_equity) * 10000) if peak_equity else 0,
            average_holding_runs=mean([trade.holding_runs for trade in closed_trades]) if closed_trades else 0.0,
            trades=trades,
        )

    def _load_snapshots(self) -> list[_Snapshot]:
        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                select
                    h.fetch_run_id,
                    r.started_at,
                    h.item_id,
                    coalesce(m.name, t.name, 'Item ' || h.item_id) as name,
                    h.market,
                    h.listing_count,
                    h.total_quantity,
                    h.min_unit_price,
                    h.first_quartile_unit_price,
                    h.median_unit_price,
                    h.third_quartile_unit_price,
                    coalesce(st.sell_through_ratio_bps, 0) as sell_through_ratio_bps,
                    coalesce(st.confidence, 0) as sell_through_confidence,
                    m.sell_price as vendor_sell_unit_price
                from item_history_metrics h
                join fetch_runs r on r.id = h.fetch_run_id
                left join tracked_items t on t.item_id = h.item_id
                left join item_metadata m on m.item_id = h.item_id
                left join sell_through_metrics st
                    on st.fetch_run_id = h.fetch_run_id
                    and st.item_id = h.item_id
                    and st.market = h.market
                where r.status = 'success'
                order by h.fetch_run_id, h.market, h.item_id
                """
            ).fetchall()

        return [
            _Snapshot(
                fetch_run_id=int(row["fetch_run_id"]),
                started_at=_parse_datetime(row["started_at"]),
                item_id=int(row["item_id"]),
                name=str(row["name"]),
                market=str(row["market"]),
                listing_count=int(row["listing_count"]),
                total_quantity=int(row["total_quantity"]),
                min_unit_price=_optional_int(row["min_unit_price"]),
                first_quartile_unit_price=_optional_int(row["first_quartile_unit_price"]),
                median_unit_price=_optional_int(row["median_unit_price"]),
                third_quartile_unit_price=_optional_int(row["third_quartile_unit_price"]),
                sell_through_ratio_bps=int(row["sell_through_ratio_bps"] or 0),
                sell_through_confidence=int(row["sell_through_confidence"] or 0),
                vendor_sell_unit_price=_optional_int(row["vendor_sell_unit_price"]),
            )
            for row in rows
        ]

    def _maybe_open_position(
        self,
        cash: int,
        snapshot: _Snapshot,
        history: list[_Snapshot],
    ) -> tuple[_Position | None, int]:
        if len(history) < self.strategy.lookback_runs:
            return (None, cash)
        if snapshot.min_unit_price is None or snapshot.total_quantity <= 0:
            return (None, cash)
        if snapshot.sell_through_ratio_bps < self.strategy.min_sell_through_bps:
            return (None, cash)

        baseline = _baseline_price(history[-self.strategy.lookback_runs :])
        if baseline is None:
            return (None, cash)
        buy_threshold = baseline * (10000 - self.strategy.buy_discount_bps) // 10000
        if snapshot.min_unit_price > buy_threshold:
            return (None, cash)

        quantity = min(self.strategy.max_position_quantity, snapshot.total_quantity)
        affordable_quantity = cash // snapshot.min_unit_price
        quantity = min(quantity, affordable_quantity)
        if quantity <= 0:
            return (None, cash)

        target_from_baseline = baseline * (10000 + self.strategy.sell_markup_bps) // 10000
        target_unit_price = max(target_from_baseline, snapshot.min_unit_price + 1)
        cost = snapshot.min_unit_price * quantity
        return (
            _Position(
                item_id=snapshot.item_id,
                name=snapshot.name,
                market=snapshot.market,
                buy_run_id=snapshot.fetch_run_id,
                buy_started_at=snapshot.started_at,
                quantity=quantity,
                buy_unit_price=snapshot.min_unit_price,
                target_unit_price=target_unit_price,
                deposit_unit_price=_deposit_unit_price(snapshot.vendor_sell_unit_price, self.strategy.auction_duration_hours),
            ),
            cash - cost,
        )

    def _maybe_close_position(
        self,
        cash: int,
        position: _Position,
        snapshot: _Snapshot,
    ) -> tuple[int, BacktestTrade | None]:
        position.holding_runs += 1
        sell_unit_price = _sell_price(snapshot)
        if sell_unit_price is None:
            return (cash, None)

        stop_loss_price = position.buy_unit_price * (10000 - self.strategy.stop_loss_bps) // 10000
        if sell_unit_price >= position.target_unit_price:
            return self._close_position(cash, position, snapshot, sell_unit_price, "target")
        if sell_unit_price <= stop_loss_price:
            return self._close_position(cash, position, snapshot, sell_unit_price, "stop_loss")
        if position.holding_runs >= self.strategy.max_holding_runs:
            return self._close_position(cash, position, snapshot, sell_unit_price, "max_holding")
        return (cash, None)

    def _close_position(
        self,
        cash: int,
        position: _Position,
        snapshot: _Snapshot,
        sell_unit_price: int,
        exit_reason: str,
    ) -> tuple[int, BacktestTrade]:
        gross_sale = sell_unit_price * position.quantity
        gross_profit = gross_sale - position.buy_unit_price * position.quantity
        auction_cut = gross_sale * self.strategy.ah_cut_bps // 10000
        deposit_cost = position.deposit_unit_price * position.quantity
        net_profit = gross_profit - auction_cut - deposit_cost
        proceeds = gross_sale - auction_cut
        return (
            cash + proceeds,
            BacktestTrade(
                item_id=position.item_id,
                name=position.name,
                market=position.market,
                buy_run_id=position.buy_run_id,
                buy_started_at=position.buy_started_at,
                sell_run_id=snapshot.fetch_run_id,
                sell_started_at=snapshot.started_at,
                quantity=position.quantity,
                buy_unit_price=position.buy_unit_price,
                target_unit_price=position.target_unit_price,
                sell_unit_price=sell_unit_price,
                gross_profit=gross_profit,
                auction_cut=auction_cut,
                deposit_cost=deposit_cost,
                net_profit=net_profit,
                holding_runs=position.holding_runs,
                exit_reason=exit_reason,
            ),
        )


def backtest_result_rows(result: BacktestResult) -> list[dict[str, object]]:
    return [
        {
            "snapshot_count": result.snapshot_count,
            "item_count": result.item_count,
            "trade_count": result.trade_count,
            "closed_trade_count": result.closed_trade_count,
            "open_position_count": result.open_position_count,
            "winning_trade_count": result.winning_trade_count,
            "losing_trade_count": result.losing_trade_count,
            "win_rate_bps": round(result.win_rate * 10000),
            "starting_cash": result.starting_cash,
            "ending_cash": result.ending_cash,
            "realized_profit": result.realized_profit,
            "unrealized_profit": result.unrealized_profit,
            "total_profit": result.total_profit,
            "return_bps": result.return_bps,
            "max_drawdown": result.max_drawdown,
            "max_drawdown_bps": result.max_drawdown_bps,
            "average_holding_runs": round(result.average_holding_runs, 2),
        }
    ]


def backtest_trade_rows(result: BacktestResult) -> list[dict[str, object]]:
    return [
        {
            "item_id": trade.item_id,
            "name": trade.name,
            "market": trade.market,
            "buy_run_id": trade.buy_run_id,
            "buy_started_at": trade.buy_started_at.isoformat() if trade.buy_started_at else "",
            "sell_run_id": trade.sell_run_id or "",
            "sell_started_at": trade.sell_started_at.isoformat() if trade.sell_started_at else "",
            "quantity": trade.quantity,
            "buy_unit_price": trade.buy_unit_price,
            "target_unit_price": trade.target_unit_price,
            "sell_unit_price": trade.sell_unit_price or "",
            "gross_profit": trade.gross_profit,
            "auction_cut": trade.auction_cut,
            "deposit_cost": trade.deposit_cost,
            "net_profit": trade.net_profit,
            "holding_runs": trade.holding_runs,
            "exit_reason": trade.exit_reason,
        }
        for trade in result.trades
    ]


def _sqlite_database_path(database_url: str) -> Path:
    url = make_url(database_url)
    if url.drivername != "sqlite" or url.database is None:
        raise ValueError("backtesting currently supports sqlite database URLs")
    return Path(url.database)


def _parse_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


def _baseline_price(history: list[_Snapshot]) -> int | None:
    prices = [
        snapshot.first_quartile_unit_price or snapshot.median_unit_price
        for snapshot in history
        if snapshot.first_quartile_unit_price is not None or snapshot.median_unit_price is not None
    ]
    return int(median(prices)) if prices else None


def _sell_price(snapshot: _Snapshot | None) -> int | None:
    if snapshot is None:
        return None
    return snapshot.first_quartile_unit_price or snapshot.median_unit_price or snapshot.min_unit_price


def _net_sale_proceeds(unit_price: int, quantity: int, ah_cut_bps: int) -> int:
    gross = unit_price * quantity
    return gross - (gross * ah_cut_bps // 10000)


def _deposit_unit_price(vendor_sell_price: int | None, duration_hours: int) -> int:
    if vendor_sell_price is None:
        return 0
    return vendor_sell_price * 1500 * duration_hours // (12 * 10000)


def _mark_to_market_value(
    positions: object,
    latest_by_item: dict[int, _Snapshot],
    ah_cut_bps: int,
) -> int:
    total = 0
    for position in positions:
        latest = latest_by_item.get(position.item_id)
        sell_price = _sell_price(latest)
        if sell_price is None:
            continue
        total += _net_sale_proceeds(sell_price, position.quantity, ah_cut_bps)
    return total
