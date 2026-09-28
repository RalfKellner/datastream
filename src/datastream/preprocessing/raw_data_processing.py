import pandas as pd
import os
import re
import logging
logging.basicConfig(format='%(asctime)s : %(levelname)s : %(message)s', level=logging.INFO)


def df_to_numeric(df_tmp):
    cols = df_tmp.columns.difference(["Date"])
    return df_tmp.assign(
        **{col: pd.to_numeric(df_tmp[col], errors="coerce") for col in cols}
    )

def fix_id_columns(df: pd.DataFrame, date_col: str = "Date"):
    def parse_id_from_col(col_name):
        match = re.search(r"DPL#\(([^(\s]+)\(", col_name)
        return match.group(1) if match else None

    new_columns, unmatched = {}, []
    for col in df.columns:
        if col == date_col:
            new_columns[col] = col
        else:
            parsed = parse_id_from_col(col)
            new_columns[col] = parsed if parsed else col
            if not parsed:
                unmatched.append(col)
    return df.rename(columns=new_columns), unmatched

def melt_dataframe(df, value_name):
    return df.melt(id_vars='Date', var_name='DSCD', value_name=value_name)

def load_and_prepare(path: str, value_name: str, max_date=None) -> pd.DataFrame:
    """Load one Excel file, clean it, and return a melted panel."""
    df = pd.read_excel(path, engine='openpyxl')
    error_cols = df.columns.astype(str).str.startswith('#ERROR')
    if error_cols.any():
        # Datastream returns '#ERROR' columns for series it could not deliver; these stocks are
        # dropped here, so their number is logged to make the loss visible.
        logging.warning(f"{os.path.basename(path)}: {int(error_cols.sum())} '#ERROR' columns dropped.")
    df = df.loc[:, ~error_cols]
    df = df.iloc[2:].copy()
    df.rename(columns={df.columns[0]: "Date"}, inplace=True)
    df["Date"] = pd.to_datetime(df["Date"])
    if max_date is not None:
        df = df[df["Date"] <= max_date]
    n_before = int(df.drop(columns="Date").notna().sum().sum())
    df = df_to_numeric(df)
    n_after = int(df.drop(columns="Date").notna().sum().sum())
    if n_after < n_before:
        # non-numeric cells (e.g. '$$ER' error strings) were converted to NaN
        logging.warning(f"{os.path.basename(path)}: {n_before - n_after} non-numeric cells coerced to NaN.")
    df, unmatched = fix_id_columns(df)
    unmatched = [c for c in unmatched if not c.startswith("#ERROR")]
    return melt_dataframe(df, value_name), unmatched


