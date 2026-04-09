from datastream.preprocessing.raw_data_processing import *
import os

data_path = "D:\\Datastream\\PriceData\\US"

# --- Static data ---
logging.info("Starting to import static data.")
static_df = pd.concat([
    pd.read_excel(os.path.join(data_path, f"{i:02d}\\STATIC_{i:02d}.xlsx"), engine="openpyxl")
    for i in range(1, 40)
]).reset_index(drop=True)
static_df.to_csv(os.path.join(data_path, "processed", "statics.csv"), index=False)
logging.info("Static dataframe has been stored.")

# Config: (file_prefix, output_column_name)
VARIABLE_CONFIG = [
    ("AF",   "AdjFactor"),
    ("MTBV",   "MTBV"),
    ("MV",   "MarketCAP"),
    ("P",  "Close"),
    ("PH", "High"),
    ("PL",  "Low"),
    ("PO",  "Open"),
    ("RI",    "ReturnIndex"),
    ("UP",   "UnadjClose"),
    ("VO",   "Volume")
]

# --- Panel data ---
logging.info("Starting to create panel data sets.")
for i in range(1, 40):
    folder_name = f"{i:02d}"
    folder_path = os.path.join(data_path, folder_name)

    logging.info(f"Loading, cleaning and melting data for folder {folder_name}.")

    # load, clean, melt — then merge
    panels = []
    unmatched_ids_all = []
    for prefix, value_name in VARIABLE_CONFIG:
        path = os.path.join(folder_path, f"{prefix}_{folder_name}.xlsx")
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


    df_panel.to_feather(os.path.join(data_path, "processed", f"panel_{folder_name}.feather"))
    logging.info(f"Panel data for folder {folder_name} saved.")