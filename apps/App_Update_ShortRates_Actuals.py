# %%
import requests
import pandas as pd
import xlwings as xw


def get_latest_effr_sofr_df():
    url = "https://markets.newyorkfed.org/api/rates/all/latest.json"

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
    }

    response = requests.get(url, headers=headers, timeout=15)
    response.raise_for_status()

    data = response.json()["refRates"]

    df = pd.DataFrame(
        row for row in data
        if row["type"] in ["EFFR", "SOFR"]
    )

    row_order = {
        "EFFR": 0,
        "SOFR": 1,
    }

    df["row_order"] = df["type"].map(row_order)

    df = (
        df.sort_values("row_order")
          [["type", "effectiveDate", "percentRate"]]
          .reset_index(drop=True)
    )

    df["effectiveDate"] = pd.to_datetime(
        df["effectiveDate"]
    ).dt.date

    return df


def publish_latest_rates():
    df = get_latest_effr_sofr_df()

    # Refers to the workbook that called this function
    wb = xw.Book.caller()
    ws = wb.sheets["OUTPUT"]

    ws.range("Y7").options(
        index=False,
        header=False,
    ).value = df

    wb.save()


