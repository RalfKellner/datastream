# Datastream research datasets

Pipeline from LSEG Datastream downloads to filtered daily price panels and point-in-time monthly firm panels,
for the U.S. and Europe. The price filters follow Landis & Skouras (2021, JBF); deviations are documented in
the `CHANGES_*.md` files and in `NOTES_EU_data.md`.

Scripts are numbered by stage: **1x** universe and lists, **2x** price data, **3x** firm data, **4x** monthly
panel, **9x** utilities. Run them in this order; the number gaps leave room for new steps. All scripts accept
`--help`. Run them from the repository root with `uv run python scripts/<script> ...`.

```
1x  Navigator exports ─► 10 domestic listings ─► 11 LSEG lists ─► 12/13 folders
                                                                  │
        Datastream Excel add-in: STATIC, daily prices, monthly firm data
                                                                  │
2x  20 check statics ─► 21 load price panels ─► 22 filter (US | EU) ─────────────┐
3x  30 import firm variables ─► 31 merge (optional)                              │
4x  40 monthly universe ◄────────────────────────────────────────────────────────┘
        └─► 41 baseline panel (+ firm variables from 30) ─► 42 check bm/ep
```

## Step by step

### 1. Universe and lists (only when the universe changes)

| Step | What | Command / tool |
|---|---|---|
| 1.1 | Export the equity lines per country from Datastream Navigator (Category = Equities; columns incl. `DSCD`, `Exchange`, `Market`, `RIC`). One `.xlsx` per country in one folder. | Navigator |
| 1.2 | Keep only lines on domestic exchanges (Landis & Skouras). Exchange names per country: `config/eu_domestic_exchanges.csv`; countries: `config/eu_countries.csv`. Review `warnings.txt` and `exchange_report.csv` (RIC suffixes as cross-check). | `10_select_domestic_listings.py --inp <exports> --out <folder>` → `EU_DSCD.csv` |
| 1.3 | Batches of 1,000 codes for the LSEG lists; create one Datastream list per column (`L#EU001`, ...). | `11_create_ds_lists.py --input EU_DSCD.csv --prefix EU --digits 3` |
| 1.4 | Empty download templates (existing files are never overwritten). | `12_setup_folders_pricedata.py --region EU --n-lists 36`<br>`13_setup_folders_firmdata.py <VARS ...> --region EU --n-lists 36` |

U.S.: the current lists come from one Navigator export of U.S. equities (39 lists, `L#US01` ...). Step 1.2 can be
used for the U.S. as well by adding a `United States` row (NYSE, NASDAQ, NYSE American, NYSE Arca) to the
exchange configuration.

### 2. Download (Datastream Excel add-in)

* **Price data**, daily, one folder per list (`D:/Datastream/PriceData/<region>/<nn>/<VAR>_<nn>.xlsx`), request
  `DPL#(X(<VAR>),6)`. U.S.: AF MTBV MV P PH PL PO RI STATIC UP VO. Europe: the same in local currency plus
  `MV_EU`, `RI_EU`, `UP_EU` with `DPL#(X(<VAR>)~E,6)` (EUR at daily market rates). Download `STATIC` first.
* **Firm data**, monthly, local currency (no `~E`: an EUR conversion changes values every month and breaks the
  report-month detection), `DPL#(X(<VAR>),6)`, into `D:/Datastream/Firmcharacteristics_Monthly/<region>/<VAR>/`.
  Request rows: `scripts/vba/GenerateFirmDataRows_EU.bas` (set `ROOT`, `LIST_PREFIX`, `N_LISTS`, `START_DATE`);
  run all rows: `scripts/vba/AutomaticRequest_EU.bas` (restartable, skips finished rows).
* **Static firm variables** (`WC05350` fiscal year end, `ENERDP124` emission estimation method) go into
  `<firm root>/Static/<VAR>/`.
* Which firm variables exist: `config/firm_variables.csv` (downloads only). Variables computed by the pipeline:
  `config/derived_variables.csv`.

### 3. Price data

| Step | What | Command |
|---|---|---|
| 3.1 | Right after the STATIC download: every code of the lists has static data, every folder holds the right list, no Excel-mangled codes. | `20_check_statics.py --root D:/Datastream/PriceData/EU --batched <EU_DSCD_batched.xlsx> [--dscd EU_DSCD.csv]` |
| 3.2 | Raw Excel files → one daily panel per list (+ `statics.csv`). | `21_load_price_panels.py --region EU` |
| 3.3 | Landis & Skouras filters, delisting return, penny stocks (lowest quartile), spreads. | U.S.: `22_filter_prices_us.py`<br>Europe: `22_filter_prices_eu.py` (country by country; `--resplit` after re-importing panels) |

Outputs: U.S. `US_data_panel_filtered_0.25.feather`; Europe `EU_data_panel_filtered_0.25/<COUNTRY>.feather`
(load with `datastream.utils.load_filtered_eu`), `filter_report_0.25.csv`; both `statics_filtered_0.25.csv`.
Checks: `analyses/00_data_universe_check.ipynb`, `analyses/01_market_sanity_checks.ipynb` (U.S.).

### 4. Firm data

| Step | What | Command |
|---|---|---|
| 4.1 | Import every new or changed variable into its own panel; refresh the inventory (`Paneldata/variable_inventory.csv`). | `30_import_firm_variables.py --region EU` |
| 4.2 | Optional: merge variables into one panel. | `31_merge_firm_variables.py --region EU --name all_vars --all` |

Check: `analyses/02_firm_variable_checks.ipynb`.

### 5. Monthly panel

| Step | What | Command |
|---|---|---|
| 5.1 | Daily panel → one row per stock-month (market cap, returns, size groups). | `40_build_monthly_universe.py --region EU` |
| 5.2 | Point-in-time baseline panel (availability lag 3 months, max age 18) with `bm`, `ep`, `dy_12m`, previous-year values. | `41_build_baseline_panel.py --region EU` |
| 5.3 | After every rebuild: `mtbv * bm` should be 1 per line (currency and share-class check). | `42_check_bm_consistency.py --region EU` |

Check: `analyses/03_baseline_validation.ipynb`.

### 9. Utilities

* `90_drop_firm_variables.py <VARS> --region <US|EU>`: remove variables from a region (after removing them from
  `config/firm_variables.csv`); files are moved to `<root>/_dropped/`.
* `scripts/legacy/`: superseded notebooks and diagnostics, kept for reference.

## Updating the data (e.g. every few months)

1. **New listings?** Re-run step 1 with fresh Navigator exports and new LSEG lists. Otherwise keep the lists.
2. **Re-download** STATIC, price data and firm data into the existing folders (the requests have no end date, so
   Datastream delivers up to the latest date). Keep a copy of the previous outputs if you need the old vintage:
   outputs are overwritten (their names contain the penny quantile, not a date).
3. Run 20 → 21 → 22 (Europe with `--resplit`) → 30 (picks up changed raw files automatically) → 40 → 41 → 42.
4. Compare the new `filter_report_*.csv`, `variable_inventory.csv` and the analysis notebooks with the previous
   run.

## Configuration and documentation

| File | Content |
|---|---|
| `config/eu_countries.csv` | Sample countries, EU/EEA/euro dates, currencies |
| `config/eu_domestic_exchanges.csv` | Domestic exchanges per country (Navigator names) |
| `config/firm_variables.csv` | Firm variables to download (mnemonic, readable name, type, sign) |
| `config/derived_variables.csv` | Variables computed by the pipeline (formula, inputs, function) |
| `config/firm_relations.csv` | Accounting identities used by the firm-variable checks |
| `NOTES_EU_data.md` | European data: currencies, filters, Worldscope currency and bm/ep checks |
| `CHANGES_review.md`, `CHANGES_filter1_name_screen.md`, `CHANGES_firm_import.md` | Changes to the filters and the firm-data pipeline (old script numbers, see below) |

## Open items

* Per-share `bm`/`ep` for multi-class firms with `WC05301` (common shares outstanding), see `NOTES_EU_data.md`.
* European version of the market sanity-check notebook (benchmark: e.g. STOXX Europe 600 / MSCI Europe).

## Old script names (before October 2026)

| Old | New |
|---|---|
| `eu_00_filter_domestic_exchanges.py` | `10_select_domestic_listings.py` |
| `00_create_batched_ds_lists.ipynb` | `11_create_ds_lists.py` (notebook in `legacy/`) |
| `00_setup_folder_structure_pricedata.py` | `12_setup_folders_pricedata.py` |
| `00_setup_folder_structure_firmdata.py` | `13_setup_folders_firmdata.py` |
| `eu_01_check_statics.py` | `20_check_statics.py` |
| `01_load_merge_panel.py` | `21_load_price_panels.py` |
| `02_filter.py` / `eu_02_filter.py` | `22_filter_prices_us.py` / `22_filter_prices_eu.py` |
| `03_import_firm_variables.py` | `30_import_firm_variables.py` |
| `04_merge_firm_panel.py` | `31_merge_firm_variables.py` |
| `05_build_monthly_universe.py` | `40_build_monthly_universe.py` |
| `06_build_baseline_panel.py` | `41_build_baseline_panel.py` |
| `eu_07_check_bm_consistency.py` | `42_check_bm_consistency.py` |
| `00_drop_firm_variables.py` | `90_drop_firm_variables.py` |
| `diagnostics_review_changes.py` | `legacy/diagnostics_review_changes.py` |
