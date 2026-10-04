import asyncio
import sys
from pathlib import Path

from ib_insync import obj
import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent.parent))

from fin_insts.derived.Class_FI_BestOf import BestOf
from fin_insts.Make_Single_Leg_Fin_Insts import get_db_df_and_make_single_leg_fin_insts
from ibkr.Class_IBKR_IB import IBKR_IB
from input_output.Class_InputOutput import InputOutput
from stat_arb.StatArb_LimitLimit import StatArb_LimitLimit
# from stat_arb.StatArb_LimitMarket import StatArb_LimitMarket


STRATEGY_TYPES = {"LimitLimit": StatArb_LimitLimit,
                 # "LimitMarket": StatArb_LimitMarket
                  }

IBKR_PORT = 7496

STRAT_WB_NAME = "2026 Group Trading Inputs.xlsm"
STRAT_WS_NAME = "ADMIN INPUTS"
STRAT_TBL_NAME = STRAT_WS_NAME.replace(" ", "_")

STRAT_NAME = "LimitLimit"


class TradingBestOf(BestOf):
    """Rank instruments using their strategy cash flows."""

    def update_subscriber_data(self, obj):
        obj.strat_hit_bid = obj.cf_unit_hit_bid - obj.comm_unit_hit_bid
        obj.strat_bid_size = obj.size_unit_bid
        obj.strat_lift_ask = obj.cf_unit_lift_ask - obj.comm_unit_lift_ask
        obj.strat_ask_size = obj.size_unit_ask
        super().update_subscriber_data(obj)


def load_inputs(io):
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

    variables = (
            sheet.range(inputs["trading_variables_upload_cell"])
            .expand()
            .options(dict)
            .value
        )

    return instruments, variables


def attach_trading_attributes(obj, instruments):
    """Attach trading attributes to a financial instrument."""
    row = instruments.loc[instruments["my_fi_name"] == obj.ibkr_contract.symbol].iloc[0]
    attr_names = instruments.columns.drop(["my_fi_name", "my_pf_name", "multiplier"])
    for attr in attr_names: 
        value = row[attr]
        setattr(obj, attr, value.upper() if isinstance(value, str) else value)  
    obj.total_trading_size = obj.buy_size if obj.buy_or_sell == "BUY" else obj.sell_size
    obj.scalar_size_FIs_per_unit = float(row["multiplier"])
    obj.reset_scalars()
    return obj


async def main():
    """Load instruments, run strategies, and clean up the broker connection."""
    
    stream_task = None
    strategy_type = STRATEGY_TYPES[STRAT_NAME]
    
    actives_df, input_dict = load_inputs(InputOutput())
    objs_list = get_db_df_and_make_single_leg_fin_insts(actives_df)

    g_or_p = "pair" if len(objs_list) == 2 else "group"
    profit_margin = float(input_dict["profit_margin"])

    try:
        ibkr = IBKR_IB(port=IBKR_PORT)
        await ibkr.connect()
        print("IBKR connected:", ibkr.ib.isConnected(), "\n")

        await asyncio.gather(*(ibkr.create_simple_contract(obj) for obj in objs_list))
        await asyncio.gather(*(ibkr.complete_obj(obj) for obj in objs_list))

        for obj in objs_list:
            obj = attach_trading_attributes(obj, actives_df)
            obj.platform_obj = ibkr
            obj.profit_margin = profit_margin

        strategy = strategy_type(g_or_p, objs_list)

        stream_task = asyncio.create_task(ibkr.start_streams(objs_list))

        await asyncio.sleep(1)
        await strategy.done_event.wait()

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
