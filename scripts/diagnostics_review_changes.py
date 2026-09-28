"""Diagnostics for the changes on branch review/fixes-2026-09 (see CHANGES_review.md).

Compares, on your real statics file, which stocks filter (1) keeps under
  (a) the implementation on `main` (OR logic + double regex escaping),
  (b) OR logic with correct escaping   (mode="legacy_or"),
  (c) Landis & Skouras (2021)           (mode="landis", the new default),
and writes the stocks that change status to a CSV for manual inspection.

Usage (adjust data_path):
    uv run python scripts/diagnostics_review_changes.py
"""
import os
import re

import pandas as pd

from datastream.preprocessing.filter import DSPreprocess

data_path = "/Users/ralfkellner/Datastream/PriceData/US/processed"
country = "UNITED STATES"

statics = pd.read_csv(os.path.join(data_path, "statics.csv"))
statics = statics.astype({c: str for c in ["DSCD", "ENAME", "TRAC"] if c in statics.columns})
statics["DSCD"] = statics["DSCD"].str.strip()
statics = statics[statics["GEOGN"] == country]
dummy_panel = pd.DataFrame({"Stock": statics["DSCD"].unique()})

# (a) exact re-implementation of the logic on main ------------------------------------------------
import inspect
src = inspect.getsource(DSPreprocess.filter_non_common_stocks)
start = src.index(f"'{country}':")
end = src.index("],", start) + 1
patterns = eval(src[start + len(f"'{country}':"):end].strip())
escaped_once = [re.escape(p) for p in patterns]
main_regex = "|".join(re.escape(p) for p in escaped_once)          # double escaping as on main
main_name = statics["ENAME"].str.contains(main_regex, case=True, na=False)
main_ord = statics["TRAC"].isin(["ORD", "ORDSUBR", "FULLPAID", "UKNOWN", "UNKNOW", "KNOW"])
kept_main = set(statics.loc[main_ord | ~main_name, "DSCD"])
n_inactive = sum(re.escape(p) != p for p in patterns)
print(f"(a) main: {len(kept_main)} stocks kept. {n_inactive} of {len(patterns)} name patterns could never "
      f"match because of double escaping.")

# (b) and (c) ----------------------------------------------------------------------------------------
kept_or = set(DSPreprocess.filter_non_common_stocks(dummy_panel, statics, country, mode="legacy_or").Stock)
kept_landis = set(DSPreprocess.filter_non_common_stocks(dummy_panel, statics, country, mode="landis").Stock)
print(f"(b) legacy_or: {len(kept_or)} stocks kept")
print(f"(c) landis:    {len(kept_landis)} stocks kept")

print("\nTRAC values of stocks kept on main but removed under 'landis':")
removed = statics[statics["DSCD"].isin(kept_main - kept_landis)]
print(removed["TRAC"].value_counts(dropna=False).head(20))

out = statics[["DSCD", "ENAME", "TRAC"]].copy()
out["kept_main"] = out["DSCD"].isin(kept_main)
out["kept_legacy_or"] = out["DSCD"].isin(kept_or)
out["kept_landis"] = out["DSCD"].isin(kept_landis)
changed = out[(out.kept_main != out.kept_landis) | (out.kept_main != out.kept_legacy_or)]
changed.to_csv(os.path.join(data_path, "diagnostics_filter1_changes.csv"), index=False)
print(f"\n{len(changed)} stocks change status; written to diagnostics_filter1_changes.csv")
