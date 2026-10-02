"""Prepare statistical arbitrage scalars and export them to Excel."""

import asyncio
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import xlwings as xw

sys.path.append(str(Path(__file__).resolve().parent.parent))

from fin_insts.Make_Single_Leg_Fin_Insts import get_db_df_and_make_single_leg_fin_insts
from ibkr.Class_IBKR_IB import IBKR_IB
from input_output.Class_InputOutput import InputOutput

IBKR_PORT = 7496

STRAT_WB_NAME = "2026 Group Trading Inputs.xlsm"
STRAT_WS_NAME = "ADMIN INPUTS"
STRAT_TBL_NAME = STRAT_WS_NAME.replace(" ", "_")

START_DATE = date(2026, 1, 1)   # update annually
CURRENT_DATE = date.today()
CURRENT_MONTH = CURRENT_DATE.month

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "spreadsheets"

DIV_PATH = OUTPUT_PATH / "2026 Fin Inst Database.xlsx"

SCALAR_PATH = OUTPUT_PATH / "scalar calculations" / CURRENT_DATE.isoformat()



def dividend_columns():
    """Yield the configured monthly dividend and ex-date column names."""
    for month in range(1, CURRENT_MONTH + 1):
        column = f"div {START_DATE.year}-{month:02d}"
        yield column, f"{column} ex-date"


def normalize_price_dates(df: pd.DataFrame) -> pd.DataFrame:
    """Use calendar dates as the price index, preserving its name."""
    df.index = pd.Index(pd.to_datetime(df.index).date, name=df.index.name)
    return df


def load_prep_inputs(io: InputOutput):
    """Read preparation settings and instruments from the strategy workbook."""
    workbook, sheet = io.set_xw_book_and_sheet(STRAT_WB_NAME, STRAT_WS_NAME)
    inputs = io.get_xw_dict(sheet, STRAT_TBL_NAME, table=True)
    sheet = io.set_xw_sheet(workbook, inputs["FIs_sheet"])
    instruments = io.get_xw_df(sheet, inputs["FIs_table"], table=True)
    return workbook, inputs, instruments


def load_dividend_rows(io: InputOutput) -> pd.DataFrame:
    """Read dividend inputs indexed by instrument symbol."""
    workbook = xw.Book(DIV_PATH)
    sheet = io.set_xw_sheet(workbook, "Scalar Inputs Table")
    dividends = sheet.tables["scalar_inputs_table"].range.options(
        pd.DataFrame, header=1, index=False
    ).value
    return dividends.set_index("symbol")


def attach_dividends(
    df: pd.DataFrame, row: pd.Series, div_treatment: int
) -> pd.DataFrame:
    """Attach dividend payments and their cumulative sum to dated prices."""
    df = df.assign(div=0.0, div_cumsum=0.0, div_adj=0.0)
    df = normalize_price_dates(df)

    if div_treatment > 0:
        for div_col, ex_date_col in dividend_columns():
            ex_date = pd.to_datetime(row[ex_date_col], errors="coerce")
            amount = pd.to_numeric(row[div_col], errors="coerce")
            if pd.isna(ex_date) or pd.isna(amount):
                continue

            ex_date = ex_date.date()
            df.loc[df.index == ex_date, "div"] = float(amount)

    df["div_cumsum"] = df["div"].cumsum()

    return df


def align_dividend_adjustments(
    anchor_df: pd.DataFrame,
    sym_df: pd.DataFrame,
    anchor_row: pd.Series,
    sym_row: pd.Series,
) -> None:
    """Adjust the earlier payer from its ex-date until the other leg's ex-date."""
    for div_column, date_column in dividend_columns():
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


def calculate_scalars(
    df: pd.DataFrame, hist_prices_df: pd.DataFrame, rows: pd.DataFrame
) -> pd.DataFrame:
    """Return instrument inputs with multipliers and dividend adjustments."""
    df = df.assign(multiplier=float("nan"), div_adj=float("nan"))
    SCALAR_PATH.mkdir(parents=True, exist_ok=True)

    for idx, row in df.iterrows():
        sym = row["my_fi_name"]
        anchor = row["anchor"]

        if sym == anchor:
            df.at[idx, "multiplier"] = 1.0
            continue

        div_treatment = int(row["div_treatment"])

        anchor_row = rows.loc[anchor]
        anchor_df = attach_dividends(hist_prices_df[[anchor]], anchor_row, div_treatment)

        sym_row = rows.loc[sym]
        sym_df = attach_dividends(hist_prices_df[[sym]], sym_row, div_treatment)

        if div_treatment == 0:
            adjustment_column = "div"  # Dividend values are zero for this treatment.
        elif div_treatment == 1:
            align_dividend_adjustments(anchor_df, sym_df, anchor_row, sym_row)
            adjustment_column = "div_adj"
        elif div_treatment == 2:
            adjustment_column = "div_cumsum"
        else:
            raise ValueError(f"Unsupported dividend treatment for {sym}: {div_treatment}")

        anchor_df[f"{anchor}*"] = anchor_df[anchor] + anchor_df[adjustment_column]
        sym_df[f"{sym}*"] = sym_df[sym] + sym_df[adjustment_column]

        export_df = sym_df.join(anchor_df, lsuffix=f"_{sym}", rsuffix=f"_{anchor}")

        export_df["ratio*"] = export_df[f"{anchor}*"] / export_df[f"{sym}*"]

        ma_days = int(row["moving_avg_days"])
        export_df["ratio*_ma"] = export_df["ratio*"].rolling(ma_days).mean()

        export_path = SCALAR_PATH / f"{sym}_{anchor}_{div_treatment}.csv"
        export_df.rename_axis("date").reset_index().to_csv(export_path, index=False)

        df.at[idx, "div_adj"] = sym_df[adjustment_column].iloc[-1]

        anchor_idx = df.index[df["my_fi_name"] == anchor][0]
        df.at[anchor_idx, "div_adj"] = anchor_df[adjustment_column].iloc[-1]

        # Exclude today's price by using the second-to-last moving average.
        df.at[idx, "multiplier"] = export_df["ratio*_ma"].iloc[-2]

    return df


async def stat_arb_prep() -> None:
    """Read inputs, fetch market data, and write the calculated scalars."""
    io = InputOutput()

    workbook, inputs, df = load_prep_inputs(io)

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
        hist_prices_df = normalize_price_dates(hist_prices_df)
        hist_prices_df = hist_prices_df.loc[hist_prices_df.index >= START_DATE].sort_index()

        rows = load_dividend_rows(io)
        df = calculate_scalars(df, hist_prices_df, rows)

        adv_df = await ibkr.get_avg_daily_volume_df(contracts)
        df = df.merge(
            adv_df, how="left", left_on="my_fi_name", right_on="symbol"
        ).drop(columns=["moving_avg_days", "div_treatment", "symbol"])

        _, output_range = io.set_xw_sheet_and_range(
            workbook,
            inputs["scalar_outputs_sheet"],
            inputs["scalar_outputs_download_cell"],
        )
        io.print_xw_df(output_range, df)
    finally:
        ibkr.ib.disconnect()

    print("\nFinished\n")


if __name__ == "__main__":
    asyncio.run(stat_arb_prep())
