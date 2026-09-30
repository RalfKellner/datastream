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

## 6. Open points for the next step (data sanity and availability)

Not implemented yet, but the import log already stores what these checks need:
- Coverage per variable × year relative to the price universe (firms alive in the filtered price panel),
  optionally by size bucket.
- Values after a firm's delisting date (Datastream padding) and stale repetitions.
- When values change within a year relative to fiscal year end (`WC05350`) and report date, to decide on
  the availability lag for point-in-time merges with returns.
- Units and scale (Worldscope items in thousands, currency), outliers and sign errors.
