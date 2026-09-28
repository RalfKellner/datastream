from datastream.preprocessing.raw_data_processing import *
import pandas as pd
import os
import logging
logging.basicConfig(format='%(asctime)s : %(levelname)s : %(message)s', level=logging.INFO)
from datastream.preprocessing.filter import DSPreprocess
import numpy as np
from bidask import edge_rolling


data_path = "/Users/ralfkellner/Datastream/PriceData/US/processed"

penny_percentile = 0.15

# read statics and determine delisting date
statics = pd.read_csv(os.path.join(data_path, "statics.csv"))
delist_str = statics["ENAME"].str.extract(r"DELIST\.(\d{2}/\d{2}/\d{2})")[0]
statics["DelistingDate"] = pd.to_datetime(delist_str, format="%d/%m/%y", errors="coerce")
statics['BDATE'] = pd.to_datetime(statics['BDATE'])

string_columns = ['DSCD', 'ENAME', 'EXMNEM', 'GEOGN', 'ISIN', 'ISINID', 'LOC', 'PCUR', 'TRAC', 'WC05601', 'TYPE', 'TR1N','TR2N','TR3N', 'CURRENCY']

if 'Type' in statics.columns:
    string_columns.insert(0, 'Type')

existing_string_columns = [col for col in string_columns if col in statics.columns]
statics[existing_string_columns] = statics[existing_string_columns].astype(str)
statics.loc[:, 'DSCD'] = statics['DSCD'].str.strip()

# import timeseries data
price_panel = pd.concat([
    pd.read_feather(os.path.join(data_path, f"panel_{i:02d}.feather"))
    for i in range(1, 40)
]).reset_index(drop=True)

price_panel.rename(columns = {"DSCD": "Stock"}, inplace=True)

price_panel.sort_values(by=["Date", "Stock"], inplace=True)
price_panel.reset_index(drop=True, inplace=True)

logging.info(f"Number of rows before removing duplicate Stock-Date observations: {price_panel.shape[0]}")
price_panel = price_panel.drop_duplicates(subset=["Stock", "Date"], keep="first")
logging.info(f"Number of rows after removing duplicate Stock-Date observations: {price_panel.shape[0]}")

price_panel.loc[:, "Stock"] = price_panel["Stock"].astype(str)
price_panel.loc[:, "Stock"] = price_panel["Stock"].str.strip()


mask = price_panel["ReturnIndex"] < 1e-6
logging.info(f"Frequency of ReturnIndex observations with extreme small values: {mask.sum()/price_panel.shape[0]:.6f}")

price_panel.loc[mask, "ReturnIndex"] = np.nan

price_panel["Return"] = (
    price_panel.groupby("Stock")["ReturnIndex"]
    .transform(lambda x: x / x.shift(1) - 1)
)

logging.info(f"Number of companies before removing non-regional companies: {statics.shape[0]}")
statics = statics[statics['GEOGN'] == 'UNITED STATES']
logging.info(f"Number of companies after removing non-regional companies: {statics.shape[0]}")

logging.info(f"Number of stocks with price data: {len(price_panel["Stock"].unique())}")

### Remove companies without return index information
logging.info(f"Observations of panel dataframe: {price_panel.shape[0]}")
# Filter (11) - Equity filter:
price_panel =  DSPreprocess.filter_companies_wo_return_index_data(price_panel)

########################################################################################################################
## Filters based on static data
########################################################################################################################
logging.info(f"Observations of panel dataframe: {price_panel.shape[0]}")
# Filter (1) - Equity filter:
# mode="landis": TRAC screen AND name screen, as in Landis & Skouras (2021). Use mode="legacy_or" to
# reproduce the earlier behaviour (see CHANGES_review.md).
price_panel = DSPreprocess.filter_non_common_stocks(price_panel, statics, country='UNITED STATES', mode="landis")

logging.info(f"Observations of panel dataframe: {price_panel.shape[0]}")
# Filter (2) - Cross-listing filter:
price_panel = DSPreprocess.filter_cross_listings(price_panel, statics, country='UNITED STATES')

logging.info(f"Observations of panel dataframe: {price_panel.shape[0]}")
# Filter (3): Duplicate LOC Codes
price_panel = DSPreprocess.filter_duplicate_loc_codes(price_panel, statics)

logging.info(f"Observations of panel dataframe: {price_panel.shape[0]}")
# Filter (4) - Foreign firms:
price_panel = price_panel[price_panel.Stock.isin(statics.DSCD.unique())]

logging.info(f"Observations of panel dataframe: {price_panel.shape[0]}")
# Filter (5) - Stocks in foreign currencies:
price_panel = DSPreprocess.filter_foreign_currency_stocks(price_panel, statics, country='UNITED STATES')


########################################################################################################################
## Filters based on ReturnIndex
########################################################################################################################
logging.info(f"Observations of panel dataframe: {price_panel.shape[0]}")
# Filter (7) - :
# Remove stocks of which more than 98% of non-zero mean returns are either positive or negative
price_panel = DSPreprocess.filter_implausible_returns(price_panel)


########################################################################################################################
## Stockday filters:
########################################################################################################################
logging.info(f"Observations of panel dataframe: {price_panel.shape[0]}")
# Filter (13):
# Truncate at the delisting date and remove the tenth and subsequent padded (zero/missing return)
# days before it, as in Landis & Skouras (2021). keep_padded=0 reproduces the earlier behaviour.
price_panel = DSPreprocess.filter_padded_values_delistings(price_panel, statics, keep_padded=9)

logging.info(f"Observations of panel dataframe: {price_panel.shape[0]}")
# Filter (12):
# If less than 120 observations.
price_panel = DSPreprocess.filter_short_history_stocks(price_panel, threshold=120)

logging.info(f"Observations of panel dataframe: {price_panel.shape[0]}")
# Filter (8):
# Remove stocks for which the returns are zero in more than 95% of their sample (After applying filter (13).
price_panel = DSPreprocess.filter_zero_return_stocks(price_panel)

logging.info(f"Observations of panel dataframe: {price_panel.shape[0]}")
# Filter (14):
# Stale prices
price_panel = DSPreprocess.filter_stale_prices(price_panel)

logging.info(f"Observations of panel dataframe: {price_panel.shape[0]}")
# Filter (9):
# Remove stocks with a daily standard deviation of more than 40%.
price_panel  = DSPreprocess.filter_stocks_by_high_volatility(price_panel, volatility_threshold=0.40)

logging.info(f"Observations of panel dataframe: {price_panel.shape[0]}")
# Filter (10):
# Remove stocks with a daily standard deviation of less than 0.01 bps.
price_panel = DSPreprocess.filter_stocks_by_low_volatility(price_panel)

logging.info(f"Observations of panel dataframe: {price_panel.shape[0]}")
# Filter (15):
# Target filter rate not reported / ~0.0015% (~0.00569% when applied on raw panel) actual filter rate
price_panel = DSPreprocess.filter_outlier_errors(price_panel, up_ts=1.0, down_ts=-0.5, method='drop')

logging.info(f"Observations of panel dataframe: {price_panel.shape[0]}")
# Filter (16):
# Holiday filter: Has to be applied after filter (11) and (13)!
# Remove days on which non-missing or non-zero returns account for less than 0.5% of total available stocks.
price_panel = DSPreprocess.filter_holidays(price_panel)

logging.info(f"Observations of panel dataframe: {price_panel.shape[0]}")
# Filter (18):
# Filter Adjustment Inconsistencies
# Remove days with inconsistent adjustment values
price_panel = DSPreprocess.filter_adjustment_inconsistencies(price_panel, threshold = 0.05)

logging.info(f"Observations of panel dataframe: {price_panel.shape[0]}")
# Filter (19) - Nonsense values: remove stockdays with zero or negative unadjusted prices.
price_panel = DSPreprocess.filter_nonsense_values(price_panel)

logging.info(f"Observations of panel dataframe: {price_panel.shape[0]}")
# Filter (Own - implausible OHLC):
# Nonsense values (Low > (Open OR High OR Close) and High < (Open OR Low OR Close):
price_panel = DSPreprocess.filter_implausible_prices(price_panel)

logging.info(f"Observations of panel dataframe: {price_panel.shape[0]}")
# Filter (No trading activity - Chaieb et al. (2021) JoFE)
price_panel = DSPreprocess.filter_no_trading_activity(price_panel)

logging.info(f"Observations of panel dataframe: {price_panel.shape[0]}")
# Filter (Own - NA filter) - Drop all rows before they are populated for the first time and apply forward + backward fill.
price_panel = DSPreprocess.handle_missings(price_panel, statics, country='UNITED STATES')

# Filter (Own) - Set delisting returns.
# Applied BEFORE the penny stock filter: otherwise, if the last month(s) of a delisted stock are removed
# as penny stock months, the delisting return would be booked on a date months before the delisting.
# The penny filter may still remove the adjusted row from the universe of that month.
# delisting_return=None switches the adjustment off (see CHANGES_review.md for caveats).
price_panel.replace([np.inf, -np.inf], np.nan, inplace=True)
price_panel = DSPreprocess.adjust_for_delisting(price_panel, delisting_return=-0.35)

# Filter (21) - Penny stocks.
# Note: Landis & Skouras (2021) use the lowest quartile (0.25); penny_percentile is set above.
logging.info(f"Observations of panel dataframe: {price_panel.shape[0]}")
price_panel = DSPreprocess.filter_penny_stocks(price_panel, threshold=penny_percentile)
# logging.info("Saving monthly thresholds for penny stock selection.")

price_panel.replace([np.inf, -np.inf], np.nan, inplace=True)
# extract companies which are in the final filtered data set
statics_for_filtered = statics[statics.DSCD.isin(price_panel.Stock.unique().tolist())]


# Estimate Bid-ask spreads.
price_panel["BidAsk_Spread"] = (
    price_panel.groupby("Stock", group_keys=False)[price_panel.columns]
    .apply(lambda g: edge_rolling(g, window=21, sign=True))
)


# Remove implausible estimates
price_panel["BidAsk_Spread"] = price_panel["BidAsk_Spread"].clip(lower=0.0)
price_panel["BidAsk_Spread"] = price_panel["BidAsk_Spread"].clip(upper=0.25)

price_panel = price_panel[price_panel['Date'] <= '2025-12-31']
price_panel = price_panel[price_panel['Date'] > '1993-10-01']

logging.info("Saving data and static information for remaining companies.")
price_panel.to_feather(os.path.join(data_path, f"US_data_panel_filtered_{penny_percentile}.feather"))
statics_for_filtered.to_csv(os.path.join(data_path, f"statics_filtered_{penny_percentile}.csv"), index = False)


