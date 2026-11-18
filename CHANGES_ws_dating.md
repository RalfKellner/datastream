# Dating of annual Worldscope / Refinitiv ESG values in Datastream (November 2026)

Branch `fix/ws-fiscal-dating`. Affects the baseline panel (`41_build_baseline_panel.py`, US and EU) and the
event study in `03_baseline_validation.ipynb`. The price data and the filters are not affected.

## Finding

Requested at monthly frequency, Datastream repeats an annual value in **every month of the fiscal year it
describes**, i.e. from the **first month of that fiscal year** on. The month in which a value changes (the
"report month" of `panel_builder.report_snapshots`) is therefore the first month of the fiscal year, not
"one month after fiscal year end" as assumed in `CHANGES_firm_import.md` (section on the findings of the first
run). The month was right, the fiscal year was off by one.

Evidence (`scripts/diagnostics/check_ws_dating.py`, US data, November 2026):

| Firm (fiscal year end) | value shown from | WC01001 | is fiscal year |
|---|---|---|---|
| American Airlines (Dec) | 2020-01 | 17,337,000 (2019-01: 45,768,000) | 2020 (COVID) |
| Exxon Mobil (Dec) | 2020-01 | 178,574,000 | 2020 |
| Walmart (Jan) | 2020-02 | 559,151,000 | FY ending Jan 2021 |
| Microsoft (Jun) | 2020-07 | 168,088,000 | FY ending Jun 2021 |

Median log sales growth at the January updates (December firms): 2019 +0.069, **2020 +0.009, 2021 +0.183**;
emissions (ENERDP023) 2020 -0.128. Emission growth correlates best with sales growth of the same update
(`32_check_emissions.py`, table 16), so ESG values follow the same convention.

## Consequence for the old panels

With "report month + 3" the fiscal-2020 accounts of a December firm were used from April 2020 instead of
about April 2021: roughly **12 months of look-ahead** in all Worldscope characteristics of the rolling panel
(bm, ep, op, ag, ...). The ff convention computed the fiscal year end as report month - 1 and formed the June
portfolios one year too early as well. Results built on panels from before this change should be rebuilt.

## Change

* `BaselineConfig.stamp_to_fye_months = 11`: fiscal year end = report month + 11 (`panel_builder.fiscal_year_end`),
  stored as `fund_fye_month`. Replaces `fye_offset_months` (old meaning: report month - 1).
* `lag_months` now counts from the fiscal year end; default **4** (keeps the original intention "about four
  months after fiscal year end"; 10-K deadlines are 60-90 days). `--lag 6` for the conventional six months.
* `max_age_months` counts from the fiscal year end (defaults 18 / 24 unchanged); `fund_age_months` = months
  since fiscal year end.
* `ff`: the fiscal year ending in calendar year t is used from June of t+1 (now with the correct fiscal year).
* `41_build_baseline_panel.py`: `--lag`, `--max-age` relative to fiscal year end, new `--stamp-to-fye`.
* Event study: anchored at the fiscal year end (`report_events` adds `fye_month`, `event_study(anchor=...)`),
  window -12..+12 in the notebook; the availability month is fiscal year end + `LAG_MONTHS` (4).
* Tests: `tests/test_ws_dating.py` with the real Datastream convention (value of fiscal year Y in all months of
  Y). The older synthetic data change 3 months after fiscal year end; those tests now set
  `stamp_to_fye_months=-3`.

A transition year (fiscal year change) gets fiscal year end = report month + 11 as well, which is later than
the true end of a short year (conservative). A value that changes within a fiscal year (restatement) is
treated as a new snapshot with its own fiscal year end + 11 (also conservative).

## To do after merging

1. Rebuild: `41_build_baseline_panel.py` (rolling and `--convention ff`), US and EU; `42_check_bm_consistency.py`.
2. Rerun `03_baseline_validation.ipynb`: FF factor correlations and the event study with the new timing.

## Fiscal year end from the monthly WC05350 series (branch `firm-data/fiscal-period-end`)

Requested as a monthly time series next to the other Worldscope items, `WC05350` (fiscal period end date)
gives the fiscal year end of the value shown in each month (American Airlines: 2020-01 ... 2020-12 show
2020-12-31). The static download of WC05350 has only the latest value.

* Import: `firm_data.DATE_VARIABLES` (`WC05350`); date cells, Excel day numbers and date strings become
  `datetime64` values (`Paneldata/variables/WC05350.parquet`). Download into `<firm root>/WC05350/` like any
  other time-series variable (request `X(WC05350)`; `DPL#(X(WC05350),6)` works as well).
* Registry: `WC05350`, category `fiscal_period`; excluded from the panel variables of `41_build_baseline_panel.py`.
* Baseline panel: `panel_builder.snapshot_fye` takes the fiscal year end of each snapshot from WC05350 in its
  report month where available and plausible (report month <= FYE <= report month + 23), else report month + 11.
  This handles fiscal-year changes and short transition years. The JSON sidecar reports under
  `fiscal_year_end` how many snapshots have WC05350, the share equal to the +11 rule and the differences.
* Event study (`validation.report_events`) uses the same fiscal year ends.
