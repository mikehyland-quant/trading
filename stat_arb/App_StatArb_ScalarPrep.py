"""Prepare statistical arbitrage scalars and export them to Excel."""

import asyncio
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd


sys.path.append(str(Path(__file__).resolve().parent.parent))

from fin_insts.Make_Single_Leg_Fin_Insts import get_db_df, make_single_leg_fin_insts
from ibkr.Class_IBKR_IB import IBKR_IB
from input_output.Class_InputOutput import InputOutput

IBKR_PORT = 7496

STRAT_WB_NAME = "2026 Pairs Trading.xlsm"
STRAT_WS_NAME = "INITIAL INPUTS"
STRAT_TBL_NAME = STRAT_WS_NAME.replace(" ", "_")

INPUT_COLS = [
    "category1",
    "category2",
    "category3",
    "pair_name",
    "lo_px_sym",
    "hi_px_sym",
    "moving_avg_days",
    "div_method",
]

START_DATE = date(2026, 1, 1)   # update annually
CURRENT_DATE = date.today()
CURRENT_MONTH = CURRENT_DATE.month

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "spreadsheets"

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
    sheet = io.set_xw_sheet(workbook, inputs["scalar_input_sheet"])
    df = io.get_xw_df(sheet, inputs["scalar_input_table"], table=True)
    df = df[INPUT_COLS]
    return workbook, inputs, df


def attach_dividends(
    sym: str, prices: pd.DataFrame, divs: pd.DataFrame, method: int
) -> pd.DataFrame:
    """Attach dividend payments and their cumulative sum to dated prices."""
    prices = prices.assign(div=0.0, div_cumsum=0.0, div_adj=0.0)
    prices = normalize_price_dates(prices)

    if method > 0:
        row = divs.loc[divs["my_fi_name"] == sym].iloc[0]
        for div_col, ex_date_col in dividend_columns():
            ex_date = pd.to_datetime(row[ex_date_col], errors="coerce")
            amount = pd.to_numeric(row[div_col], errors="coerce")
            if pd.isna(ex_date) or pd.isna(amount):
                continue

            ex_date = ex_date.date()
            prices.loc[prices.index == ex_date, "div"] = float(amount)

    prices["div_cumsum"] = prices["div"].cumsum()

    return prices


def align_dividend_adjustments(
    lo_prices: pd.DataFrame,
    hi_prices: pd.DataFrame,
    divs: pd.DataFrame,
    lo: str,
    hi: str,
) -> None:
    """Adjust the earlier payer from its ex-date until the other leg's ex-date."""
    hi_row = divs.loc[divs["my_fi_name"] == hi].iloc[0]
    lo_row = divs.loc[divs["my_fi_name"] == lo].iloc[0]

    for div_col, date_col in dividend_columns():
        hi_date, lo_date = pd.to_datetime(
            [hi_row[date_col], lo_row[date_col]],
            errors="coerce",
            format="mixed",
        ).date

        if pd.isna(hi_date) or pd.isna(lo_date) or hi_date == lo_date:
            continue

        if hi_date < lo_date:
            prices, row = hi_prices, hi_row
            start, end = hi_date, lo_date
        else:
            prices, row = lo_prices, lo_row
            start, end = lo_date, hi_date

        amount = pd.to_numeric(row[div_col], errors="coerce")
        if pd.notna(amount):
            mask = (prices.index >= start) & (prices.index < end)
            prices.loc[mask, "div_adj"] = float(amount)


def calculate_scalars(
    scalars: pd.DataFrame, prices: pd.DataFrame, divs: pd.DataFrame
) -> pd.DataFrame:
    """Calculate each lo/hi pair's multiplier and dividend adjustments."""
    scalars = scalars.assign(multiplier=np.nan, lo_div_adj=np.nan, hi_div_adj=np.nan)
    SCALAR_PATH.mkdir(parents=True, exist_ok=True)
    adjustment_cols = {0: "div", 1: "div_adj", 2: "div_cumsum"}

    for idx, row in scalars.iterrows():
        lo, hi = row["lo_px_sym"], row["hi_px_sym"]

        method = int(row["div_method"])
        if method not in adjustment_cols:
            raise ValueError(f"Unsupported dividend method for {lo}/{hi}: {method}")
        
        adj_col = adjustment_cols[method]

        lo_prices = attach_dividends(lo, prices[[lo]], divs, method)
        hi_prices = attach_dividends(hi, prices[[hi]], divs, method)

        if method == 1:
            align_dividend_adjustments(lo_prices, hi_prices, divs, lo, hi)

        for sym, leg in ((lo, lo_prices), (hi, hi_prices)):
            leg[f"{sym}*"] = leg[sym] + leg[adj_col]
            leg.iloc[-1, leg.columns.get_loc(f"{sym}*")] = np.nan

        df = lo_prices.join(hi_prices, lsuffix=f"_{lo}", rsuffix=f"_{hi}")
        df["ratio*"] = df[f"{hi}*"] / df[f"{lo}*"]

        days = int(row["moving_avg_days"])
        df["ratio*_ma"] = df["ratio*"].rolling(days).mean()

        df[f"{lo}* x ratio*_ma"] = df[f"{lo}*"] * df["ratio*_ma"].shift(1)

        ratio = df[f"{lo}* x ratio*_ma"] / df[f"{hi}*"]
        df["ln(unit_prices)"] = np.log(ratio.where(ratio > 0))

        df = add_stats(df, lo, hi)

        path = SCALAR_PATH / f"{lo}_{hi}_{method}.csv"
        df.rename_axis("date").to_csv(path)

        scalars.at[idx, "lo_div_adj"] = lo_prices[adj_col].iloc[-1]
        scalars.at[idx, "hi_div_adj"] = hi_prices[adj_col].iloc[-1]
        
        # Preserve the existing exclusion of the most recent price row.
        scalars.at[idx, "multiplier"] = df["ratio*_ma"].iloc[-2]

    return scalars


def add_stats(df: pd.DataFrame, lo: str, hi: str) -> pd.DataFrame:
    """Add lo/hi log returns and summary statistics to the price export."""
    for name in (lo, hi):
        prices = df[f"{name}*"]
        df[f"log_chg({name}*)"] = np.log(prices / prices.shift(1))

    ratio = df["ratio*_ma"]
    log_prices = df["ln(unit_prices)"]

    ratio_min, ratio_max = ratio.min(), ratio.max()
    log_min, log_max = log_prices.min(), log_prices.max()

    stats = {
        "analysis_date": pd.to_datetime(df.index).max().date(),
        "corr(log_chg*)": df[f"log_chg({lo}*)"].corr(df[f"log_chg({hi}*)"]),
        "min(ratio*_ma)": ratio_min,
        "max(ratio*_ma)": ratio_max,
        "log_diff(ratio*_ma)": np.log(ratio_max / ratio_min),
        "min(ln(unit_prices))": log_min,
        "max(ln(unit_prices))": log_max,
        "diff(ln(unit_prices))": log_max - log_min,
        "stdev(ln(unit_prices))": log_prices.std(),
    }

    df["stat_label"] = None
    df["stat"] = pd.Series(index=df.index, dtype=object)

    for idx, (label, value) in zip(df.index, stats.items()):
        df.at[idx, "stat_label"] = label
        df.at[idx, "stat"] = value

    return df


async def stat_arb_scalar_prep() -> None:
    """Read inputs, fetch market data, and write the calculated scalars."""
    io = InputOutput()

    db = get_db_df()
    workbook, inputs, scalars = load_prep_inputs(io)

    symbols = pd.concat(
        [scalars["lo_px_sym"], scalars["hi_px_sym"]]
    ).dropna()

    db = db.loc[db["my_fi_name"].isin(symbols)].copy()

    objs = make_single_leg_fin_insts(db)

    ibkr = IBKR_IB(port=IBKR_PORT)
    try:
        await ibkr.connect()
        print("IBKR connected:", ibkr.ib.isConnected(), "\n")

        await asyncio.gather(*(ibkr.create_simple_contract(obj) for obj in objs))
        await asyncio.gather(*(ibkr.complete_obj(obj) for obj in objs))

        # Fetch prices with a date index for dividend alignment.
        contracts = [obj.ibkr_contract for obj in objs]
        prices = await ibkr.get_historical_closes_df(contracts, remove_today=False)
        prices = normalize_price_dates(prices)
        prices = prices.loc[prices.index >= START_DATE].sort_index()

        output = calculate_scalars(scalars, prices, db)

        _, rng = io.set_xw_sheet_and_range(
            workbook,
            inputs["scalar_outputs_sheet"],
            inputs["scalar_outputs_download_cell"],
        )
        io.print_xw_df(rng, output)
    finally:
        ibkr.ib.disconnect()

    print("\nFinished\n")


if __name__ == "__main__":
    asyncio.run(stat_arb_scalar_prep())
