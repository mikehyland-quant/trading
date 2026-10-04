"""Sequential spreadsheet-driven stat-arb trades.

Expects load_trading_inputs to return instruments, mode, and a var_list
containing total anchor shares, execution count, and delay in seconds.
The enabled spreadsheet rows must describe one pair with one BUY and one SELL.
Leg sizes retain the spreadsheet buy_size/sell_size ratio to the anchor.
"""

import asyncio
import math
import sys
from decimal import Decimal, ROUND_FLOOR
from dataclasses import dataclass, field
from pathlib import Path

from ib_insync import obj

from fin_insts.derived.Class_FI_BestOf import BestOf
from fin_insts.Make_Single_Leg_Fin_Insts import get_db_df_and_make_single_leg_fin_insts
from ibkr.Class_IBKR_IB import IBKR_IB
from input_output.Class_InputOutput import InputOutput
from stat_arb.StatArb_LimitLimit import StatArb_LimitLimit

sys.path.append(str(Path(__file__).resolve().parent.parent))

EXECUTION_TIMEOUT_SECONDS = 300
CLEANUP_TIMEOUT_SECONDS = 30

IBKR_PORT = 7496

WB_NAME = "2026 Group Trading Inputs.xlsm"
WS_NAME = "ADMIN INPUTS"
TBL_NAME = ADMIN_WS_NAME.replace(" ", "_")

STRAT_NAME = "LimitLimit"
STRATEGY_TYPES = {"LimitLimit": StatArb_LimitLimit}



@dataclass
class BatchProgress:
    """Track settled fills and replace each partial round with one extra round."""

    remaining_anchor_shs: int
    executions_left: int
    completed_executions: int = 0
    filled_shs_by_symbol: dict = field(default_factory=dict)
    execution_results: list = field(default_factory=list)

    def record(self, anchor, requested, filled_shs_by_symbol):
        fills = {symbol: Decimal(str(size)) for symbol, size in filled_shs_by_symbol.items()}
        if len(fills) != 2 or any(not size.is_finite() or size <= 0 for size in fills.values()):
            raise RuntimeError("A settled execution must report positive fills for both symbols")
        filled_anchor = fills[anchor]
        if filled_anchor != int(filled_anchor) or filled_anchor > requested:
            raise RuntimeError("Anchor fills must be whole shares and cannot exceed the request")
        if filled_anchor > self.remaining_anchor_shs:
            raise RuntimeError("Anchor fills exceed the remaining batch target")
        self.remaining_anchor_shs -= int(filled_anchor)
        self.executions_left -= 1
        if filled_anchor < requested:
            self.executions_left += 1
        self.completed_executions += 1
        self.execution_results.append(fills)
        for symbol, size in fills.items():
            self.filled_shs_by_symbol[symbol] = self.filled_shs_by_symbol.get(symbol, Decimal(0)) + size


def execution_fills(strategy):
    """Read settled fills, verifying the future strategy report against owned orders.

    Future strategy scripts should set execution_result to {symbol: actual_shares}
    before setting done_event, after adjusting the hedge and settling leftovers.
    A partial package requires that explicit completion report. Until that
    strategy integration exists, only fully filled original packages can pass.
    """
    actual = {}
    expected = {}
    for obj in strategy.objs_list:
        symbol = obj.ibkr_contract.symbol.upper()
        actual[symbol] = sum((Decimal(str(trade.orderStatus.filled))
                              for owner, trade in strategy.orders if owner is obj), Decimal(0))
        expected[symbol] = Decimal(str(getattr(obj, f"{obj.buy_or_sell.lower()}_size")))
    report = getattr(strategy, "execution_result", None)
    if report is None:
        if actual != expected:
            raise RuntimeError("Partial execution requires the strategy's settled execution_result report")
    else:
        report = {symbol.upper(): Decimal(str(size)) for symbol, size in report.items()}
        if report != actual:
            raise RuntimeError("Strategy execution_result does not match settled order fills")
    return actual

'''
def anchor_size(remaining_anchor_shs, executions_left):
    """Allocate whole shares, leaving the exact remainder for the final run."""
    if remaining_anchor_shs < executions_left or executions_left <= 0:
        raise ValueError("Need at least one anchor share per remaining execution")
    return remaining_anchor_shs // executions_left


def leg_size(anchor_shares, base_size, base_anchor_size, increment=1, minimum=1):
    """Scale the designated leg size, rounding down to its order increment."""
    values = [Decimal(str(v)) for v in (anchor_shares, base_size, base_anchor_size,
                                        increment or 1, minimum or 0)]
    if any(not v.is_finite() for v in values) or any(v <= 0 for v in values[:4]):
        raise ValueError("Share sizes and increments must be finite and positive")
    shares, base, anchor, step, min_size = values
    quantity = (shares * base / anchor / step).to_integral_value(rounding=ROUND_FLOOR) * step
    if quantity <= 0 or quantity < min_size:
        raise ValueError("Execution leg size is below its minimum order size")
    return float(quantity)
'''

def batch_strategy_type():
    # Delay live imports so sizing tests need no trading packages.
    from stat_arb.StatArb_LimitLimit import StatArb_LimitLimit

    class BatchLimitLimit(StatArb_LimitLimit):
        """Use existing pricing, with full-fill completion and owned order tracking."""

        def __init__(self, mode, objects):
            self.orders = []
            self.finished_orders = set()
            super().__init__(mode, objects)

        def _placed_order_admin(self, obj, trade, input_amt):
            if not any(existing is trade for _, existing in self.orders):
                self.orders.append((obj, trade))
            super()._placed_order_admin(obj, trade, input_amt)

        def on_trade_exec(self, obj, trade):
            # A partial fill must not disable handling of later fills or finish
            # the package. Keep quoting until the first leg is fully filled.
            if trade.orderStatus.status != "Filled":
                return
            key = trade.order.orderId
            if key in self.finished_orders:
                return
            self.finished_orders.add(key)
            super().on_trade_exec(obj, trade)

        async def _update_trade(self, obj):
            if obj.strat_on_mkt_data_change:
                await super()._update_trade(obj)

    return BatchLimitLimit


async def settle(strategy, broker, timeout):
    """Disable callbacks, drain workers, cancel leftovers and await acknowledgements."""
    for obj in strategy.objs_list:
        for flag in ("strat_on_closing_price", "strat_on_mkt_data_change", "strat_on_trade_exec"):
            setattr(obj, flag, False)
    tasks = [getattr(strategy, name, None)
             for name in ("update_task", "follow_up_orders_task")]
    tasks = [task for task in tasks if task is not None]
    for task in tasks:
        if not task.done():
            task.cancel()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for _, trade in strategy.orders:
        if not trade.isDone():
            broker.cancel_order(trade)

    async def wait_terminal():
        while any(not trade.isDone() for _, trade in strategy.orders):
            await asyncio.sleep(0.05)

    await asyncio.wait_for(wait_terminal(), timeout)
    for result in results:
        if isinstance(result, Exception):
            raise result


async def execute(strategy, broker):
    while not strategy.done_event.is_set():
        if not broker.ib.isConnected():
            raise RuntimeError("Broker disconnected during execution")
        for _, trade in strategy.orders:
            if trade.isDone() and trade.orderStatus.status != "Filled":
                raise RuntimeError(f"Order {trade.order.orderId} ended with {trade.orderStatus.status}")
        for name in ("update_task", "follow_up_orders_task"):
            task = getattr(strategy, name, None)
            if task is not None and task.done():
                task.result()
        await asyncio.sleep(0.05)

def load_inputs(io, wb_name, ws_name, tbl_name):
    """Read enabled instruments and their shared group or pair mode."""
    workbook, sheet = io.set_xw_book_and_sheet(wb_name, ws_name)
    inputs = io.get_xw_dict(sheet, tbl_name, table=True)
        # this loads the dictionary on the ADMIN INPUTS sheet
    
    sheet = io.set_xw_sheet(workbook, inputs["trading_inputs_sheet"])

    instruments = (
        sheet.range(inputs["trading_inputs_upload_cell"])
        .expand()
        .options(pd.DataFrame, index=False)
        .value
    )
    instruments = instruments.loc[instruments["TRUE/FALSE"]]
    print(instruments, "\n")
        # this loads one group or pair of instruments

    variables = (
            sheet.range(inputs["trading_variables_upload_cell"])
            .expand()
            .options(dictionary, index=False)
            .value
        )

    return instruments, variables

def attach_strategy_variables(obj, instruments, input_variables):
    """Apply spreadsheet attributes to each instrument object."""
    row = instruments.loc[instruments["my_fi_name"] == obj.ibkr_contract.symbol].iloc[0]
    obj.scalar_size_FIs_per_unit = float(row["multiplier"])
    obj.div_adj = float(row["div_adj"])
    obj.buy_or_sell = row["buy_or_sell"]
    if obj.buy_or_sell.upper() == "BUY":
        obj.buy_size = float(row["buy_size"])
        obj.sell_size = None
    elif obj.buy_or_sell.upper() == "SELL":
        obj.sell_size = float(row["sell_size"])
        obj.buy_size = None
    obj.reset_scalars()


async def main():
    from stat_arb.App_StatArb_Trade import (
        IBKR_PORT, InputOutput, IBKR_IB, build_strategies,
        get_db_df_and_make_single_leg_fin_insts
    )
    # strategy_type = batch_strategy_type()
    broker = IBKR_IB(port=IBKR_PORT)

    instruments, input_variables = load_inputs(InputOutput(), WB_NAME, WS_NAME, TBL_NAME)

    executions = int(input_variables["executions"])
    delay = float(input_variables["delay"])    
    g_or_p = input_variables["group_or_pairs"].lower()
    profit_margin = float(input_variables["profit_margin"]) 

    objects = get_db_df_and_make_single_leg_fin_insts(instruments)
    for obj in objects:
        obj = attach_strategy_variables(obj, instruments, input_variables)
    
    try:
        await broker.connect()
        await asyncio.gather(*(broker.create_simple_contract(obj) for obj in objects))
        await asyncio.gather(*(broker.complete_obj(obj) for obj in objects))

        strategy = StatArb_LimitLimit(g_or_p, objects)

        for obj in objects:
            obj.platform_obj = broker
            if obj.buy_or_sell.upper() == "BUY":
                obj.total_target_shares = obj.buy_size
                obj.buy_size = None
            elif obj.buy_or_sell.upper() == "SELL":
                obj.total_target_shares = obj.sell_size
                obj.sell_size = None
    
        while executions > 0:

            for obj in objects:
                reset obj flags
                if obj.buy_or_sell.upper() == "BUY":
                    obj.buy_size = obj.total_target_shares // executions
                elif obj.buy_or_sell.upper() == "SELL":
                    obj.sell_size = obj.total_target_shares // executions 
            
            try:
                await broker.start_streams(objects)
                
            finally:
                try:
                    await settle(strategy, broker, CLEANUP_TIMEOUT_SECONDS)
                finally:
                    for ticker, obj in list(broker.ticker_dict.items()):
                        ticker.updateEvent -= broker.tick_handler
                        broker.ib.cancelMktData(obj.ibkr_contract)
                    broker.ticker_dict.clear()
            # Account for actual shares only after every order has settled.
            fills = execution_fills(strategy)
            progress.record(anchor, requested, fills)
            for _, trade in strategy.orders:
                trade.statusEvent -= broker.order_handler
                broker.obj_by_order_handler.pop(trade.order, None)
            print(f"Execution {number} fills: {fills}")
            print(f"Cumulative shares: {progress.filled_shs_by_symbol}")
            print(f"Execution {number} complete; {progress.remaining_anchor_shs} anchor shares remaining; "
                  f"{progress.executions_left} executions left")

            if executions > 1:
                await asyncio.sleep(delay)
            
    finally:
        broker.ib.disconnect()
    print(f"Batch complete: {total_anchor_shs} anchor shares")
    return progress



if __name__ == "__main__":
    asyncio.run(main())
