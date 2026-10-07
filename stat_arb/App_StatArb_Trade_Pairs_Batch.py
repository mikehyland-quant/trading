import asyncio
import sys
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent.parent))

from fin_insts.Make_Single_Leg_Fin_Insts import get_db_df_and_make_single_leg_fin_insts
from ibkr.Class_IBKR_IB import IBKR_IB
from input_output.Class_InputOutput import InputOutput
from stat_arb.StatArb_LimitLimit import StatArb_LimitLimit


STRATEGY_TYPES = {"LimitLimit": StatArb_LimitLimit}

IBKR_PORT = 7496

STRAT_WB_NAME = "2026 Group Trading Inputs.xlsm"
STRAT_WS_NAME = "ADMIN INPUTS"
STRAT_TBL_NAME = STRAT_WS_NAME.replace(" ", "_")


def load_inputs(io):
    """Read enabled instruments and shared trading variables."""

    workbook, sheet = io.set_xw_book_and_sheet(STRAT_WB_NAME, STRAT_WS_NAME)
    inputs = io.get_xw_dict(sheet, STRAT_TBL_NAME, table=True)

    sheet = io.set_xw_sheet(workbook, inputs["trading_inputs_sheet"])

    df = (
        sheet.range(inputs["trading_inputs_upload_cell"])
        .expand()
        .options(pd.DataFrame, index=False)
        .value
    )
    df = df.loc[df["TRUE/FALSE"].eq(True)]

    print(df, "\n")

    return df


def attach_trading_attributes(obj, df):
    """Attach trading attributes to a financial instrument."""
    row = df.loc[df["my_fi_name"] == obj.ibkr_contract.symbol].iloc[0]
    trading_attributes = row[["anchor", "div_adj", "shares"]]
    for attr, value in trading_attributes.items():
        setattr(obj, attr, value.upper() if isinstance(value, str) else value)

    obj.buy_or_sell = "BUY" if obj.shares > 0 else "SELL"

    obj.remaining_trading_size = abs(obj.shares)
    obj.filled_trading_size = 0

    obj.scalar_size_FIs_per_unit = float(row["multiplier"])
    obj.reset_scalars()

    return obj


async def main():
    """Run a pair or group in batches and clean up the broker connection."""

    ibkr = IBKR_IB(port=IBKR_PORT)
    stream_task = None

    input_df = load_inputs(InputOutput())

    shares = input_df["shares"]
    if shares.gt(0).all() or shares.lt(0).all() or shares.eq(0).any():
        raise ValueError("Problem with shares sizes.")

    objects = get_db_df_and_make_single_leg_fin_insts(input_df)

    settings = input_df.iloc[0]
    delay = float(settings["delay"])
    executions_remaining = int(settings["executions"])
    current_profit_margin = float(settings["initial_profit_margin"])
    profit_margin_increment = float(settings["profit_margin_increment"])
    strategy_type = STRATEGY_TYPES[settings["strat_name"]]

    try:
        await ibkr.connect()
        print("IBKR connected:", ibkr.ib.isConnected(), "\n")

        await asyncio.gather(*(ibkr.create_simple_contract(obj) for obj in objects))
        await asyncio.gather(*(ibkr.complete_obj(obj) for obj in objects))

        for obj in objects:
            attach_trading_attributes(obj, input_df)
            obj.platform_obj = ibkr

        while executions_remaining > 0:
            for obj in objects:
                obj.size = obj.remaining_trading_size // executions_remaining
                obj.actively_updating_mkt_data = False
                obj.need_to_save_closing_price = True

            strategy = strategy_type(current_profit_margin, objects)

            stream_task = asyncio.create_task(ibkr.start_streams(objects))

            await strategy.done_event.wait()

            executions_remaining -= 1
            current_profit_margin += profit_margin_increment
            await asyncio.sleep(delay)

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
