import re
import pandas as pd
import numpy as np

class DSPreprocess:

    @staticmethod
    def handle_missings(panel, statics, country,
            ffill_cols=('Open', 'High', 'Low', 'Close', 'ReturnIndex', 'AdjFactor', 'UnadjClose'),
            require_cols=('Open', 'High', 'Low', 'Close', 'Volume', 'ReturnIndex', 'AdjFactor', 'UnadjClose'),
            flag_col='IsFilled'
                        ):
        """
        Filters a panel DataFrame by removing initial rows containing missing observations for specified columns on a per-stock basis.
        Furthermore, modeling relevant variables are forward filled. non-modeling relevant variables are forward filled and then backward filled.
        If the non-modeling relevant variables are not reported for a Stock at all it is populated by the median value of the corresponding timeframe.

        Parameters:
        - panel (pd.DataFrame): Contains at least columns "Stock", "Date", "MCAP", etc.
        - statics (pd.DataFrame): Contains at least columns "GEOGN" (country name) and "DSCD" (stock ID).
        - country (str): Name of the country (e.g., "UNITED STATES").
        - ffill_cols (list): Columns critical for modeling, to be forward-filled. 'Volume' is no longer
          forward-filled by default: a missing volume is not the previous day's volume.
        - require_cols (list): A stock's history starts at the first date on which all these columns are
          non-missing (unchanged from the earlier implementation, which used ffill_cols for this).
        - flag_col (str or None): Name of a boolean column marking rows in which at least one of
          ffill_cols was forward-filled, so that downstream steps (e.g. spread estimation, event
          studies) can exclude filled prices. None disables the flag.

        Returns:
        - pd.DataFrame: Cleaned subset of panel data (only stocks for 'country'),
          with missing MCAP filled by that country's median MCAP (per date).
        """
        country_stocks = statics.loc[statics["GEOGN"] == country, "DSCD"].unique()
        panel = panel[panel["Stock"].isin(country_stocks)].copy()
        panel.replace([np.inf, -np.inf], np.nan, inplace=True)

        ffill_cols = list(ffill_cols)
        require_cols = list(require_cols)

        def drop_and_fill_missings(group):
            valid_rows = group.dropna(subset=require_cols, how='any')

            if not valid_rows.empty:
                first_valid_date = valid_rows['Date'].min()

                group = group[group['Date'] >= first_valid_date].copy()
                if flag_col is not None:
                    group[flag_col] = group[ffill_cols].isna().any(axis=1)
                group[ffill_cols] = group[ffill_cols].ffill()  # forward fill any cols used in modeling:
                # if bfill_cols is not None:
                #     group[bfill_cols] = group[bfill_cols].ffill().bfill()  # backward fill cols used only for analysis:
                return group
            else:
                return group.iloc[0:0]

        panel_filtered = panel.groupby('Stock', group_keys=False)[panel.columns].apply(drop_and_fill_missings)
        if flag_col is not None and flag_col in panel_filtered.columns:
            panel_filtered[flag_col] = panel_filtered[flag_col].astype(bool)
            print(f"handle_missings: {int(panel_filtered[flag_col].sum())} rows contain forward-filled values "
                  f"(flagged in column '{flag_col}').")

        # if bfill_cols is not None:
        #     panel_filtered[bfill_cols] = panel_filtered[bfill_cols].fillna(
        #         panel_filtered.groupby('Date')[bfill_cols].transform('median')
        #     )
        # else:
        #     panel_filtered['MCAP'] = panel_filtered['MCAP'].fillna(
        #         panel_filtered.groupby('Date')['MCAP'].transform('median')
        #     )

        if panel.shape[0] == 0:
            removal_percentage = 0
        else:
            removal_percentage = round(1 - panel_filtered.shape[0] / panel.shape[0], 5)
        print(f"For {country}, filter (0) removes ~{removal_percentage * 100}% of observations")

        return panel_filtered.reset_index(drop=True)

    @staticmethod
    def filter_companies_wo_return_index_data(panel):
        """
           Filters out stocks without valid ReturnIndex data.

           Stocks for which all observations in the column 'ReturnIndex'
           are missing (NaN) are removed from the panel. This ensures that
           only stocks with at least one available ReturnIndex observation
           remain in the dataset.

           Parameters:
               panel (pd.DataFrame): DataFrame containing at least the
                                     columns 'Stock' and 'ReturnIndex'.

           Returns:
               pd.DataFrame: A copy of the input panel with stocks lacking
                             ReturnIndex data removed.
        """
        valid_mask     = panel.groupby("Stock")["ReturnIndex"].transform(lambda x: x.notna().any())
        panel_filtered = panel[valid_mask].copy()

        removed_fraction = 1 - panel_filtered.shape[0] / panel.shape[0]
        print(f"Filter removes ~{round(removed_fraction * 100, 6)}% of observations")

        return panel_filtered.reset_index(drop=True)

    @staticmethod
    def filter_non_common_stocks(panel, statics, country, mode="landis"):
        """
        Remove non-common stocks from panel. See filter (1) from Landis & Skouras (2021).

        mode="landis" (default): a stock is kept only if its TRAC is accepted (or not populated)
        AND its ENAME contains none of the country-specific non-common strings, as described in the
        paper (Section 3.1.1). mode="legacy_or" reproduces the earlier implementation
        (keep if TRAC accepted OR name clean) and is only meant for comparisons.

        Parameters:
          panel (pd.DataFrame): The panel dataset (e.g. OHLCV data) containing a 'Stock' column.
          statics (pd.DataFrame): The metadata dataset containing columns:
                                  - 'TRAC' (to check for 'ORD'),
                                  - 'ENAME' (to search for patterns),
                                  - 'DSCD' (unique stock identifier).

        Returns:
          pd.DataFrame: A filtered version of the panel dataset with only stocks that pass the filter.
        """
   
        equity_identifers = {
            'UNITED STATES':
            [" TRUST ", " REPR ", " RIGHT", " SERIES ", " NV ", " IV TST",
            "REAL ESTATE INVESTMENT", "REALTY ", " RLTY", "ROYALTY INVESTMENT",
            "ASSET INVESTMENT", "CAPITAL INVESTMENT", "ASSET MANAGEMENT",
            "CAPITAL MANAGEMENT", "INVESTMENT MANAGEMENT", "VENTURE CAPITAL",
            "FINANCIAL SHBI", "PROPERTY INVESTORS", "INCOME PROPERTY", " UNITS ",
            " UNIT ", "LIMITED PARTENERSHIP", "FUND ", "EQUITY PARTNERS",
            "LIMITED VOTING", "SUB VOTING", "TIER ONE SUB", "VARIABLE VOTING",
            "NON VOTINGREIT ", " RESIDENTIAL", "R E I T", "BENEFICIAL",
            "BENEFICIARY", "BENEFIT INTEREST", "BEN INTEREST", "SH BEN INT ",
            "WARRANT", "WRTS", " L P ", "L P INTEREST", "LP UT", "HOLDINGS LP",
            "PARTENERS UNIT", "PART INT", "UNIT PARTENERSHIP", "UNIT LIMITED",
            " MORTGAGE", " REAL ESTATE", "CERTIFICATE", "NO PAR VALUE",
            "HOLDING UNIT", " BACKED", " ST MIN", " CORTS ", " TORPS ", " TOPRS ",
            "SECURITIES TRUPS", " QUIPS ", "STRATS HIGH YIELD", "TOTAL RETURN",
            "DIVERSIFIED HOLDINGS", "(SICAV)", "DEPOSITARY", "DEPOSITOR",
            " RECEIPT", "REP & SHARES", " GLOBAL SHARES", " ADR ", " GDR", "EXPD.",
            "EXPIRED", "DUPLICATE", "CONVERTIBLE", "CNVRT.", "CONVRT.", "EXCH.",
            "DEBANTURE", "(DEB)", "NIL PAID", "STRUCTURED ASSET", " CALLABLE",
            "FLOATING RATE", " ADJUSTABLE", "REDEEMABLE", " PAIRED CTF",
            "CONSOLIDATED", "INSURED", "CAPITAL SHARES", "DEBT STRATEGIES",
            "LIQUIDATING", "LIQUID UNIT", "L UNIT", "- LASD", "ACQUISITION",
            "CAP UNIT", "INCOME UNIT", "PREFERRED"],
            
            'AUSTRIA':
            ['CERTIFICATE', ' ZT ', ' NK5 ', 'DUPLICATE', 'PARTICIPATION CERTIFICATE', 
             'CERT ', ' VI ', 'REIT ',' % S ', 'NIL PAID'], 
            
            'AZERBAIJAN':
            [], 
            
            'BELGIUM':
            ['STR ', 'STR VV ', ' STRIP', ' STRIPS', ' VVPR', 'CERTIFICATE', ' CERT', ' PC ', 
             ' CNP ', ' IDR', ' UNITS', 'DELAWARE', ' ST VV', ' CS 1', ' STRIP VV PR', 
             'FULLY', ' CVA', ' RNC', 'RIGHTS', ' REIT'], 
            
            'BOSNIA AND HERZEGOVINA':
            [], 
            
            'BULGARIA':
            ['FUND', 'REIT', 'NIL PAID', 'MONTSTROY'], 
            
            'CROATIA':
            ['PREFERENCE SHARES', ' PIF'], 
            
            'CZECH REPUBLIC':
            [], 
            
            'CYPRUS':
            ['NIL PAID', ' RTS'], 
            
            'DENMARK':
            ['NIL PAID', 'REGD CERT'], 
            
            'ESTONIA':
            ['ADDITIONAL SHARE', ' NRFD', 'TUIENDAV AKTSIA'], 
            
            'FINLAND':
            [' FDR', 'SUBSCRIPTION RECEIPT', 'SALES RIGHTS'], 
            
            'FRANCE':
            ['CERTIFICATE', 'DELAWARE', 'LIMITED DATA', 'BONUS RIGHTS', ' BDR', ' ADP', 
             ' SPA', 'PREFERRED', 'STOCK DIVIDEND', 'SPA RP', ' AFV ', 'NIL PAID', ' NV ', 
             ' NRFD ', 'NR ', ' CVA', 'DROIT DE VOTE', ' PS ', 'NIL PAID', ' ADP', 
             ' FDR'],
            
            'GERMANY':
            ['REIT', ' SWAP', 'GENUSSSCHEINE', 'PREFERENCE', ' NPV', 'NIL PAID', 
             'BONUS RIGHTS', ' GS ', ' PF ', 'SUB RIGHTS', ' CDI ', ' RSP ', 
             'DEPOSITORY RECEIPTS', ' UNIT ', 'CHESS DEPOSITORY INTEREST', 'DEFERRED', 
             'PARTICIPATE CERTIFICATE', 'LIMITED PARTENERSHIP', ' GDRS', ' TRUST', ' REIT', 
             ' REFINERY'], 
            
            'GREECE':
            [' PR ', ' UNITS', 'PREFERENCE'], 
            
            'HUNGARY':
            [' UNITS', ' TRUST'], 
            
            'ICELAND':
            [], 
            
            'IRELAND':
            ['DUPLICATE', ' FUND', ' UNITS', ' REIT'], 
            
            'ITALY':
            [' RSP', ' CONV RTS', 'NIL PAID', 'SUB RIGHTS', 'BONUS RIGHTS', ' RIGHTS ', 
             ' RP ', ' RCV', ' NRFD', 'FULLY PAID', 'FULLY PIAD', ' ETN '], 
            
            'KAZAKHSTAN':
            ['PREFERENCE LIMITED', 'PREFERENCE SHARES'], 
            
            'LATVIA':
            ['FB '], 
            
            'LITHUANIA':
            [], 
            
            'LUXEMBOURG':
            [' IDR ', 'DEPOSITARY RECEIPT', 'DEPOSITARY', ' EDR ', ' VVPR', 'DELAWARE', 
             ' EDR ', ' CDR ', ' FDR ', ' CERT', ' GDR ', ' BDR'], 
            
            'NORTH MACEDONIA':
            [], 
            
            'MALTA':
            [], 
            
            'MONTENEGRO':
            [], 
            
            'NETHERLANDS':
            ['CERTIFICATE', 'DUPLICATE', 'DEPOSITARY', 'BONUS RIGHTS', ' % STOCK ', ' CERT', 
             'CERTS', 'TRUST INCOME', ' STRIP', ' CT ', ' DUPL', ' UNITS', ' SPA ', 
             'STRIP VVPR', 'PREFERENCE'], 
            
            'NORWAY':
            [' DUPLI', 'NEW SHARES', 'NIL PAID'], 
            
            'POLAND':
            ['NIL PAID'], 
            
            'PORTUGAL':
            ['BONUS RIGHT', 'NIL PAID'], 
            
            'ROMANIA':
            [], 
            
            'RUSSIAN FEDERATION':
            [' PREF', 'TRAST', ' RDP', 'PREFERENCE', ' PREF.'], 
            
            'SERBIA':
            [' CF '], 
            
            'SLOVAKIA':
            [' FOND', ' VP', ' POV P', ' PP', ' LINKV', ' ZSP'], 
            
            'SLOVENIA':
            [], 
            
            'SPAIN':
            ['NIL PAID', 'BONUS RIGHTS', 'BUNUS RIGHTS', ' SHARES', ' LIMITED DATA', 
             ' CPO '], 
            
            'SWEDEN':
            [' SDB', ' UNIT', 'NIL PAID REDEMPTION', ' REDEMP', ' SDR', 'FULLY PAID', 
             ' RFD', 'INTERIM SHARE', ' RIGHTS', 'DEPOSITARY', 'RECEIPTS', ' SR 1', 
             ' RFD'], 
            
            'SWITZERLAND':
            ['WHEN ISSUED', 'CERTIFICATE', 'DELAWARE', ' SERIES', 'REAL ESTATE FUND', 
             ' CERT', 'DUPLICATE', ' UNITS', ' BOND', 'BONUS RIGHTS', 'REAL ESTATE IFCA', 
             'PROPERTY FUND', ' MIXED', ' REIT', 'COMMERCIAL FUND', ' DRC'], 
            
            'TURKEY':
            ['CERT', ' NRFD', 'NIL PAID', 'FULLY PAID'], 
            
            'UNITED KINGDOM':
            [' FUND ', ' TRUST ', 'NIL PAID', 'STOCK UNIT', 'ANNUITY UNIT', 'UNIT £', 
             'UNIT TRUST', ' UNITS', ' ZDP ', 'REIT', 'POST RED', 'DEPOSITARY', ' RECEIPT', 
             'INTERIM SHARES', 'REEDEMABLE', 'PREFERENCE', 'INVESTMENT TRUST', ' ADR',
             'FULLY PAID', 'PARTLY PAID', ' BDR', ' NRDF', 'DEFERRED'], 
            
            'UKRAINE':
            [' CF ', ' FUND', ' CLOSED FUND '] 
            
        }

        if country not in equity_identifers:
            raise ValueError(f"Invalid country: '{country}'. Must be one of {list(equity_identifers.keys())}.")
        
        equity_identifer = equity_identifers[country]
        equity_identifer = [re.escape(p) for p in equity_identifer]

        # TRAC values which Landis & Skouras (2021, Section 3.1.1, filter 1) accept.
        # "UKNOWN", "UNKNOW" and "KNOW" are spelled as in the paper (TDS codes for "unknown").
        accepted_trac = ["ORD", "ORDSUBR", "FULLPAID", "UKNOWN", "UNKNOW", "KNOW"]
        trac = statics["TRAC"].astype(str).str.strip()
        is_ord = trac.isin(accepted_trac)
        # The paper notes that TRAC is frequently not populated (esp. for delisted stocks) and that
        # excluding such stocks would bias the sample towards survivors. Unpopulated TRAC is therefore
        # treated like "unknown" and kept. Depending on how statics were read, missing values show up
        # as NaN, "nan", "NA", "None" or "".
        trac_missing = statics["TRAC"].isna() | trac.isin(["", "nan", "NaN", "NA", "N/A", "None", "<NA>"])

        if equity_identifer:
            pattern_regex = "|".join(equity_identifer)  # patterns were already escaped above
            ename_condition = statics["ENAME"].astype(str).str.contains(pattern_regex, case=True, na=False, regex=True)
        else:
            ename_condition = pd.Series(False, index=statics.index)  # no exclusion via name patterns

        if mode == "landis":
            # Paper: exclude stocks whose TRAC is not in the accepted list AND (separately) exclude all
            # stocks whose ENAME contains a non-common text string -> a stock must pass both screens.
            keep_condition = (is_ord | trac_missing) & (~ename_condition)
        elif mode == "legacy_or":
            # Behaviour of the original implementation (kept for comparison only): a stock is kept if
            # it passes either screen.
            keep_condition = is_ord | (~ename_condition)
        else:
            raise ValueError("mode must be 'landis' or 'legacy_or'.")

        statics_filtered = statics[keep_condition].copy()
        remaining_stocks = statics_filtered.DSCD.unique()

        removal_percentage = round(1 - keep_condition.sum() / statics.shape[0], 4)
        print(f"For {country}, filter (1) [{mode}] removes ~{removal_percentage * 100:.2f}% of stocks (based on raw data): "
              f"{int((~(is_ord | trac_missing)).sum())} fail the TRAC screen, {int(ename_condition.sum())} fail the name screen, "
              f"{int(((~(is_ord | trac_missing)) & ename_condition).sum())} fail both.")

        panel_filtered = panel[panel["Stock"].isin(remaining_stocks)].copy()

        return panel_filtered.reset_index(drop=True)


    @staticmethod
    def filter_cross_listings(panel, statics, country):
        """
        Remove cross-listings from the panel. See filter (2) from Landis & Skouras (2021).

        Parameters:
            panel (pd.DataFrame): The panel dataset containing at least the 'Stock' column.
            statics (pd.DataFrame): The metadata dataset containing 'ENAME' and 'DSCD' columns.

        Returns:
            pd.DataFrame: The filtered panel dataset.
        """
                             
        cross_listings_dict = {
            'UNITED STATES':
            r"\(NYS\)|\(NAS\)|\(ASE\)|\(OTC\)|\(XSQ\)|\(XQB\)",
            
            'AUSTRIA':
            r"\(WBO\)", 
            
            'AZERBAIJAN':
            r"", 
            
            'BELGIUM':
           r"\(BRU\)", 
            
            'BOSNIA AND HERZEGOVINA':
            r"", 
            
            'BULGARIA':
            r"", 
            
            'CROATIA':
            r"", 
            
            'CZECH REPUBLIC':
            r"\(PRA\)", 
            
            'CYPRUS':
            r"\(CYP\)", 
            
            'DENMARK':
            r"\(CSE\)", 
            
            'ESTONIA':
            r"", 
            
            'FINLAND':
            r"\(HEL\)", 
            
            'FRANCE':
            r"\(PAR\)",
            
            'GERMANY':
            r"\(FRA\)|\(STU\)|\(HAM\)|\(DUS\)|\(MUN\)|\(XET\)", 
            
            'GREECE':
            r"\(ATH\)", 
            
            'HUNGARY':
            r"\(BUD\)", 
            
            'ICELAND':
            r"\(ICE\)", 
            
            'IRELAND':
            r"\(DUB\)|\(IEX\)|\(ESM\)", 
            
            'ITALY':
            r"\(MIL\)", 
            
            'KAZAKHSTAN':
            r"\(KAZ\)", 
            
            'LATVIA':
            r"", 
            
            'LITHUANIA':
            r"", 
            
            'LUXEMBOURG':
            r"\(LUX\)", 
            
            'NORTH MACEDONIA':
            r"", 
            
            'MALTA':
            r"\(MALTA\)|\(MAL.\)", 
            
            'MONTENEGRO':
            r"", 
            
            'NETHERLANDS':
            r"\(AMS\)|\(FL\)", 
            
            'NORWAY':
            r"\(OSL\)", 
            
            'POLAND':
            r"\(WAR\)", 
            
            'PORTUGAL':
            r"\(LIS\)", 
            
            'ROMANIA':
            r"\(BSE\)", 
            
            'RUSSIAN FEDERATION':
            r"", 
            
            'SERBIA':
            r"", 
            
            'SLOVAKIA':
            r"", 
            
            'SLOVENIA':
            r"", 
            
            'SPAIN':
            r"\(MAD\)", 
            
            'SWEDEN':
            r"\(OME\)|\(XSQ\)", 
            
            'SWITZERLAND':
            r"\(SWX\)|\(BRN\)", 
            
            'TURKEY':
            r"\(IST\)", 
            
            'UNITED KINGDOM':
            r"\(LON\)|\(UNITED KINGDOM\)", 
            
            'UKRAINE':
            r"" 
            
        }
                             
        if country not in cross_listings_dict:
            raise ValueError(f"Invalid country: '{country}'. Must be one of {list(cross_listings_dict.keys())}.")
                             
        cross_listings     = cross_listings_dict[country]

        if cross_listings.strip(): # only filter if there's a non-empty pattern
            statics_f2 = statics[~statics["ENAME"].str.contains(cross_listings, case=True, na=False)]
        else:
            statics_f2 = statics.copy()

        removal_percentage = round(1 - statics_f2.shape[0] / statics.shape[0], 3)

        print(f"For {country}, filter (2) removes ~{removal_percentage * 100}% of stocks (based on raw data).")
        rem_stocks_f2  = statics_f2["DSCD"].unique()
        panel_filtered = panel[panel["Stock"].isin(rem_stocks_f2)].copy()

        return panel_filtered.reset_index(drop=True)


    @staticmethod
    def filter_duplicate_loc_codes(panel, statics):
        """
        Remove non-common stock identification from duplicate local codes. See filter (3) from Landis & Skouras (2021).

        In each LOC group:
          - If there is more than one entry and at least one row has ISINID equal to 'P',
            then keep only rows with ISINID equal to 'P'.
          - Otherwise, keep all rows.

        Parameters:
            panel (pd.DataFrame): The panel dataset containing at least the 'Stock' column.
            statics (pd.DataFrame): The metadata dataset containing the columns 'LOC',
                                    'ISINID', and 'DSCD'.

        Returns:
            pd.DataFrame: The filtered panel dataset.
        """
        loc_size     = statics.groupby("LOC")["LOC"].transform("size") # Compute number of rows for each local code
        has_p        = statics.groupby("LOC")["ISINID"].transform(lambda x: (x == "P").any()) # Create boolean mask that is true if ISINID == "P"
        rows_to_keep = ~((loc_size > 1) & (has_p) & (statics["ISINID"] != "P"))

        statics_f3   = statics[rows_to_keep].copy()
        removal_percentage = round(1 - statics_f3.shape[0] / statics.shape[0], 3)
        print(f"Filter (3) removes ~{removal_percentage * 100}% of stocks (based on raw data).")

        rem_stocks_f3  = statics_f3["DSCD"].unique()
        panel_filtered = panel[panel["Stock"].isin(rem_stocks_f3)].copy()

        return panel_filtered.reset_index(drop=True)

    @staticmethod
    def filter_foreign_stocks(panel, statics, country):
        """
        Remove foreign (default: non-US stocks) from the panel. See filter (4) from Landis & Skouras (2021).

        This filter removes any stocks that are not identified as "UNITED STATES" in the GEOGN column
        of the statics dataset.

        Parameters:
            panel (pd.DataFrame): The panel dataset containing at least the 'Stock' column.
            statics (pd.DataFrame): The metadata dataset containing the 'GEOGN' and 'DSCD' columns.
            country (str): Country to remove. Default = "UNITED STATES"
        Returns:
            pd.DataFrame: The filtered panel dataset.
        """
        
        country_codes_dict = {
            
            'USA': 
            "UNITED STATES",
            
            'Austria':
            "AUSTRIA", 
            
            'Azerbaijan':
            "AZERBAIJAN", 
            
            'Belgium':
            "BELGIUM", 
            
            'Bosnia-Herzegovina':
            "BOSNIA AND HERZEGOVINA", 
            
            'Bulgaria':
            "BULGARIA", 
            
            'Croatia':
            "CROATIA", 
            
            'Czech Republic':
            "CZECH REPUBLIC", 
            
            'Cyprus':
            "CYPRUS", 
            
            'Denmark':
            "DENMARK", 
            
            'Estonia':
            "ESTONIA", 
            
            'Finland':
            "FINLAND", 
            
            'France':
            "FRANCE",
            
            'Germany':
            "GERMANY", 
            
            'Greece':
            "GREECE", 
            
            'Hungary':
            "HUNGARY", 
            
            'Iceland':
            "ICELAND", 
            
            'Ireland':
            "IRELAND", 
            
            'Italy':
            "ITALY", 
            
            'Kazakhstan':
            "KAZAKHSTAN", 
            
            'Latvia':
            "LATVIA", 
            
            'Lithuania':
            "LITHUANIA", 
            
            'Luxembourg':
            "LUXEMBOURG", 
            
            'Macedonia':
            "MACEDONIA", 
            
            'Malta':
            "MALTA", 
            
            'Montenegro':
            "MONTENEGRO", 
            
            'Netherlands':
            "NETHERLANDS", 
            
            'Norway':
            "NORWAY", 
            
            'Poland':
            "POLAND", 
            
            'Portugal':
            "PORTUGAL", 
            
            'Romania':
            "ROMANIA", 
            
            'Russia':
            "RUSSIAN FEDERATION", 
            
            'Serbia':
            "SERBIA", 
            
            'Slovakia':
            "SLOVAKIA", 
            
            'Slovenia':
            "SLOVENIA", 
            
            'Spain':
            "SPAIN", 
            
            'Sweden':
            "SWEDEN", 
            
            'Switzerland':
            "SWITZERLAND", 
            
            'Turkey':
            "TURKEY", 
            
            'UK':
            "UNITED KINGDOM", 
            
            'Ukraine':
            "UKRAINE" 
            
        }
        
        # Accept both the short keys above (e.g. 'Germany') and GEOGN values (e.g. 'GERMANY'), so that
        # this function can be called with the same `country` argument as the other filters.
        # 'NORTH MACEDONIA' is added because the other filters use this spelling (TDS may report 'MACEDONIA').
        valid_geogn = set(country_codes_dict.values()) | {"NORTH MACEDONIA"}
        if country in country_codes_dict:
            country_code = country_codes_dict[country]
        elif country in valid_geogn:
            country_code = country
        else:
            raise ValueError(f"Invalid country: '{country}'. Must be one of {list(country_codes_dict.keys())} "
                             f"or one of {sorted(valid_geogn)}.")

        statics_f4 = statics[statics["GEOGN"] == country_code].copy()

        rem_stocks_f4  = statics_f4["DSCD"].unique()
        panel_filtered = panel[panel["Stock"].isin(rem_stocks_f4)].copy()

        removal_percentage = round(1 - statics_f4.shape[0] / statics.shape[0], 4)
        print(f"Filter (4) removes ~{removal_percentage * 100:.2f}% of stocks")

        return panel_filtered.reset_index(drop=True)

    @staticmethod
    def filter_surivorship_bias(panel, statics, country):
        """
        Remove dates from the panel that are prior to the recommended starting date.
        See filter (17) from Landis & Skouras (2021) and the Internet Appendix - Table B.4 for further details.

        Parameters:
            panel (pd.DataFrame): The panel dataset. Must contain columns "Stock" and "Date".
            statics (pd.DataFrame): The metadata dataset. Must contain columns "GEOGN" and "DSCD".
            country (str): The name of the country to filter on, e.g. "UNITED STATES".

        Returns:
            pd.DataFrame: The filtered panel dataset (only rows on or after the earliest start date).
        """

        # Dictionary: country -> earliest allowable date (DD-MM-YYYY)
        country_start_dates = {
            'UNITED STATES': '31-12-1984',
            'AUSTRIA': '31-12-1991',
            'AZERBAIJAN': '31-12-1900',
            'BELGIUM': '31-12-1991',
            'BOSNIA AND HERZEGOVINA': '31-12-2009',
            'BULGARIA': '31-12-2005',
            'CROATIA': '31-12-2005',
            'CZECH REPUBLIC': '31-12-1995',
            'CYPRUS': '31-12-2009',
            'DENMARK': '31-12-1987',
            'ESTONIA': '31-12-2009',
            'FINLAND': '31-12-1987',
            'FRANCE': '31-12-1981',
            'GERMANY': '31-12-1988',
            'GREECE': '31-12-1995',
            'HUNGARY': '31-12-1999',
            'ICELAND': '31-12-2005',
            'IRELAND': '31-12-1989',
            'ITALY': '31-12-1988',
            'KAZAKHSTAN': '31-12-2013',
            'LATVIA': '31-12-2009',
            'LITHUANIA': '31-12-2007',
            'LUXEMBOURG': '31-12-1994',
            'NORTH MACEDONIA': '31-12-2009',
            'MALTA': '31-03-2004',
            'MONTENEGRO': '31-12-2012',
            'NETHERLANDS': '31-12-1986',
            'NORWAY': '31-12-1986',
            'POLAND': '31-12-1995',
            'PORTUGAL': '31-12-1991',
            'ROMANIA': '31-12-2008',
            'RUSSIAN FEDERATION': '31-12-2004',
            'SERBIA': '31-12-2009',
            'SLOVAKIA': '31-12-2007',
            'SLOVENIA': '31-12-2005',
            'SPAIN': '31-12-1992',
            'SWEDEN': '31-12-1986',
            'SWITZERLAND': '31-12-1982',
            'TURKEY': '31-12-1994',
            'UNITED KINGDOM': '31-12-1984',
            'UKRAINE': '31-12-2006',
        }

        if country not in country_start_dates:
            raise ValueError(
                f"Invalid country: '{country}'. Must be one of {list(country_start_dates.keys())}."
            )

        # Convert the string to a proper datetime object; note dayfirst=True or format='%d-%m-%Y'
        earliest_date = pd.to_datetime(country_start_dates[country], dayfirst=True)

        # Filter statics down to the stocks belonging to the specified country
        country_statics = statics[statics["GEOGN"] == country].copy()
        country_stocks  = country_statics["DSCD"].unique()
        panel_country   = panel[panel["Stock"].isin(country_stocks)].copy()
        panel_country["Date"] = pd.to_datetime(panel_country["Date"])
        panel_filtered = panel_country[panel_country["Date"] >= earliest_date].copy()

        original_count = len(panel_country)
        filtered_count = len(panel_filtered)
        removal_percentage = (
                1.0 - filtered_count / float(original_count)
        ) if original_count > 0 else 0.0

        print(f"For {country}, filter (17) removes ~{round(removal_percentage * 100, 2)}% of rows.")
        return panel_filtered.reset_index(drop=True)


    @staticmethod
    def filter_foreign_currency_stocks(panel, statics, country):
        """
        Remove stocks that are not denoted in US$ from the panel. See filter (5) from Landis & Skouras (2021).

        Parameters:
            panel (pd.DataFrame): The panel dataset containing at least the 'Stock' column.
            statics (pd.DataFrame): The metadata dataset containing 'PCUR' and 'DSCD' columns.
            currency (str): Currency to remove. Default = "U$"

        Returns:
            pd.DataFrame: The filtered panel dataset.
        """
        
        currencies_dict = {
            
            'UNITED STATES':
            ['U$'],
            
            'AUSTRIA':
            ['AS', 'E'], 
            
            'AZERBAIJAN':
            ['AM'], 
            
            'BELGIUM':
            ['BF', 'E'], 
            
            'BOSNIA AND HERZEGOVINA':
            ['BO'], 
            
            'BULGARIA':
            ['BL'], 
            
            'CROATIA':
            ['KA', 'E'],
            
            'CZECH REPUBLIC':
            ['CK', 'E'],
            
            'CYPRUS':
            ['CY', 'E'],
            
            'DENMARK':
            ['DK'],
            
            'ESTONIA':
            ['EK', 'E'], 
            
            'FINLAND':
            ['M', 'E'], 
            
            'FRANCE':
            ['FF', 'E'],
            
            'GERMANY':
            ['DM', 'E'], 
            
            'GREECE':
            ['DR', 'E'], 
            
            'HUNGARY':
            ['HF'], 
            
            'ICELAND':
            ['IK'], 
            
            'IRELAND':
            ['£E', 'E'],
            
            'ITALY':
            ['L', 'E'], 
            
            'KAZAKHSTAN':
            ['KT'],
            
            'LATVIA':
            ['LV', 'E'],
            
            'LITHUANIA':
            ['LT', 'E'],
            
            'LUXEMBOURG':
            ['LF', 'E'],
            
            'NORTH MACEDONIA':
            ['MC'],
            
            'MALTA':
            ['M£', 'E'],
            
            'MONTENEGRO':
            ['E'],
            
            'NETHERLANDS':
            ['FL', 'E'],
            
            'NORWAY':
            ['NK'],
            
            'POLAND':
            ['PZ'],
            
            'PORTUGAL':
            ['PE', 'E'],
            
            'ROMANIA':
            ['RL'],
            
            'RUSSIAN FEDERATION':
            ['UR', 'U$'],
            
            'SERBIA':
            ['YD'],
            
            'SLOVAKIA':
            ['KK', 'E'],
            
            'SLOVENIA':
            ['TO', 'E'],
            
            'SPAIN':
            ['EP', 'E'],
            
            'SWEDEN':
            ['SK'],
            
            'SWITZERLAND':
            ['SF'],
            
            'TURKEY':
            ['TL'],
            
            'UNITED KINGDOM':
            ['£'],
            
            'UKRAINE':
            ['KB']
                 
       }                      
        
        if country not in currencies_dict:
            raise ValueError(f"Invalid country: '{country}'. Must be one of {list(currencies_dict.keys())}.")                     
                             
        currency = currencies_dict[country]                  
                             
        # statics_f5 = statics[statics["PCUR"].isin(currency)].copy()
        statics_f5 = statics[(statics["PCUR"].isin(currency)) & (statics["GEOGN"] == country)].copy()

        rem_stocks_f5  = statics_f5["DSCD"].unique()
        panel_filtered = panel[panel["Stock"].isin(rem_stocks_f5)].copy()

        removal_percentage = round(1 - statics_f5.shape[0] / statics[statics["GEOGN"] == country].shape[0], 3)
        print(f"For {country}, filter (5) removes ~{removal_percentage * 100}% of stocks")

        return panel_filtered.reset_index(drop=True)

    @staticmethod
    def filter_countries_with_few_stocks(OHLCV_panel, statics, min_stocks=20):
        """
        Remove all countries from both datasets that have fewer than `min_stocks` unique stocks
        in the OHLCV_panel. Prints a summary of removed countries. See filter (6) from Landis & Skouras (2021).

        Parameters:
            OHLCV_panel (pd.DataFrame): Time series panel data with a 'Stock' column.
            statics (pd.DataFrame): Static metadata with 'DSCD' (stock code) and 'GEOGN' (country).
            min_stocks (int): Minimum number of unique stocks required to keep a country.

        Returns:
            Tuple[pd.DataFrame, pd.DataFrame]: Filtered OHLCV_panel and statics dataframes.
        """
        stock_country = OHLCV_panel[['Stock']].drop_duplicates().merge(
            statics[['DSCD', 'GEOGN']],
            left_on='Stock',
            right_on='DSCD',
            how='left'
        )

        country_counts    = stock_country.groupby('GEOGN')['Stock'].nunique()
        removed_countries = country_counts[country_counts < min_stocks]
        valid_countries   = country_counts[country_counts >= min_stocks].index

        if not removed_countries.empty:
            print("\nThe following countries were removed due to having fewer than "
                  f"{min_stocks} unique stocks:")
            for country, count in removed_countries.items():
                print(f"  - {country}: {count} stocks")
        else:
            print("\nNo countries removed — all meet the minimum stock threshold.")

        statics_filtered     = statics[statics['GEOGN'].isin(valid_countries)].copy()
        valid_stocks         = statics_filtered['DSCD'].unique()
        OHLCV_panel_filtered = OHLCV_panel[OHLCV_panel['Stock'].isin(valid_stocks)].copy()

        return OHLCV_panel_filtered, statics_filtered


    @staticmethod
    def filter_implausible_returns(panel):
        """
        Filter out stocks with implausible return patterns. See filter (7) from Landis & Skouras (2021).

        Parameters:
            panel (pd.DataFrame): The panel dataset containing at least the following columns:
                                  - 'Stock'
                                  - 'ReturnIndex'

        Returns:
            pd.DataFrame: The filtered panel dataset.
        """
        panel = panel.copy()

        def check_implausibility(x):
            x = x.dropna()
            nonzero = x[x != 0]
            if len(nonzero) == 0:
                return False

            pos_fraction = (nonzero > 0.0).mean()
            neg_fraction = (nonzero < 0.0).mean()

            # If more than 98% of nonzero returns are all positive or all negative, flag as implausible.
            return pos_fraction > 0.98 or neg_fraction > 0.98

        stock_implausible  = panel.groupby("Stock")["Return"].apply(check_implausibility)
        stocks_implausible = stock_implausible[stock_implausible].index

        panel_filtered     = panel[~panel["Stock"].isin(stocks_implausible)].copy()
        removal_percentage = round(1 - panel_filtered.shape[0] / panel.shape[0], 3)
        print(f"Filter (7) removes ~{removal_percentage * 100}% of observations")

        return panel_filtered.reset_index(drop=True)


    @staticmethod
    def filter_padded_values_delistings(panel, statics, keep_padded=9):
        """
        Truncate each stock at its DelistingDate and remove padded observations. See filter (13) from Landis & Skouras (2021).

        Landis & Skouras (2021, Section 3.2): "we eliminate data for stocks where we observe padded values
        for return indexes immediately preceding their delisting date [...] we remove the tenth and
        subsequent padded daily observation." Padded observations are the trailing days (up to the
        delisting date) whose return is zero or missing. With keep_padded=9 (default) the first nine
        padded days are kept and the tenth and all later ones are removed, as in the paper.
        keep_padded=0 reproduces the earlier implementation, which removed all trailing padded days.

        Parameters:
            panel (pd.DataFrame): The panel dataset (e.g., OHLCV_panel) with at least the columns:
                                  'Stock', 'Date', 'Return', 'ReturnIndex'.
            statics (pd.DataFrame): The metadata DataFrame containing at least the columns:
                                    'DSCD' and 'DelistingDate'.
            keep_padded (int): Number of trailing padded observations to keep (paper: 9).

        Returns:
            pd.DataFrame: The filtered panel dataset after applying Filter (13).
        """
        if keep_padded < 0:
            raise ValueError("keep_padded must be >= 0.")

        # Diagnostic: dead stocks whose delisting date could not be parsed from ENAME are not truncated
        # by this filter (their padded tail is then only handled by the staleness filter (14)).
        if "ENAME" in statics.columns:
            dead_wo_date = statics["ENAME"].astype(str).str.contains("DEAD", na=False) & statics["DelistingDate"].isna()
            if dead_wo_date.any():
                print(f"Filter (13) warning: {int(dead_wo_date.sum())} stocks contain 'DEAD' in ENAME but have no "
                      f"parsable delisting date; they are not truncated by this filter.")

        statics_unique = statics.drop_duplicates(subset="DSCD", keep="first")

        panel_merged = panel.merge(
            statics_unique[["DSCD", "DelistingDate"]],
            how="inner",
            left_on="Stock",
            right_on="DSCD"
        )

        def truncate_at_delisting(df):
            df = df.sort_values("Date")
            delist_date = df["DelistingDate"].iloc[0]
            if pd.notna(delist_date):
                df = df[df["Date"] <= delist_date].copy()
                ret_vals = df["Return"].to_numpy(dtype=float)
                padded = (ret_vals == 0) | np.isnan(ret_vals)
                # length of the trailing run of padded observations
                n_trailing = len(padded) if padded.all() else int(np.argmin(padded[::-1]))
                n_remove = max(n_trailing - keep_padded, 0)
                if n_remove > 0:
                    df = df.iloc[:len(df) - n_remove]
            else:
                df = df.copy()
            return df

        panel_filtered = panel_merged.groupby("Stock", group_keys=False)[panel_merged.columns].apply(
            truncate_at_delisting)

        removal_percentage = round(1 - panel_filtered.shape[0] / panel.shape[0], 3)
        print(f"Filter (13) removes ~{removal_percentage * 100}% of observations")

        return panel_filtered.reset_index(drop=True)


    @staticmethod
    def filter_stale_prices(panel):
        """
        Filters the panel DataFrame by removing rows for which the 'ReturnIndex'
        has the same value for more than 30 consecutive days per stock. See filter (14) from Landis & Skouras (2021).

        Parameters:
            panel (DataFrame): A DataFrame with at least the columns 'Date', 'Stock', and 'ReturnIndex'.
                               Typically, this is OHLCV_panel.

        Returns:
            DataFrame: The filtered panel DataFrame.
        """

        def filter_stale_prices_for_stock(df):
            df     = df.sort_values("Date").copy()
            prices = df["ReturnIndex"].values
            keep   = [True] * len(df)

            current_run = 1
            for i in range(1, len(prices)):
                if prices[i] == prices[i - 1]:
                    current_run += 1  # Increment the run if the same price repeats
                else:
                    current_run = 1   # Reset the run when a new price is encountered

                if current_run > 30:
                    keep[i] = False   # Mark the row to be dropped if repetition exceeds 30 days

            return df[keep].copy()

        original_count = panel.shape[0]
        panel_filtered = panel.groupby("Stock", group_keys=False)[panel.columns].apply(filter_stale_prices_for_stock)

        removal_percentage = round(1 - panel_filtered.shape[0] / original_count, 5)
        print(f"Filter (14) removes ~{removal_percentage * 100}% of observations")

        return panel_filtered.reset_index(drop=True)


    @staticmethod
    def filter_zero_return_stocks(panel):
        """
        Filters out stocks with more than 95% of their 'Return' values equal to zero.
        See filter (8) from Landis & Skouras (2021).

        Parameters:
            panel (pd.DataFrame): DataFrame with at least 'Stock' and 'Return' columns.

        Returns:
            pd.DataFrame: Filtered DataFrame excluding stocks with excessive zero returns.
        """
        # Compute fraction of zero returns per stock
        frac_zero = panel.groupby("Stock")["Return"].apply(lambda x: (x == 0.0).mean())

        # Identify and remove stocks with more than 95% zeros
        stocks_too_many_zeros = frac_zero[frac_zero > 0.95].index
        panel_filtered = panel[~panel["Stock"].isin(stocks_too_many_zeros)].copy()

        removed_percentage = round(1 - panel_filtered.shape[0] / panel.shape[0], 6)
        print(f"Filter (8) removes ~{removed_percentage * 100}% of observations")

        return panel_filtered.reset_index(drop=True)

    @staticmethod
    def filter_stocks_by_high_volatility(panel, volatility_threshold=0.4):
        """
        Filters out stocks with a daily standard deviation of returns above a specified threshold.
        See filter (9) from Landis & Skouras (2021).

        Parameters:
            panel (pd.DataFrame): DataFrame containing at least the columns 'Stock' and 'Return'.
            volatility_threshold (float): Maximum allowed daily standard deviation (default is 0.4, i.e., 40%).

        Returns:
            pd.DataFrame: A copy of the input panel with stocks exceeding the volatility threshold removed.
        """

        std_returns = panel.groupby("Stock")["Return"].std().dropna()
        stocks_to_filter = std_returns[std_returns > volatility_threshold].index

        panel_filtered = panel[~panel["Stock"].isin(stocks_to_filter)].copy()
        removed_percentage = round(1 - panel_filtered.shape[0] / panel.shape[0], 3)
        print(f"Filter (9) removes ~{removed_percentage * 100}% of observations")

        return panel_filtered.reset_index(drop=True)

    @staticmethod
    def filter_stocks_by_low_volatility(panel, low_threshold=1e-6):
        """
        Filters out stocks with a daily standard deviation of returns below a specified low volatility threshold.
        Stocks with a daily standard deviation of returns less than the low_threshold (default is 0.01 basis points, i.e., 1e-6)
        are removed from the panel. See filter (10) from Landis & Skouras (2021).

        Parameters:
            panel (pd.DataFrame): DataFrame containing at least the columns 'Stock' and 'Return'.
            low_threshold (float): The minimum allowed daily standard deviation (default 1e-6).

        Returns:
            pd.DataFrame: A copy of the input panel with stocks having low volatility removed.
        """
        std_returns      = panel.groupby("Stock")["Return"].std().dropna()
        low_vol_stocks   = std_returns[std_returns < low_threshold].index

        panel_filtered   = panel[~panel["Stock"].isin(low_vol_stocks)].copy()
        removed_fraction = 1 - panel_filtered.shape[0] / panel.shape[0]
        print(f"Filter (10) removes ~{round(removed_fraction * 100, 6)}% of observations")

        return panel_filtered.reset_index(drop=True)

    @staticmethod
    def filter_short_history_stocks(panel, threshold=120):
        """
        Filters the panel to include only stocks with sufficient observation history.
        See filter (12) from Landis & Skouras (2021).

        A stock is retained if it has at least 'threshold' (default 120) observations,
        unless its first observation is within the last 'threshold' days of the overall sample,
        in which case it is retained regardless of count.

        Parameters:
        - panel (pd.DataFrame): DataFrame containing at least the columns 'stock' and 'date'.
          'date' should be in a datetime format.
        - threshold (int): Minimum number of valid observations required (default=120).

        Returns:
        - pd.DataFrame: The filtered panel.
        """
        panel['Date'] = pd.to_datetime(panel['Date'])
        overall_last_date = panel['Date'].max()

        def stock_filter(group):
            num_obs = group.shape[0]  # total observations for the stock
            first_date = group['Date'].min()  # first observation date for the stock

            if (overall_last_date - first_date).days < threshold:
                return True  # True if short horizon is at the end of the dataset.
            else:
                return num_obs >= threshold  # Returns False if there are less observations than "threshold".

        panel_filtered   = panel.groupby('Stock').filter(stock_filter)
        removed_fraction = 1 - panel_filtered.shape[0] / panel.shape[0]
        print(f"Filter (12) removes ~{round(removed_fraction * 100, 6)}% of observations")

        return panel_filtered.reset_index(drop=True)

    @staticmethod
    def filter_outlier_errors(panel, up_ts=1.0, down_ts=-0.5, method="drop"):
        """
        Filters out stock-days that appear to be extreme outlier errors.
        See filter (15) from Landis & Skouras (2021).

        For each stock:
        - If on day t, the return > 1.0 and on day t+1, the return < -0.50,
          then both day t and day t+1 are removed.
        - If on day t, the return < -0.50 and on day t+1, the return > 1.00,
          then both day t and day t+1 are removed.

        Parameters:
        - panel (pd.DataFrame): A DataFrame with columns 'Stock', 'Date', and 'Return'.
          The 'Date' column will be converted to datetime if necessary.

        Returns:
        - pd.DataFrame: A filtered DataFrame with the flagged stock-days removed.
        """
        if method not in ["drop", "zero"]:
            raise ValueError("The method parameter must be either 'drop' or 'zero'.")

        def filter_stock(group, method = method):
            group = group.sort_values('Date').reset_index(drop=True)
            ret = group['Return']

            # Define error conditions:
            cond1 = (ret > up_ts) & (ret.shift(-1) < down_ts)
            cond2 = (ret < down_ts) & (ret.shift(-1) > up_ts)
            outlier = cond1 | cond2  # | = OR condition. Mark as outlier if either condition is met

            to_replace = outlier | outlier.shift(1, fill_value=False)  # Shift outlier mask by one to also mark the second day.

            if method == 'drop':
                group = group[~to_replace]
            else:
                group.loc[to_replace, 'Return'] = 0
            return group

        panel_filtered = panel.groupby('Stock', group_keys=False)[panel.columns].apply(filter_stock)

        removed_fraction = 1 - panel_filtered.shape[0] / panel.shape[0]
        print(f"Filter (15) removes ~{round(removed_fraction * 100, 5)}% of observations")

        return panel_filtered.reset_index(drop=True)

    @staticmethod
    def filter_holidays(panel, threshold=0.005):
        """
        Filters out dates for which the number of stocks with valid (non-missing and non-zero)
        returns is less than the specified threshold (default 0.5% of total stocks).
        See filter (16) from Landis & Skouras (2021).

        Parameters:
        - panel (pd.DataFrame): DataFrame with columns 'Date', 'Stock', and 'Return'.
          The 'Date' column should be convertible to datetime.
        - threshold (float): Fraction of total stocks required for a date to be considered active.
          Default is 0.005 (0.5%).

        Returns:
        - pd.DataFrame: The filtered panel with dates not meeting the threshold removed.
        """
        # Total unique stocks in the panel.
        total_stocks = panel['Stock'].nunique()

        # For each date, count stocks with non-missing and non-zero returns.
        daily_active  = panel.groupby('Date')['Return'].apply(lambda x: (x.notna() & (x != 0.0)).sum())

        # Compute the fraction of active stocks per day.
        daily_fraction = daily_active / total_stocks
        valid_dates    = daily_fraction[daily_fraction >= threshold].index
        panel_filtered = panel[panel['Date'].isin(valid_dates)]

        removed_fraction = 1 - panel_filtered.shape[0] / panel.shape[0]
        print(f"Filter (16) removes ~{round(removed_fraction * 100, 3)}% of observations")

        return panel_filtered.reset_index(drop=True)

    @staticmethod
    def filter_penny_stocks(panel, threshold=0.05):
        """
        Filters out stocks from the investment universe for month t when their previous month's
        unadjusted close price ('UnadjClose') is in the lowest quartile of stocks available in that month.
        NA values in 'UnadjClose' are ignored when computing the quartile thresholds. See filter (21) from Landis & Skouras (2021)

        For each month t:
          - The filter computes the 25th percentile of the previous month's 'UnadjClose' values (ignoring NA).
          - Stocks with a previous month's 'UnadjClose' below this threshold are removed from month t.
          - Stocks with missing previous month's 'UnadjClose' (NA) are retained.

        Args:
            panel (pd.DataFrame): DataFrame with daily observations containing at least the columns
                                  'Date', 'Stock', and 'UnadjClose'.

        Returns:
            pd.DataFrame: The filtered DataFrame with stocks removed based on the described criteria.
        """
        panel = panel.copy()
        panel['Date'] = pd.to_datetime(panel['Date'])
        panel['Month'] = panel['Date'].dt.to_period('M')  # Extract month

        # Compute the last trading day's unadjusted close for each stock in each month
        monthly = (
            panel.sort_values('Date')
            .groupby(['Stock', 'Month'], as_index=False)
            .last()[['Stock', 'Month', 'UnadjClose']]
            .rename(columns={'UnadjClose': 'LastUnadjClose'})
        )

        # For each stock, shift the last observed unadjusted close price
        monthly['prev_UnadjClose'] = monthly.groupby('Stock')['LastUnadjClose'].shift(1)

        # Merge the previous month's UnadjClose back into the original panel
        panel = panel.merge(monthly[['Stock', 'Month', 'prev_UnadjClose']], on=['Stock', 'Month'], how='left')

        # For each month, compute the xth percentile of the previous month's UnadjClose, ignoring NA values
        panel['q'] = panel.groupby('Month')['prev_UnadjClose'].transform(lambda x: x.quantile(threshold))

        # Filter out stocks in month t if their previous month's UnadjClose is below the xth percentile.
        panel_filtered = panel[
            (panel['prev_UnadjClose'].isna()) | (panel['prev_UnadjClose'] >= panel['q'])
            ].copy()

        panel_filtered.drop(columns=['Month', 'q'], inplace=True)

        removed_fraction = 1 - panel_filtered.shape[0] / panel.shape[0]
        print(f"Filter (21) removed ~{round(removed_fraction * 100, 4)}% of observations")
        return panel_filtered


    @staticmethod
    def filter_nonsense_values(panel):
        """
        Remove stockdays with zero or negative unadjusted prices. See filter (19) from Landis & Skouras (2021):
        "We remove all stockdays for which unadjusted prices contain zero or negative values."
        Missing unadjusted prices are kept (they are not 'zero or negative').

        Parameters:
            panel (pd.DataFrame): DataFrame with column 'UnadjClose'.

        Returns:
            pd.DataFrame: The filtered panel.
        """
        panel_filtered = panel[~(panel["UnadjClose"] <= 0)]
        removed_fraction = 1 - panel_filtered.shape[0] / panel.shape[0]
        print(f"Filter (19) removes ~{round(removed_fraction * 100, 5)}% of observations")
        return panel_filtered.reset_index(drop=True)

    @staticmethod
    def filter_implausible_prices(panel):
        """
        Filters out rows for which the OHLC price data are implausible.

        Specifically, a row is removed if:
          - The High price is less than one or more of the Open, Close, or Low prices, or
          - The Low price is greater than one or more of the Open, Close, or High prices.

        Parameters:
        - panel (pd.DataFrame): DataFrame with columns 'Open', 'High', 'Low', and 'Close'.

        Returns:
        - pd.DataFrame: The filtered panel with inconsistent rows removed.
        """
        max_val = panel[['Open', 'Close', 'Low']].max(axis=1)
        min_val = panel[['Open', 'Close', 'High']].min(axis=1)

        valid_mask = ((panel['High'].isna() | (panel['High'] >= max_val)) & (panel['Low'].isna() | (panel['Low'] <= min_val)))

        panel_filtered = panel[valid_mask]

        removed_fraction = 1 - panel_filtered.shape[0] / panel.shape[0]
        print(f"OHLC inconsistency filter removes ~{round(removed_fraction * 100, 5)}% of observations")

        return panel_filtered.reset_index(drop=True)


    @staticmethod
    def filter_extreme_prices(panel, ts=1_000_000):
        """
         Filters the OHLCV panel by removing rows where any of the price columns ('Open', 'High', 'Low', 'Close')
         exceed the specified threshold, while ignoring NaN values.

         Args:
             panel (pd.DataFrame): DataFrame containing the columns 'Open', 'High', 'Low', and 'Close'.
             ts (int, optional): Maximum allowed value for the price columns (default is 1,000,000).

         Returns:
             pd.DataFrame: Filtered DataFrame.
         """
        condition = (panel[['Open', 'High', 'Low', 'Close']] <= ts) | (panel[['Open', 'High', 'Low', 'Close']].isna())
        mask = condition.all(axis=1)
        panel_filtered = panel[mask].copy()
        removed_fraction = 1 - panel_filtered.shape[0] / panel.shape[0]
        print(f"Extreme prices filter ~{round(removed_fraction * 100, 7)}% of observations")

        return panel_filtered


    @staticmethod
    def filter_decimal_errors(panel, up_ts=4.0, down_ts=-0.85):
        """
        Filters the OHLCV panel by removing rows where the 'Return' is above 4.0 (400%)
        or below -0.85.

        Args:
            panel (pd.DataFrame): DataFrame with a 'Return' column.

        Returns:
            pd.DataFrame: Filtered DataFrame.
        """
        panel_filtered = panel[(panel["Return"] <= up_ts) & (panel["Return"] >= down_ts)]

        removed_fraction = 1 - panel_filtered.shape[0] / panel.shape[0]
        print(f"Decimal error filter ~{round(removed_fraction * 100, 7)}% of observations")
        return panel_filtered


    @staticmethod
    def filter_no_trading_activity(panel):
        """
        Filters out rows from the panel where, on a per-stock basis, the 'High', 'Low', and 'Volume'
        are identical to the previous day's values.

        Args:
            panel (pd.DataFrame): DataFrame with at least the columns 'Stock', 'Date', 'High', 'Low', and 'Volume'.

        Returns:
            pd.DataFrame: Filtered DataFrame.
        """

        def drop_identical(group):
            group["High_prev"] = group["High"].shift(1)
            group["Low_prev"] = group["Low"].shift(1)
            group["Volume_prev"] = group["Volume"].shift(1)

            group["drop"] = (
                    (group["High"] == group["High_prev"]) &
                    (group["Low"] == group["Low_prev"]) &
                    (group["Volume"] == group["Volume_prev"])
            )
            group["drop"] = group["drop"].fillna(False)
            group = group[~group["drop"]]
            return group.drop(columns=["High_prev", "Low_prev", "Volume_prev", "drop"])

        panel_filtered = panel.groupby("Stock", group_keys=False)[panel.columns].apply(drop_identical)
        removed_fraction = 1 - panel_filtered.shape[0] / panel.shape[0]
        print(f"Identical HL&V filter removed ~{round(removed_fraction * 100, 7)}% of observations")

        return panel_filtered


    @staticmethod
    def filter_extreme_returns(panel, lower=0.001, upper=0.999):
        """
        Filters out rows from the panel with 'Return' values outside the specified quantile range for each Date group.

        Parameters:
            panel (pd.DataFrame): A DataFrame containing at least the columns 'Date' and 'Return'.
            lower (float): The lower quantile threshold (e.g., 0.05 for the 5th percentile).
            upper (float): The upper quantile threshold (e.g., 0.95 for the 95th percentile).

        Returns:
            pd.DataFrame: The filtered DataFrame containing only observations with 'Return'
                          between the lower and upper quantiles for each Date.
        """
        lower_threshold = panel.groupby('Date')['Return'].transform(lambda x: x.quantile(lower))
        upper_threshold = panel.groupby('Date')['Return'].transform(lambda x: x.quantile(upper))

        panel_filtered = panel[(panel['Return'] >= lower_threshold) & (panel['Return'] <= upper_threshold)].copy()

        removal_percentage = round(1 - panel_filtered.shape[0] / panel.shape[0], 3)
        print(f"Outlier filter removes ~{removal_percentage * 100:.2f}% of observations")

        return panel_filtered


    @staticmethod
    def filter_extreme_returns2(panel, n_std=8):
        """
        Filters out rows from the panel where 'Return' is more than n_std standard deviations away from
        the median, by Date.

        Parameters:
            panel (pd.DataFrame): A DataFrame containing at least 'Date' and 'Return'.
            n_std (float): The number of standard deviations to use as the cutoff (default=8).

        Returns:
            pd.DataFrame: The filtered DataFrame containing only observations whose returns
                          lie within [median - n_std*std, median + n_std*std] for each Date.
        """
        # For each date, compute the median and std of returns
        med = panel.groupby('Date')['Return'].transform('median')
        std = panel.groupby('Date')['Return'].transform('std')

        # Compute upper/lower thresholds
        upper_threshold = med + n_std * std
        lower_threshold = med - n_std * std

        # Filter out extreme observations
        panel_filtered = panel[
            panel['Return'].isna() |
            ((panel['Return'] >= lower_threshold) & (panel['Return'] <= upper_threshold))
            ].copy()

        # Print how many observations were removed
        removal_percentage = 1 - panel_filtered.shape[0] / panel.shape[0]
        print(f"Outlier filter 2 removes ~{removal_percentage * 100:.5f}% of observations")

        return panel_filtered


    @staticmethod
    def null_values(df):
        """
        Calculate and display the number and percentage of missing values for each column in the DataFrame.

        Parameters:
            df (pd.DataFrame): The DataFrame to analyze.

        Returns:
            pd.DataFrame: A table with the count and percentage of missing values for columns with missing data.
        """
        mis_val = df.isnull().sum()
        mis_val_percent = 100 * mis_val / len(df)

        mis_val_table = pd.concat([mis_val, mis_val_percent], axis=1)
        mis_val_table.columns = ['Missing Values', '% of Total Values']

        mis_val_table = mis_val_table[mis_val_table['% of Total Values'] != 0]
        mis_val_table = mis_val_table.sort_values('% of Total Values', ascending=False).round(1)

        print(f"Dataframe has {df.shape[1]} columns.\nThere are/is {mis_val_table.shape[0]} columns with missing values.")

        return mis_val_table

    @staticmethod
    def filter_adjustment_inconsistencies(panel, threshold=0.05):
        """
        Filters out stockdays with adjustment inconsistencies.

        Adjustment inconsistency is defined as a discrepancy where the unadjusted price (UP)
        differs from the product of the adjusted price (P) and the adjustment factor (AF)
        by more than the specified threshold (default 5%). See filter (18) from Landis & Skouras (2021).

        Parameters:
            panel (pd.DataFrame): A DataFrame containing at least the columns 'UP', 'P', and 'AF'.
            threshold (float): The relative discrepancy threshold (e.g., 0.05 for 5%).

        Returns:
            pd.DataFrame: The filtered DataFrame containing only stockdays where
                          |P - (UP * AF)| / (UP * AF) <= threshold.
        """
        expected_P     = panel['UnadjClose'] * panel['AdjFactor']
        diff_percentage = abs(panel['Close'] - expected_P) / expected_P

        panel_filtered = panel[(diff_percentage <= threshold) | (diff_percentage.isna())].copy()

        removal_percentage = round(1 - panel_filtered.shape[0] / panel.shape[0], 3)
        print(f"Filter (18) removes ~{removal_percentage * 100:.2f}% of observations")

        return panel_filtered

    @staticmethod
    def adjust_for_delisting(panel, delisting_return=-0.35, flag_col="DelistingReturnApplied"):
        """
        Adjusts the panel for delisting events (own addition; not part of Landis & Skouras, 2021).

        For stocks with a non-missing 'DelistingDate':
            - All observations strictly after the DelistingDate are removed.
            - The delisting return is compounded into the return of the stock's LAST remaining
              observation: Return_last = (1 + Return_last) * (1 + delisting_return) - 1.
              If Return_last is missing, it is set to delisting_return.
            - 'ReturnIndex' on that last observation is scaled by (1 + delisting_return), so that
              returns computed from 'ReturnIndex' (e.g. monthly returns via
              utils.determine_monthly_returns) contain the delisting return as well.
            - A boolean column `flag_col` marks the adjusted rows.

        Why the last remaining observation and not the row dated exactly on DelistingDate:
        Datastream's delisting date is typically later than the last trading day and filter (13)
        removes (most of) the padded days before it, so a row dated on DelistingDate rarely
        survives. The earlier implementation therefore applied the delisting return to almost no stock.

        Caveat: Datastream does not report the delisting reason. A single negative delisting
        return treats mergers/acquisitions like performance-related delistings. Set
        delisting_return=None to skip the adjustment entirely.

        Parameters:
            panel (pd.DataFrame): DataFrame containing at least the columns
                                  'Stock', 'Date', 'Return', 'ReturnIndex' and 'DelistingDate'.
            delisting_return (float or None): Delisting return (default -0.35). None disables it.
            flag_col (str): Name of the flag column.

        Returns:
            pd.DataFrame: A copy of the input panel with delisting adjustments applied.
        """
        # Remove rows that are past the DelistingDate
        keep_mask = (
                panel["DelistingDate"].isna() |
                (panel["Date"] <= panel["DelistingDate"])
        )
        panel_filtered = panel[keep_mask].copy()
        panel_filtered[flag_col] = False

        removed_fraction = 1 - panel_filtered.shape[0] / panel.shape[0]
        print(f"Delisting adjustment removes ~{round(removed_fraction * 100, 6)}% of observations (after DelistingDate)")

        if delisting_return is None:
            print("Delisting return disabled (delisting_return=None).")
            return panel_filtered.reset_index(drop=True)

        panel_filtered = panel_filtered.sort_values(["Stock", "Date"])
        is_last = ~panel_filtered["Stock"].duplicated(keep="last")
        mask_last_delisted = is_last & panel_filtered["DelistingDate"].notna()

        ret = panel_filtered.loc[mask_last_delisted, "Return"]
        panel_filtered.loc[mask_last_delisted, "Return"] = (
            (1 + ret.fillna(0.0)) * (1 + delisting_return) - 1
        )
        panel_filtered.loc[mask_last_delisted, "ReturnIndex"] = (
            panel_filtered.loc[mask_last_delisted, "ReturnIndex"] * (1 + delisting_return)
        )
        panel_filtered.loc[mask_last_delisted, flag_col] = True

        n_delisted = panel_filtered.loc[panel_filtered["DelistingDate"].notna(), "Stock"].nunique()
        print(f"Delisting return of {delisting_return} applied to {int(mask_last_delisted.sum())} of "
              f"{n_delisted} stocks with a delisting date.")

        return panel_filtered.sort_values(["Date", "Stock"]).reset_index(drop=True)
