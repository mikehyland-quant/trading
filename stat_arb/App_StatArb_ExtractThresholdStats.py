"""Extract saved scalar statistics for each pair and export them to Excel."""

import asyncio
import sys
import pandas as pd
from datetime import date
from pathlib import Path



sys.path.append(str(Path(__file__).resolve().parent.parent))

from input_output.Class_InputOutput import InputOutput


STRAT_WB_NAME = "2026 Pairs Trading.xlsm"
STRAT_WS_NAME = "INITIAL INPUTS"
STRAT_TBL_NAME = STRAT_WS_NAME.replace(" ", "_")

CURRENT_DATE = date.today()

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "spreadsheets"


SCALAR_PATH = OUTPUT_PATH / "scalar calculations" / CURRENT_DATE.isoformat()

def load_prep_inputs(io: InputOutput):
    """Read pair inputs and resolve the configured output destination."""
    workbook, sheet = io.set_xw_book_and_sheet(STRAT_WB_NAME, STRAT_WS_NAME)
    inputs = io.get_xw_dict(sheet, STRAT_TBL_NAME, table=True)

    sheet = io.set_xw_sheet(workbook, inputs["threshold_sheet"])

    df = io.get_xw_df(sheet, inputs["threshold_input_range"])

    return inputs, df


def collect_threshold_stats(df: pd.DataFrame, scalar_path: Path) -> pd.DataFrame:
    """Append statistics to each pair, preserving input order and blank rows."""
    pair_columns = ["my_fi_name", "anchor", "div_method"]
    missing = set(pair_columns) - set(df.columns)
    if missing:
        raise ValueError(f"Missing pair input columns: {sorted(missing)}")
    
    records = []
    for index, row in df.iterrows():
        if row[pair_columns].isna().all():
            records.append({})
            continue

        if row[pair_columns].isna().any():
            raise ValueError(f"Incomplete pair inputs at DataFrame index {index!r}")
        
        method = float(row["div_method"])
        filename = f"{row['my_fi_name']}_{row['anchor']}_{int(method)}.csv"
        path = scalar_path / filename

        if not path.is_file():
            records.append({})
            continue

        stats = pd.read_csv(path, usecols=["stat_label", "stat"])
        stats = stats.dropna(subset=["stat_label"])

        if stats["stat_label"].duplicated().any():
            raise ValueError(f"Duplicate statistic labels in {path}")
        
        values = stats.set_index("stat_label")["stat"]

        # The CSV stat column contains both a date and numbers.
        records.append({
            label: value if label == "analysis_date" else pd.to_numeric(value)
            for label, value in values.items()
        })

    extracted = pd.DataFrame(records, index=df.index)
    collisions = df.columns.intersection(extracted.columns)
    if len(collisions):
        raise ValueError(f"Statistic labels overlap input columns: {list(collisions)}")
    
    return pd.concat([df, extracted], axis=1)


async def extract_threshold_stats() -> None:
    """Read today's saved statistics and write the complete output once."""
    io = InputOutput()
    inputs, df = load_prep_inputs(io)
    result = collect_threshold_stats(df, SCALAR_PATH)

    wb, ws = io.set_xw_book_and_sheet(STRAT_WB_NAME, inputs['threshold_sheet'])
    output_range = io.set_xw_range(ws, inputs['threshold_output_cell'])
    
    io.print_xw_df(output_range, result)
    print("\nFinished\n")


if __name__ == "__main__":
    asyncio.run(extract_threshold_stats())
