import asyncio
import sys
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent.parent))

from fin_insts.derived.Class_FI_BestOf import BestOf
from fin_insts.Make_Single_Leg_Fin_Insts import get_db_df_and_make_single_leg_fin_insts
from ibkr.Class_IBKR_IB import IBKR_IB
from input_output.Class_InputOutput import InputOutput
from stat_arb.StatArb_LimitLimit import StatArb_LimitLimit


IBKR_PORT = 7496
STRAT_NAME = "LimitLimit"
STRAT_WB_NAME = "2026 Group Trading Inputs.xlsm"
STRAT_WS_NAME = "ADMIN INPUTS"
STRAT_TBL_NAME = STRAT_WS_NAME.replace(" ", "_")
STRATEGY_TYPES = {"LimitLimit": StatArb_LimitLimit}


class TradingBestOf(BestOf):
    """Rank instruments using their strategy cash flows."""

    def update_subscriber_data(self, obj):
        obj.strat_hit_bid = obj.cf_unit_hit_bid - obj.comm_unit_hit_bid
        obj.strat_bid_size = obj.size_unit_bid
        obj.strat_lift_ask = obj.cf_unit_lift_ask - obj.comm_unit_lift_ask
        obj.strat_ask_size = obj.size_unit_ask
        super().update_subscriber_data(obj)


def load_trading_inputs(io):
    """Read enabled instruments and their shared group or pair mode."""
    workbook, sheet = io.set_xw_book_and_sheet(STRAT_WB_NAME, STRAT_WS_NAME)
    inputs = io.get_xw_dict(sheet, STRAT_TBL_NAME, table=True)
    sheet = io.set_xw_sheet(workbook, inputs["trading_inputs_sheet"])
    instruments = (
        sheet.range(inputs["trading_inputs_upload_cell"])
        .expand()
        .options(pd.DataFrame, index=False)
        .value
    )
    instruments = instruments.loc[instruments["TRUE/FALSE"]]
    print(instruments, "\n")

    modes = instruments["g_or_p"].unique()
    if len(modes) != 1:
        raise ValueError(f"Expected exactly one g_or_p value, found: {modes}")
    return instruments, modes[0].lower()


def build_strategies(instruments, objs_list, group_or_pair):
    """Apply spreadsheet attributes and create one strategy per anchor."""
    attr_names = instruments.columns.drop(["my_fi_name", "my_pf_name", "multiplier"])
    groups = instruments.groupby("anchor")["my_fi_name"].apply(list)
    strategy_type = STRATEGY_TYPES[STRAT_NAME]
    strategies = {}

    for anchor, symbols in groups.items():
        anchor_objs = []
        for symbol in symbols:
            obj = next(obj for obj in objs_list if obj.ibkr_contract.symbol == symbol)
            row = instruments.loc[instruments["my_fi_name"] == symbol].iloc[0]
            obj.scalar_size_FIs_per_unit = float(row["multiplier"])
            obj.reset_scalars()

            for attr in attr_names:
                value = row[attr]
                setattr(obj, attr, value.upper() if isinstance(value, str) else value)
            anchor_objs.append(obj)

        strategy_input = anchor_objs
        if group_or_pair == "group":
            strategy_input = TradingBestOf(
                anchor,
                anchor_objs,
                [("strat_hit_bid", max), ("strat_lift_ask", min)],
                mode="auto",
                ranked_list=True,
            )
        strategies[anchor] = strategy_type(group_or_pair, strategy_input)

    return strategies


async def main():
    """Load instruments, run strategies, and clean up the broker connection."""
    io = InputOutput()
    ibkr = IBKR_IB(port=IBKR_PORT)
    instruments, group_or_pair = load_trading_inputs(io)
    objs_list = get_db_df_and_make_single_leg_fin_insts(instruments)
    for obj in objs_list:
        obj.platform_obj = ibkr

    stream_task = None
    try:
        await ibkr.connect()
        print("IBKR connected:", ibkr.ib.isConnected(), "\n")
        await asyncio.gather(*(ibkr.create_simple_contract(obj) for obj in objs_list))
        await asyncio.gather(*(ibkr.complete_obj(obj) for obj in objs_list))

        strategies = build_strategies(instruments, objs_list, group_or_pair)
        stream_task = asyncio.create_task(ibkr.start_streams(objs_list))
        await asyncio.sleep(1)
        await asyncio.gather(*(strategy.done_event.wait() for strategy in strategies.values()))
    finally:
        try:
            if stream_task is not None:
                stream_task.cancel()
                await asyncio.gather(stream_task, return_exceptions=True)
        finally:
            ibkr.ib.disconnect()

    print("Program finished cleanly.", "\n")


if __name__ == "__main__":
    asyncio.run(main())
