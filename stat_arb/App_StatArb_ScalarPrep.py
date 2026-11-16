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
    "pairs_category",
    "pair_name",
    "sym1",
    "sym2",
    "moving_avg_days",
    "div_method",
    "neg_entry",
    "neg_exit",
    "pos_entry",
    "pos_exit"
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
    sym: str, 
    prices: pd.DataFrame, 
    divs: pd.DataFrame, 
    method: int
) -> pd.DataFrame:
    """Attach dividend payments and their cumulative sum to dated prices."""
    prices = prices.assign(div=0.0, div_cumsum=0.0)
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

    prices = prices[["div", "div_cumsum", sym]]
    prices[f"{sym}+div"] = prices["div"] + prices[sym]
    prices["rtns"] = np.log(prices[f"{sym}+div"] / prices[sym].shift(1))
    prices["div_adj"] = 0.0

    return prices


def align_dividend_adjustments(
    sym1: str,
    sym2: str,    
    sym1_prices: pd.DataFrame,
    sym2_prices: pd.DataFrame,
    divs: pd.DataFrame,
) -> None:
    """Adjust the earlier payer from its ex-date until the other leg's ex-date."""
    sym1_row = divs.loc[divs["my_fi_name"] == sym1].iloc[0]
    sym2_row = divs.loc[divs["my_fi_name"] == sym2].iloc[0]

    for div_col, date_col in dividend_columns():
        sym1_date, sym2_date = pd.to_datetime(
            [sym1_row[date_col], sym2_row[date_col]],
            errors="coerce",
            format="mixed",
        ).date

        if pd.isna(sym1_date) or pd.isna(sym2_date) or sym1_date == sym2_date:
            continue

        if sym1_date > sym2_date:
            prices, row = sym2_prices, sym2_row
            start, end = sym2_date, sym1_date
        else:
            prices, row = sym1_prices, sym1_row
            start, end = sym1_date, sym2_date

        amount = pd.to_numeric(row[div_col], errors="coerce")
        if pd.notna(amount):
            mask = (prices.index >= start) & (prices.index < end)
            prices.loc[mask, "div_adj"] = float(amount)


def add_stats(df: pd.DataFrame, sym1: str, sym2: str) -> pd.DataFrame:
    """Add sym1/sym2 log returns and summary statistics to the price export."""

    ratio = df["scalar"]
    log_prices = df["ln(unit_prices)"]

    ratio_min, ratio_max = ratio.min(), ratio.max()
    log_min, log_max = log_prices.min(), log_prices.max()

    stats = {
        "analysis_date": pd.to_datetime(df.index).max().date(),
        "corr_of_mkt_rtns": df[f"rtns_{sym1}"].corr(df[f"rtns_{sym2}"]),
        "scalar_min": ratio_min,
        "scalar_max": ratio_max,
        "scalar_range": np.log(ratio_max / ratio_min),
        "unit_price_ratio_min": log_min,
        "unit_price_ratio_max": log_max,
        "unit_price_ratio_range": log_max - log_min,
        "unit_price_ratio_stdev": log_prices.std(),
    }

    df["stat_label"] = None
    df["stat"] = pd.Series(index=df.index, dtype=object)

    for idx, (label, value) in zip(df.index, stats.items()):
        df.at[idx, "stat_label"] = label
        df.at[idx, "stat"] = value

    return df


def calculate_scalars(
    scalars: pd.DataFrame, 
    prices: pd.DataFrame, 
    divs: pd.DataFrame
) -> pd.DataFrame:
    """Calculate each sym1/sym2 pair's scalar and dividend adjustments."""
    scalars = scalars.assign(corr_of_mkt_rtns=np.nan, scalar_range=np.nan, unit_price_ratio_range=np.nan,
                             sym1_div_adj=np.nan, sym2_div_adj=np.nan, 
                             sym1_scalar=np.nan, sym2_scalar=1)
    SCALAR_PATH.mkdir(parents=True, exist_ok=True)
    adjustment_cols = {0: "div", 1: "div_adj", 2: "div_cumsum"}

    for idx, row in scalars.iterrows():
        sym1, sym2 = row["sym1"], row["sym2"]

        method = int(row["div_method"])
        if method not in adjustment_cols:
            raise ValueError(f"Unsupported dividend method for {sym1}/{sym2}: {method}")
        
        adj_col = adjustment_cols[method]

        sym1_prices = attach_dividends(sym1, prices[[sym1]], divs, method)
        sym2_prices = attach_dividends(sym2, prices[[sym2]], divs, method)

        if method == 1:
            align_dividend_adjustments(sym1, sym2, sym1_prices, sym2_prices, divs)

        for sym, leg in ((sym1, sym1_prices), (sym2, sym2_prices)):
            leg[f"{sym}*"] = leg[sym] + leg[adj_col]
            leg.iloc[-1, leg.columns.get_loc(f"{sym}*")] = np.nan

        df = sym1_prices.join(sym2_prices, lsuffix=f"_{sym1}", rsuffix=f"_{sym2}")
        df["ratio*"] = df[f"{sym2}*"] / df[f"{sym1}*"]

        days = int(row["moving_avg_days"])
        df["scalar"] = df["ratio*"].rolling(days).mean()

        df[f"{sym1}* x scalar"] = df[f"{sym1}*"] * df["scalar"].shift(1)

        ratio = df[f"{sym1}* x scalar"] / df[f"{sym2}*"]
        df["ln(unit_prices)"] = np.log(ratio.where(ratio > 0))

        df = add_stats(df, sym1, sym2)

        path = SCALAR_PATH / f"{sym1}_{sym2}_{method}.csv"
        df.rename_axis("date").to_csv(path)

        labels = ["corr_of_mkt_rtns", "scalar_range", "unit_price_ratio_range"]#, "unit_price_ratio_stdev"]
        for label in labels:
            scalars.at[idx, label] = df.loc[df["stat_label"] == label, "stat"].iloc[0]

        scalars.at[idx, "sym1_div_adj"] = sym1_prices[adj_col].iloc[-1]
        scalars.at[idx, "sym2_div_adj"] = sym2_prices[adj_col].iloc[-1]
        
        # Preserve the existing exclusion of the most recent price row.
        scalars.at[idx, "sym1_scalar"] = df["scalar"].iloc[-2]

    return scalars


async def stat_arb_scalar_prep() -> None:
    """Read inputs, fetch market data, and write the calculated scalars."""
    io = InputOutput()

    db = get_db_df()
    workbook, inputs, scalars = load_prep_inputs(io)

    symbols = pd.concat(
        [scalars["sym1"], scalars["sym2"]]
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
