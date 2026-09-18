"""Prepare statistical arbitrage scalars and export them to Excel."""

import asyncio
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import xlwings as xw

sys.path.append(str(Path(__file__).resolve().parent.parent))

from fin_insts.Make_Single_Leg_Fin_Insts import get_db_df_and_make_single_leg_fin_insts
from input_output.Class_InputOutput import InputOutput
from ibkr.Class_IBKR_IB import IBKR_IB

IBKR_PORT = 7496

STRAT_WB_NAME = "2026 Group Trading Inputs.xlsm"
STRAT_WS_NAME = "ADMIN INPUTS"
STRAT_TBL_NAME = STRAT_WS_NAME.replace(" ", "_")

DIV_PATH = (
    Path(__file__).resolve().parent.parent
    / "spreadsheets"
    / "2026 Fin Inst Database.xlsx"
)

START_DATE = date(2026, 1, 1)   # update annually
CURRENT_DATE = date.today()
CURRENT_MONTH = CURRENT_DATE.month

def attach_dividends(df: pd.DataFrame, row: pd.Series) -> pd.DataFrame:
    """Attach dividend payments and their cumulative sum to dated prices."""
    df = df.copy()
    df.index = pd.Index(pd.to_datetime(df.index).date, name=df.index.name)
    df["div"] = 0.0
    df["div_adj"] = 0.0

    for month in range(1, CURRENT_MONTH + 1):
        div_col = f"div 2026-{month:02d}"
        ex_date_col = f"{div_col} ex-date"

        ex_date = pd.to_datetime(row[ex_date_col], errors="coerce")
        amount = pd.to_numeric(row[div_col], errors="coerce")
        if pd.isna(ex_date) or pd.isna(amount):
            continue

        ex_date = ex_date.date()
        df.loc[df.index == ex_date, "div"] = float(amount)

    df["div_cumsum"] = df["div"].cumsum()

    return df


def align_dividend_adjustments(anchor_df, sym_df, anchor_row, sym_row):
    """Adjust the earlier payer from its ex-date until the other leg's ex-date."""
    for month in range(1, CURRENT_MONTH + 1):
        div_column = f"div 2026-{month:02d}"
        date_column = f"{div_column} ex-date"
        
        anchor_date, sym_date = pd.to_datetime(
            [anchor_row[date_column], sym_row[date_column]],
            errors="coerce",
            format="mixed",
        ).date

        if pd.isna(anchor_date) or pd.isna(sym_date) or anchor_date == sym_date:
            continue

        if anchor_date < sym_date:
            prices, dividends = anchor_df, anchor_row
            start, end = anchor_date, sym_date
        else:
            prices, dividends = sym_df, sym_row
            start, end = sym_date, anchor_date

        amount = pd.to_numeric(dividends[div_column], errors="coerce")
        if pd.notna(amount):
            mask = (prices.index >= start) & (prices.index < end)
            prices.loc[mask, "div_adj"] = float(amount)


def calculate_scalars(df, hist_prices_df, rows):
    """Return instrument inputs with multipliers and dividend adjustments."""
    df = df.assign(multiplier=float("nan"), div_adj=float("nan"))

    for idx, row in df.iterrows():
        sym = row["my_fi_name"]
        anchor = row["anchor"]

        if sym == anchor:
            df.at[idx, "multiplier"] = 1.0
            continue

        div_treatment = int(row["div_treatment"])

        if div_treatment == 0:
            anchor_adj_prices = hist_prices_df[anchor]
            sym_adj_prices = hist_prices_df[sym]

        else:
            anchor_row = rows.loc[anchor]
            anchor_df = attach_dividends(hist_prices_df[[anchor]], anchor_row)

            sym_row = rows.loc[sym]
            sym_df = attach_dividends(hist_prices_df[[sym]], sym_row)

            if div_treatment == 1:
                align_dividend_adjustments(anchor_df, sym_df, anchor_row, sym_row)
                adjustment_column = "div_adj"
            elif div_treatment == 2:
                adjustment_column = "div_cumsum"
            else:
                raise ValueError(f"Unsupported dividend treatment for {sym}: {div_treatment}")

            anchor_adj_prices = anchor_df[anchor] + anchor_df[adjustment_column]
            sym_adj_prices = sym_df[sym] + sym_df[adjustment_column]
            df.at[idx, "div_adj"] = sym_df[adjustment_column].iloc[-1]
            anchor_idx = df.index[df["my_fi_name"] == anchor][0]
            df.at[anchor_idx, "div_adj"] = anchor_df[adjustment_column].iloc[-1]

        ma_days = int(row["moving_avg_days"])
        ratio = anchor_adj_prices / sym_adj_prices
        df.at[idx, "multiplier"] = ratio.rolling(ma_days).mean().iloc[-2] # Use the second-to-last value to avoid using today's price in the calculation

    return df


async def stat_arb_prep():
    """Read inputs, fetch market data, and write the calculated scalars."""
    io = InputOutput()

    wb, ws = io.set_xw_book_and_sheet(STRAT_WB_NAME, STRAT_WS_NAME)
    input_dict = io.get_xw_dict(ws, STRAT_TBL_NAME, table=True)

    ws = io.set_xw_sheet(wb, input_dict["FIs_sheet"])
    df = io.get_xw_df(ws, input_dict["FIs_table"], table=True)

    objs_list = get_db_df_and_make_single_leg_fin_insts(df)

    ibkr = IBKR_IB(port=IBKR_PORT)
    try:
        await ibkr.connect()
        print("IBKR connected:", ibkr.ib.isConnected(), "\n")

        await asyncio.gather(*(ibkr.create_simple_contract(obj) for obj in objs_list))
        await asyncio.gather(*(ibkr.complete_obj(obj) for obj in objs_list))

        # Fetch prices with a date index for dividend alignment.

        contracts = [obj.ibkr_contract for obj in objs_list]
        hist_prices_df = await ibkr.get_historical_closes_df(contracts, remove_today=False)
        hist_prices_df.index = pd.Index(
            pd.to_datetime(hist_prices_df.index).date, name=hist_prices_df.index.name
        )
        hist_prices_df = hist_prices_df.loc[hist_prices_df.index >= START_DATE].sort_index()

        div_wb = xw.Book(DIV_PATH)
        ws = io.set_xw_sheet(div_wb, "Scalar Inputs Table")
        div_df = ws.tables["scalar_inputs_table"].range.options(
            pd.DataFrame, header=1, index=False
        ).value
        rows = div_df.set_index("symbol")

        df = calculate_scalars(df, hist_prices_df, rows)

        adv_df = await ibkr.get_avg_daily_volume_df(contracts)
        df = df.merge(adv_df, how="left", left_on="my_fi_name", right_on="symbol")
        df = df.drop(columns=["moving_avg_days", "div_treatment", "symbol"])

        _, output_range = io.set_xw_sheet_and_range(
            wb,
            input_dict["scalar_outputs_sheet"],
            input_dict["scalar_outputs_download_cell"],
        )
        io.print_xw_df(output_range, df)
    finally:
        ibkr.ib.disconnect()

    print("\nFinished\n")


if __name__ == "__main__":
    asyncio.run(stat_arb_prep())
