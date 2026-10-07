# Firm-variable import restructure (branch `firm-data/import-restructure`)

The import of monthly firm variables is split into two independent steps, and an inventory of all
variables is maintained automatically. **None of this touches the price pipeline or the Landis & Skouras
filters.** The only change outside the firm-variable code is the repaired `.gitignore` (section 4).

## 1. New workflow

| Step | Command | Output (below `D:/Datastream/Firmcharacteristics_Monthly/US`) |
|---|---|---|
| 0. Templates for a new variable | `uv run python scripts/00_setup_folder_structure_firmdata.py WC03501` | `WC03501/WC03501_01.xlsx` ... `_39.xlsx` |
| 1. Import (per variable) | `uv run python scripts/03_import_firm_variables.py` | `Paneldata/variables/<VAR>.parquet` |
| 2. Merge (optional) | `uv run python scripts/04_merge_firm_panel.py --name all_vars --all` | `Paneldata/merged/all_vars.parquet` + `.json` |
| Overview | written by steps 1 and 2, or `03_... --inventory-only` | `Paneldata/variable_inventory.csv` |

Step 1 without arguments imports only variables that are **new or whose Excel files changed** since their
last import (file modification time). `03_... WC02999 WC01001` imports specific variables, `--all` re-imports
everything. Other machine: `--root <folder>` or the environment variable `DS_FIRM_ROOT` (folder that
contains `US/`, `EU/`).

Code: `src/datastream/preprocessing/firm_data.py`. Tests: `tests/test_firm_data.py` (synthetic files in the
add-in layout). The old notebooks are kept unchanged in `scripts/legacy/` for comparison.

## 2. What the import does differently from the legacy notebook

| Topic | Legacy notebook | Now |
|---|---|---|
| Unit of work | loop over a hard-coded variable list, all re-imported every time | one variable per call, only new/changed ones by default |
| Output format | CSV per variable | Parquet per variable (typed `Date`, `DSCD` string, float value), written atomically |
| Failed file | printed, panel still written without that list, error raised at the end | panel **not written** (older panel kept, inventory shows `incomplete`/`blocked`); `--allow-incomplete` overrides |
| Unfilled template (empty workbook) | raised an error (possibly why `WC02999_23.xlsx` failed on `main`) | detected and reported as `empty_template` |
| File selection | all `.xlsx` in the folder | `<VAR>_*.xlsx`, Excel lock files `~$...` ignored; 2- and 3-digit list numbers work (`ENERO132V_001.xlsx`) |
| Unparsable series codes | became a column named `None` | dropped and counted |
| `#ERROR` columns | dropped silently | dropped and counted (= requested firms Datastream could not deliver) |
| Non-numeric cells (`$$ER ...`) | coerced silently | coerced, counted, examples in the report |
| Same DSCD in two lists | would have produced duplicate rows | first file kept; count and conflicting values written to `_reports/<VAR>_duplicates.csv` |
| Documentation of the import | none | `_reports/<VAR>_files.csv` (one row per list) and `_import_log.csv` (one row per variable) |

The ID parsing, the two skipped metadata rows and dropping missing values are the same as before, so the
content of a variable panel should equal the legacy CSV except for the handled edge cases above.
**Check:** compare `Paneldata/variables/WC02003.parquet` with the legacy `Paneldata/WC02003.csv` (row count
and values) once after the first run.

## 3. Merge step

- Outer join of any set of variable panels on (`DSCD`, `Date`), via index-aligned `concat` instead of a
  chain of `merge` calls.
- **Dates are aligned to the calendar month end by default.** That is the convention of the monthly returns
  from the price panel (`resample("ME")`), and it prevents a silent problem: if two variables were
  downloaded with different date settings (e.g. first of month vs. month end), the legacy outer merge put
  them into separate rows of the same firm-month. With `--no-align`, differing conventions raise an error.
  If two raw dates of one firm fall into the same month, the merge stops (data not monthly).
- The sidecar `<name>.json` records the variables, alignment, row/firm counts and the modification time of
  each variable panel used. The inventory reads these sidecars (`in_merged` column), so you can see which
  variables are part of which dataset and whether a dataset is older than its inputs.
- The legacy notebook dropped `EPSISURDTE`/`EPSISURSUE`; now you simply choose the variables to merge.

## 4. Inventory and variable registry

`config/firm_variables.csv` is a hand-maintained registry (mnemonic, description, category, source). The
descriptions of the Worldscope/Datastream items were filled in from memory and are marked `verified=no`;
please check them in the Datastream Navigator. The environmental items (`ENERO...`) still need a description.

Variables that are computed from downloaded data (returns, `bm`, `ep`, `dy_12m`, `<var>_prev`, `fund_*`,
size groups, spreads, flags) are documented in `config/derived_variables.csv` (formula, inputs, unit, stage and
the function that creates them). The registry itself only lists Datastream downloads.

To drop a variable: remove it from the registry, then run
`uv run python scripts/00_drop_firm_variables.py <VAR ...> --region US` (and `--region EU`). Raw files and panels
are moved to `<root>/_dropped/`, the variable's rows are removed from the import logs (backup in `_dropped/`) and
the inventory is refreshed. Use `--dry-run` first.

`Paneldata/variable_inventory.csv` combines the registry, the raw folders, the import log and the merged
sidecars. Key columns: `panel_state` (missing / current / stale / incomplete / blocked), `n_raw_files`,
`n_series` (firms requested), `n_error_series`, `n_firms_with_data`, `share_firms_with_data`, `n_obs`,
`first_date`, `last_date`, `date_convention`, `in_merged`.

## 5. Other changes

- `scripts/00_setup_folder_structure_firmdata.py`: takes the variables as arguments and **no longer
  overwrites existing files**. The old version called `Workbook().save()` for every file, so running it
  again for an existing variable replaced downloaded data with empty workbooks.
- `.gitignore` rewritten as plain ASCII. The line added with PowerShell `echo` was UTF-16; Git read it as
  `*` and therefore ignored **every new file** in the repository. After merging, run `git status` on both
  machines: files created since 2026-09-28 may show up as untracked for the first time.

## 6. Static variables (second commit)

Some items only exist as static requests (e.g. `ENERDP124` emissions estimation method, `WC05350` fiscal
year end) and only return the **current** value. They have their own reader (`src/datastream/preprocessing/
static_data.py`) and their own output; `scripts/03_import_firm_variables.py` imports both kinds.

| | Time series | Static |
|---|---|---|
| Raw files | `<root>/<VAR>/<VAR>_nn.xlsx` | `<root>/Static/<VAR>/<VAR>_nn.xlsx` (+ dated subfolders, see below) |
| Excel layout | codes in first data row, dates in rows | `Type \| <VAR> \| CURRENCY`, one row per firm |
| Output | `Paneldata/variables/<VAR>.parquet`: `Date \| DSCD \| VAR` | `Paneldata/static/<VAR>.parquet`: `DSCD \| VAR \| as_of` |
| Value type | float | detected per variable: string, datetime or numeric |

**Snapshots.** Because only the current value is delivered, history can only be built going forward. Each
download is a snapshot with an `as_of` date:
- files directly in `Static/<VAR>/`: `as_of` = date of the newest file;
- files in `Static/<VAR>/YYYY-MM-DD/`: `as_of` = folder name.

Before downloading a static variable again, move the current files into a folder named after their download
date (e.g. `Static/ENERDP124/2026-09-30/`). The static table is rebuilt from all raw snapshots on every
import, so the history is reproducible from the raw files. `load_static(root, var)` returns the latest
value per firm, `load_static(root, var, as_of="2027-06-30")` the value known at that date, `as_of=None` all
snapshots. The import log reports `n_changed_vs_previous` (firms whose value changed since the previous
snapshot).

**Details of the reader**
- DSCDs that Excel stored as numbers (e.g. `902242`) are converted to strings; codes shorter than 6
  characters are zero-padded and counted (`n_padded_ids`), since Excel drops leading zeros.
- `#NA`/empty cells are counted as missing, `$$ER`/`#ERROR` values as errors (with examples in the report).
- `_reports/<VAR>_values.csv` shows the distribution per snapshot: categories for string variables
  (ENERDP124: `CO2`, `Median`, `Reported`, `Energy`), fiscal-year-end month for date variables (WC05350).
- Unfilled templates or failed files block the write, as for time series.

**Other adjustments:** `discover_raw_variables` skips `Static`, `Paneldata` and folders starting with `_`.
The registry has a new column `type` (timeseries/static), and the inventory has one row per variable and
type, with `n_snapshots`, `latest_as_of`, `value_type` and `n_distinct_values` for static variables. Asking
the script for a variable without raw files no longer counts as a failed import.

Checked on the two example files (list 01): ENERDP124 has a value for 43 of 1,000 firms, WC05350 for 890
of 1,000 (691 with a December fiscal year end).

## 7. Open points for the next step (data sanity and availability)

Not implemented yet, but the import log already stores what these checks need:
- Coverage per variable × year relative to the price universe (firms alive in the filtered price panel),
  optionally by size bucket.
- Values after a firm's delisting date (Datastream padding) and stale repetitions.
- When values change within a year relative to fiscal year end (`WC05350`) and report date, to decide on
  the availability lag for point-in-time merges with returns.
- Units and scale (Worldscope items in thousands, currency), outliers and sign errors.

## 8. Sanity checks for the time-series firm variables (third commit)

New: `src/datastream/firm_evaluation.py`, `analyses/02_firm_variable_checks.ipynb`,
`scripts/05_build_monthly_universe.py`, `config/firm_relations.csv`, `tests/test_firm_evaluation.py`,
`tests/synthetic_firm_data.py`. Nothing in the datasets is changed.

**Run order (Windows):**
1. `uv run python scripts/05_build_monthly_universe.py` - reads only `Stock, Date, MarketCAP, Close, MTBV` of
   `US_data_panel_filtered_0.2.feather`, record batch by record batch, and writes
   `monthly_universe_0.2.parquet` (one row per stock-month in the filtered universe, with month-end market cap,
   first/last price month, delisting date and size group with NYSE breakpoints from `EXMNEM`). Rerun after
   every new run of `02_filter.py`.
2. Import the static `WC05350` (for the reporting lag) and the time-series variables with
   `03_import_firm_variables.py`.
3. Run `analyses/02_firm_variable_checks.ipynb`. Paths in the configuration cell; `DS_FIRM_ROOT` and
   `DS_PRICE_PATH` override them. Tables and figures go to `Paneldata/checks/`.

**Battery**

| Block | Check | Function |
|---|---|---|
| A | EW and VW coverage of the price universe per month | `coverage_by_month` |
| A | coverage by size group (Q1 smallest) per year | `coverage_by_size` |
| A | firms, firm-months, mean/median/trimmed mean per year | `yearly_overview` |
| A | history length, gaps inside histories, matching with the universe | `history_stats`, `matching` |
| B | value changes per firm-year (0 / 1 / 2-3 / 4 / 5+) | `update_frequency` |
| B | month of change relative to the fiscal-year end (WC05350) | `reporting_lag` |
| B | values after the last price / delisting, stale runs > 24 months while trading | `stale_and_padding` |
| C | yearly quantiles of firm-year values | `yearly_quantiles` |
| C | zeros, sign violations (registry column `sign`), robust-z outliers | `implausible_values` |
| C | unit jumps: change by >= ~316x, or by >= ~8x reversed within 24 months (one row per episode) | `unit_jumps` |
| D | identities, bounds, ranges and recomputed ratios from `config/firm_relations.csv` (December cross-sections) | `evaluate_relations` |
| D | share of firm-years in which two variables update in the same month | `update_alignment` |

The summary table flags a variable when a number is outside `firm_evaluation.THRESHOLDS`. These thresholds
are heuristics to direct attention, not filters.

**Design choices worth knowing**
- Firm-year values are the last value of each firm in each calendar year. Relations use December
  cross-sections, so that monthly repetitions of the same annual value are not counted twelve times.
- Units: Datastream MV is in millions and Worldscope items are assumed to be in thousands. The `mtbv` relation
  (MTBV vs. 1000 x MV / common equity) reports the median ratio, which confirms or refutes this assumption.
- The reporting lag uses the **current** fiscal-year end; firms that changed their fiscal year blur the
  distribution, so read the mode.
- `compare` relations (ROE, ROA, D/E, PE) are informational: Worldscope definitions differ from the simple
  recomputation (average equity/assets, interest add-back).
- Registry: new column `sign` (`nonneg` for items that cannot be negative) and entry `WC01751` (net income).

**Tests.** `tests/synthetic_firm_data.py` builds a price panel and firm variables with planted errors (padding
after delisting, stale values, x1000 reversals and a permanent jump, identity violations, negative assets,
firms outside the universe, poor small-cap coverage). `tests/test_firm_evaluation.py` checks that each one
is detected and that the batch-wise universe equals a direct computation. Scale test: 3,000 stocks over 32
years (23M stock-days) take about 35 s for the universe and about 4 s per variable.

## 9. Refinements after the first run on real data (fourth commit)

The first run on the US data showed where the checks over- or under-reported. Changes:

| Topic | Before | Now | Why |
|---|---|---|---|
| Observations used | all firm-months of a variable | coverage, history and padding: all; timing (B1, B2), levels (A4), distributions and jumps (C), relations (D1), alignment (D2): **only firm-months in the price universe** | Datastream pads DY/EPS/PE until today for ~95% of dead firms; lists also contain non-common stocks and SPAC units. These distorted zeros, negatives, medians and staleness. |
| Staleness | identical values > 24 months, incl. zeros; denominator all obs | non-zero runs only, share of universe firm-months; zero runs reported separately (`share_zero_runs_while_trading`) | Zero debt, zero dividends and zero controversies are economic, not stale. |
| Unit jumps | flagged per variable (factor >= ~316, or >= ~8 reversed) | per-variable jumps only for `nonneg` level variables (`n_large_jumps`, informational); **unit errors** = firm-months where >= 3 items jump by the same power of 1000 (`unit_error_candidates`, `n_unit_errors`, flag) | Most single-item jumps were SPACs (2021 wave) and cash / short-term debt; only 8 firm-months showed jumps in >= 5 items. |
| Coverage flag | last full year | reference year, default the year before the last full year (`COVERAGE_YEAR` in the notebook) | ESG values for the latest year are published with a delay. |
| Relations | all December firm-years; `within 5%` counted 0/0 as a miss | universe firm-years; optional `condition` column (EBITDA margin only for sales >= USD 10m); `within 5%` over non-zero rhs, `share_both_zero` separately | Ratios of zero-debt firms; margins of pre-revenue firms are economic. |
| Empty panels | shown with zero coverage and flags | skipped (`check_variable` returns None) | Leftovers of earlier imports (WC05350, ENERDP124 as time series) and the date-valued EPSISURDTE. |
| Robust z | could explode for (near) constant cross-sections | no z-score when the MAD is ~0 | |

Registry: new variables added (WC01250, ENERDP023/024/025/123, ENSCORE, TRESGS, ESGCCSC, ESGCCBDSC, EPSISURSUE,
EPSISURDTE); descriptions that were not certain are left as "fill in description". Environmental and score
variables are marked `nonneg`.

**Findings of the first run that remain valid** (and matter for building the panel):
- Worldscope and ESG values change one month after fiscal year end for ~90% of updates, and all items of a firm
  change in the same month: Datastream dates values at the fiscal period, not at publication. A point-in-time
  panel needs an availability lag (>= 4 months for Worldscope, conventionally 6; longer for ESG).
- Units: MTBV vs. 1000 x MV / common equity has a median ratio of 1.000, confirming MV in millions and Worldscope
  in thousands.
- Total debt identity violated in 0.08% of firm-years; PE matches price / EPS in 99.6%.

## 10. Baseline panel: price universe + Worldscope, point in time (fifth commit)

New: `src/datastream/panel_builder.py`, `scripts/06_build_baseline_panel.py`, `tests/test_panel_builder.py`.
Changed: the monthly universe now also contains month-end `ReturnIndex` (**rebuild it once** with
`scripts/05_build_monthly_universe.py`).

    uv run python scripts/05_build_monthly_universe.py
    uv run python scripts/06_build_baseline_panel.py                    # rolling, lag 3, max age 18
    uv run python scripts/06_build_baseline_panel.py --convention ff    # Fama-French June timing (max age 24)

Output in `Paneldata/baseline/`: `baseline_rolling_0.2.parquet`, a JSON sidecar with all parameters and cleaning
counts, and `..._coverage_by_year.csv` (share of universe stock-months with a value).

**Rows.** Exactly the stock-months of the filtered price universe. Firm data never add rows, so padded values
after delisting, pre-listing months and securities outside the universe cannot enter.

**Timing (rolling convention).**
1. *Report months* per firm: first month with data and every month in which any Worldscope item changes (the
   checks showed that all items of a firm change in the same month, ~1 month after fiscal year end).
2. At each report month all items are taken as one snapshot, including unchanged ones (zero debt keeps being
   reported and is not aged out).
3. A snapshot is available from `report month + lag` (default 3, i.e. ~4 months after fiscal year end) and is
   used until the next snapshot becomes available, at most `max_age` months after its report month (default
   18). Columns `fund_report_month`, `fund_available_month` and `fund_age_months` document this per row.

`--convention ff`: the fiscal year ending in calendar year t (fiscal year end = report month - 1) is used from
June of t+1 to May of t+2 (max age 24).

**Cleaning** (counts in the JSON sidecar): negative values of `nonneg` items set to missing; unit-error episodes
(>= 3 items jump by the same power of 1000) set to missing for the involved items until they change again.

**Market data and derived variables.**
- `ret` from month-end ReturnIndex over consecutive months (includes the delisting return of 02_filter.py; the
  same construction as the market sanity checks), `retx` from Close.
- `bm` = WC03501 / (1000 x MarketCAP), `ep` = WC01751 / (1000 x MarketCAP): point-in-time fundamentals with the
  current market cap (rolling, as in Asness & Frazzini 2013), not the December market cap of Fama-French.
- `dy_12m` = sum of the last 12 monthly dividend returns (ret - retx, floored at 0). Datastream's DY, PE and EPS
  are not used in the baseline: their timing is unclear and they are padded after delisting.

**Tests** (synthetic data with values changing 3 months after fiscal year end): the baseline value in month t
equals the raw value in t-3 (no look-ahead), zero debt persists, firms that stop reporting expire after 18
months, sign and unit errors are removed while an economic (SPAC-like) jump is kept, returns and ratios match,
and the FF convention changes values only in June.

## 11. Validation of the baseline panel (sixth commit)

New: `src/datastream/validation.py`, `analyses/03_baseline_validation.ipynb`, `tests/test_validation.py`.
Changed: `panel_builder` adds previous-report values (`WC02999_prev`, `WC01751_prev`, `WC03501_prev`,
`WC01001_prev`, `fund_prev_report_month`: latest report at least 9 months older, available together with the
current report), needed for asset growth and earnings growth. **Rebuild the baseline panels.**

    uv run python scripts/06_build_baseline_panel.py                     # rolling (sections 2-4)
    uv run python scripts/06_build_baseline_panel.py --convention ff     # Fama-French timing (section 1)

| Section | Test | Benchmark |
|---|---|---|
| 1 | SMB, HML, RMW, CMA rebuilt with the FF method (2x3, NYSE breakpoints from the current `EXMNEM`, June formation, B/M with December ME, VW July-June) vs. Ken French's factors | corr SMB >= 0.95, HML ~0.85-0.95, RMW/CMA ~0.8-0.9 |
| 2 | Fama-MacBeth: next-month return on log size, log B/M, momentum (t-11..t-1), 1-month reversal, operating profitability, asset growth, E/P; winsorized 1/99, standardized per month, Newey-West t; all stocks, without the smallest NYSE size quintile, subperiods | signs as in the literature (column `expected`) |
| 3 | Decile sorts EW/VW and D10-D1 spreads | same signs as 2 |
| 4 | Event study: market-adjusted returns from -3 to +12 months around report months, by earnings-growth quintile | most of the reaction before the availability month, small drift after |

Profitability uses operating income / book equity; set `INTEREST_VAR = "WC01251"` in the notebook after
downloading interest expense to get the FF definition (revenue - COGS - SG&A - interest) more closely.

Tests: Fama-MacBeth and decile sorts recover planted effects; the FF construction gives zero factors when all
stocks have the same return and runs on the synthetic panel; the event study finds a planted reaction in month
+1 and nothing elsewhere; characteristics are aligned (return of t+1, momentum over t-11..t-1). The notebook
was run end to end on synthetic data (the sandbox cannot reach the French data library, so the comparison
there used stand-in factors).

## 12. Readable variable names (seventh commit)

New: `src/datastream/naming.py`, `datatypes/pricedata_variables.md` (Datastream datatype descriptions; the
earlier commit of this folder on `main` did not include it because of the broken `.gitignore`).

- **Registry:** `config/firm_variables.csv` has a new column `name` (e.g. `WC02999` -> `total_assets`,
  `WC03501` -> `common_equity`, `ENERDP024` -> `co2e_scope1`) and the descriptions from the datatypes file.
  Entries marked `verified=no` need a check (net income WC01751 and interest expense WC01251 are not in the
  file). ENERO52V = scope 1 intensity, ENERO55V = scope 2 intensity, as defined in Datastream (the datatypes
  file first listed both as scope 1).
- **Where names are used:** only in output datasets. Raw files, imported variable panels, import logs and the
  checks keep the Datastream mnemonics, so every value can be traced back to Datastream and new downloads need
  no mapping. The baseline panel (default) and merged panels (`04_merge_firm_panel.py --names`) use readable
  names; the JSON sidecars store the mapping (`column_names`). `06_build_baseline_panel.py --mnemonics` keeps
  the mnemonics.
- **Price and static columns in the baseline:** `market_cap` (MV, USD m), `price` (P), `mtbv`, `return_index`
  (RI), `n_trading_days`; and, joined from `statics_filtered_<p>.csv` (current values, not point in time):
  `company_name`, `isin`, `ticker`, `exchange`, `trbc_economic_sector`, `trbc_business_sector`,
  `trbc_industry` (those present in the statics). The daily price panel of `02_filter.py` keeps its names
  (MarketCAP, ReturnIndex, ...); renaming it would require rerunning the daily pipeline.
- `validation.py` and notebook 03 use the readable names; notebook 02 and the inventory show the name next to
  the mnemonic.
