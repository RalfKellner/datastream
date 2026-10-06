"""Load the raw Datastream price files of all list folders and store one daily panel per folder.

Layout: <data-path>/<nn>/<VAR>_<nn>.xlsx with list folders 01, 02, ... and STATIC_<nn>.xlsx.
Outputs in <data-path>/processed/: statics.csv and panel_<nn>.feather (one row per DSCD and date).

    uv run python scripts/01_load_merge_panel.py                    # US (default, unchanged)
    uv run python scripts/01_load_merge_panel.py --region EU        # Europe: local + EUR series

Europe: local-currency series keep the US column names (used by the filters: stale prices, zero
returns, padded values); the EUR series (Datastream ~E, files <VAR>_EU_<nn>.xlsx) get the suffix _EUR
and are used for returns, market caps and the cross-country penny filter. For euro-area stocks both are
identical; for non-euro countries and for lines still quoted in a legacy currency (e.g. ATS, FRF) they
differ by the exchange rate.
"""
import argparse
import logging
import os
import re

import pandas as pd

from datastream.preprocessing.raw_data_processing import load_and_prepare

logging.basicConfig(format="%(asctime)s : %(levelname)s : %(message)s", level=logging.INFO)

# (file prefix, output column name)
VARIABLE_CONFIG = {
    "US": [
        ("AF", "AdjFactor"),
        ("MTBV", "MTBV"),
        ("MV", "MarketCAP"),
        ("P", "Close"),
        ("PH", "High"),
        ("PL", "Low"),
        ("PO", "Open"),
        ("RI", "ReturnIndex"),
        ("UP", "UnadjClose"),
        ("VO", "Volume"),
    ],
    "EU": [
        ("AF", "AdjFactor"),
        ("MTBV", "MTBV"),
        ("MV", "MarketCAP"),            # local currency
        ("MV_EU", "MarketCAP_EUR"),
        ("P", "Close"),
        ("PH", "High"),
        ("PL", "Low"),
        ("PO", "Open"),
        ("RI", "ReturnIndex"),          # local currency
        ("RI_EU", "ReturnIndex_EUR"),
        ("UP", "UnadjClose"),           # local currency
        ("UP_EU", "UnadjClose_EUR"),
        ("VO", "Volume"),
    ],
}
DEFAULT_PATH = {"US": "D:/Datastream/PriceData/US", "EU": "D:/Datastream/PriceData/EU"}


def list_folders(data_path: str) -> list[str]:
    """List folders named by digits only (01, 02, ...), sorted numerically."""
    names = [d for d in os.listdir(data_path)
             if os.path.isdir(os.path.join(data_path, d)) and re.fullmatch(r"\d+", d)]
    return sorted(names, key=int)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", default="US", choices=sorted(VARIABLE_CONFIG))
    ap.add_argument("--data-path", default=None, help="Root with the list folders (default per region)")
    ap.add_argument("--folders", nargs="+", default=None, help="Only these folders, e.g. 01 02 (default: all)")
    args = ap.parse_args()

    data_path = args.data_path or DEFAULT_PATH[args.region]
    config = VARIABLE_CONFIG[args.region]
    folders = args.folders or list_folders(data_path)
    os.makedirs(os.path.join(data_path, "processed"), exist_ok=True)
    logging.info(f"Region {args.region}: {len(folders)} list folders in {data_path}.")

    # --- Static data ---
    logging.info("Starting to import static data.")
    static_df = pd.concat([
        pd.read_excel(os.path.join(data_path, f, f"STATIC_{f}.xlsx"), engine="openpyxl").assign(list_folder=f)
        for f in list_folders(data_path)
    ]).reset_index(drop=True)
    static_df.to_csv(os.path.join(data_path, "processed", "statics.csv"), index=False)
    logging.info("Static dataframe has been stored.")

    # --- Panel data ---
    logging.info("Starting to create panel data sets.")
    for folder_name in folders:
        folder_path = os.path.join(data_path, folder_name)
        logging.info(f"Loading, cleaning and melting data for folder {folder_name}.")

        panels, unmatched_ids_all = [], []
        for prefix, value_name in config:
            path = os.path.join(folder_path, f"{prefix}_{folder_name}.xlsx")
            if not os.path.exists(path):
                logging.warning(f"Missing file {path}: column {value_name} will be empty for folder {folder_name}.")
                continue
            panel, unmatched = load_and_prepare(path, value_name)
            panels.append(panel)
            unmatched_ids_all.extend(unmatched)
            logging.info(f"Data for variable {prefix} in folder {folder_name} is processed.")

        if unmatched_ids_all:
            logging.warning(f"Unmatched IDs in folder {folder_path}: {unmatched_ids_all}")

        df_panel = panels[0]
        for p in panels[1:]:
            df_panel = df_panel.merge(p, on=["Date", "DSCD"], how="outer")

        # remove rows with NAs only
        value_cols = [col for col in df_panel.columns if col not in ("Date", "DSCD")]
        df_panel = df_panel.dropna(subset=value_cols, how="all")

        df_panel.reset_index(drop=True).to_feather(
            os.path.join(data_path, "processed", f"panel_{folder_name}.feather"))
        logging.info(f"Panel data for folder {folder_name} saved.")


if __name__ == "__main__":
    main()
