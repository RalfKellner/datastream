"""Which fiscal year does a Worldscope value stamped in month m describe? (COVID test)

For firms with a December fiscal year end, Worldscope values change in January. If the January-2020 value is
fiscal 2020 (Datastream repeats an annual value over all months of its fiscal year, i.e. from the start of the
fiscal year), sales growth at the January-2020 update is negative (COVID) and at January 2021 strongly positive.
If it is fiscal 2019 (dated after fiscal year end), the pattern is shifted by one year.

    uv run python scripts/diagnostics/check_ws_dating.py
    uv run python scripts/diagnostics/check_ws_dating.py --root D:/Datastream/Firmcharacteristics_Monthly/US
"""

import argparse

import numpy as np
import pandas as pd

from datastream.preprocessing.firm_data import load_variable, resolve_root

EXAMPLES = {"93801Q": "American Airlines", "905039": "Exxon Mobil", "916548": "Walmart (FYE Jan)",
            "719643": "Microsoft (FYE Jun)"}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=None)
    ap.add_argument("--region", default="US")
    args = ap.parse_args()
    root = resolve_root(args.root, args.region)

    for var in ["WC01001", "ENERDP023"]:
        try:
            s = load_variable(root, var, align_month_end=True)
        except FileNotFoundError:
            continue
        s["DSCD"] = s["DSCD"].astype(str)
        s = s.dropna(subset=[var]).sort_values(["DSCD", "Date"])
        ev = s[s[var].ne(s.groupby("DSCD")[var].shift())].copy()
        ev["growth"] = np.log(ev[var] / ev.groupby("DSCD")[var].shift())
        jan = ev[(ev["Date"].dt.month == 1) & ev["growth"].abs().lt(3)]
        print(f"\n{var}: median log growth at January updates (December FYE firms), by year of the update")
        print(jan.groupby(jan["Date"].dt.year)["growth"].agg(["median", "size"]).loc[2017:2023].round(4).to_string())
        print(f"\n{var}: values of example firms at their updates 2018-2022")
        for code, name in EXAMPLES.items():
            e = ev[(ev["DSCD"] == code) & ev["Date"].dt.year.between(2018, 2022)]
            print(f"  {name:22s} " + "  ".join(f"{d:%Y-%m}: {v:,.0f}" for d, v in zip(e["Date"], e[var])))


if __name__ == "__main__":
    main()
