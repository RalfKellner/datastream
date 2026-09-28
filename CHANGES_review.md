# Code review changes (branch `review/fixes-2026-09`)

This file documents every change made on this branch and why it was made. Nothing here has been run on the
real Datastream data yet. All changes were tested on synthetic data only (`tests/test_filters.py`, plus a full
run of `scripts/02_filter.py` on a synthetic panel). Please compare the outputs of `main` and this branch
before merging (see "How to check the consequences" at the end).

Reference: Landis, C., Skouras, S. (2021). Guidelines for asset pricing research using international equity
data from Thomson Reuters Datastream. *Journal of Banking & Finance* 130, 106128. Below, "L&S" refers to
this paper and filter numbers follow its Table 1.

---

## 1. Changes that affect the filtered dataset

### 1.1 Filter (1), non-common stocks: name patterns were mostly inactive (bug)

**Problem.** The name patterns were regex-escaped twice:

```python
equity_identifer = [re.escape(p) for p in equity_identifer]          # 1st escape
pattern_regex = "|".join([re.escape(p) for p in equity_identifer])   # 2nd escape
```

After the second escape, every pattern containing a space, dot, parenthesis or `&` searches for a literal
backslash, so it can never match. **80 of the 97 U.S. patterns** were affected. Examples are `" TRUST "`,
`"REAL ESTATE INVESTMENT"`, `" ADR "`, `" UNIT "`, `"(SICAV)"`, `"EXPD."` and `" MORTGAGE"`. Only the 17
single-word patterns (e.g. `"WARRANT"`, `"PREFERRED"`, `"DEPOSITARY"`) were active.

**Fix.** The patterns are escaped once.

### 1.2 Filter (1), non-common stocks: TRAC screen and name screen are combined with AND

**What L&S do** (Section 3.1.1, filter 1): *"we exclude all stocks with security type code (datatype TRAC)
taking any value other than "ORD", "ORDSUBR", "FULLPAID", "UKNOWN", "UNKNOW" and "KNOW". However, the
majority of international stocks either do not have this datatype populated, or it is populated with a value
that means it is unknown (this happens frequently in delisted stocks, which can cause an unsuspecting user to
filter out delisted stocks instead of non-common stocks, biasing the filtered sample). [...] TDS' 'Equity'
classification is not reliable. We therefore refined and updated previous researchers' efforts to collect text
strings [...] hence our text strings have been designed to also exclude non-equity instruments, erroneously
classified by TDS. Specifically, we use stocks' extended names (datatype ENAME) and remove all stocks where
ENAME includes any of the country-specific strings listed in column 2 of our Table 2."*

Both screens exclude stocks, so a stock must pass both. The previous code kept a stock if it passed either
(`is_ord | ~ename_condition`). A stock with TRAC = ORD was then never checked against the names, and a stock
with a non-accepted TRAC (e.g. a REIT or ADR code) was kept whenever its name looked clean. I could not find
a justification for OR logic in the paper.

**Fix.** `filter_non_common_stocks(..., mode="landis")` (default):

```python
keep = (TRAC in accepted list  OR  TRAC not populated)  AND  (ENAME contains no pattern)
```

- **Unpopulated TRAC is kept.** This follows the paper's warning quoted above: dropping stocks without TRAC
  would disproportionately remove delisted stocks. Unpopulated covers NaN, `"nan"`, `"NA"`, `""` and similar,
  because `02_filter.py` casts statics to `str`, which turns NaN into `"nan"`.
- **"UKNOWN", "UNKNOW" and "KNOW" are kept as spelled.** They appear exactly like this in the paper.
- **The old logic is still available** as `mode="legacy_or"`, but with the escaping bug from 1.1 fixed.
- **The log line now shows where removals come from.** It reports how many stocks fail the TRAC screen, the
  name screen, or both.

**Expected effect.** Filter (1) will remove more stocks than on `main`. L&S report a filter-1 removal rate of
about 13% of U.S. instruments (Table 1, "US daily"). Comparing your removal rate with this number is a useful
sanity check. `scripts/diagnostics_review_changes.py` lists every stock whose status changes.

### 1.3 Filter (13), padded values before delisting: keep 9, remove the 10th and later

**What L&S do** (Section 3.2, filter 13): *"we eliminate data for stocks where we observe padded values for
return indexes immediately preceding their delisting date. While Ince and Porter (2006) [...] implement this
by removing the second and subsequent padded value in monthly data, we remove the tenth and subsequent padded
daily observation."*

**Previous code.** It truncated at the delisting date, which is consistent with the paper, and then removed
**all** trailing zero or missing returns. That is stricter than L&S.

**Fix.** `filter_padded_values_delistings(panel, statics, keep_padded=9)`:
- The default follows the paper: the first 9 padded days are kept, and the 10th and all later ones are
  removed.
- `keep_padded=0` reproduces the previous behaviour.
- The loop was replaced by a numpy expression with identical logic.
- New diagnostic: the filter prints how many stocks have `DEAD` in ENAME but no parsable `DELIST.dd/mm/yy`
  date. Those stocks are not truncated by filter 13, so their padded tail is only handled by the staleness
  filter (14).

### 1.4 Filter (19), nonsense values: added

L&S filter 19: *"We remove all stockdays for which unadjusted prices contain zero or negative values."* This
filter was missing. It has been added as `DSPreprocess.filter_nonsense_values` and is called in `02_filter.py`
after filter 18. Missing unadjusted prices are not removed. L&S report that this filter has a small effect
(0.02% of global stockdays).

### 1.5 Delisting return (own addition, not part of L&S): it was almost never applied (bug)

**Problem.** `adjust_for_delisting` set `Return = -0.35` only on the row dated exactly on `DelistingDate`.
There were two issues:

1. **The matching row rarely exists.** Datastream's delisting date lies after the last trading day (L&S
   make the same point), and filter 13 removes the padded days before it. A row dated on the delisting
   date therefore rarely survives, so the adjustment hit almost no stock.
2. **Monthly returns ignored it.** Only `Return` was changed, while `ReturnIndex` stayed as it was. Monthly
   returns are computed from `ReturnIndex` (`utils.determine_monthly_returns`, `analyses/00_...ipynb`), so
   they never contained the delisting return even when it was applied.

**Fix.** For every stock with a delisting date:
- **Rows after the delisting date are dropped.** This is unchanged.
- **The delisting return is compounded into the stock's last remaining observation:**
  `Return = (1 + Return) * (1 + d) - 1`.
- **`ReturnIndex` on that row is multiplied by `(1 + d)`,** so RI-based monthly returns contain the delisting
  return as well.
- **A new boolean column `DelistingReturnApplied` marks the adjusted rows.**
- **`delisting_return=None` switches the adjustment off.**

**Order change in `02_filter.py`.** The delisting adjustment now runs **before** the penny stock filter (21).
Before, if a delisted stock's last month(s) were removed as penny stock months, the "last remaining row" would
lie months before the actual delisting. The penny filter is a universe filter (L&S Section 3.3, applied on
investment dates), so it should not decide on which date the delisting return is booked.

**Caveat for papers.** The delisting return is not part of L&S. Datastream does not report delisting reasons,
so a uniform −35% treats mergers and acquisitions like performance-related delistings. Shumway (1997) derives
the ~−30% figure for performance-related delistings. It may be worth reporting results with
`delisting_return=None` as a robustness check.

### 1.6 `handle_missings` (own step): volume is no longer forward-filled, and filled rows are flagged

**Problem.** A missing `Volume` was replaced by the previous day's volume, which distorts every liquidity
measure. Forward-filled prices also entered the EDGE bid-ask estimator without any marker.

**Fix.**
- **Volume is left missing.** `Volume` was removed from the default `ffill_cols`.
- **Start dates are unchanged.** The rule for where a stock's history starts now uses a separate
  `require_cols`. It still includes `Volume`, so the first row per stock is identical to before.
- **Filled rows are flagged.** A new boolean column `IsFilled` marks rows where at least one price column was
  forward-filled. Downstream steps can exclude them, e.g. the spread estimation or event-study windows.
- **Not changed:** `Return` is still not recomputed on filled days (it stays NaN, while the filled
  `ReturnIndex` implies a zero return). The flag makes this visible. Whether filled days should exist in the
  base dataset at all is a design question for the next step.

---

## 2. Changes that do not affect the filtered dataset

| File | Change | Reason |
|---|---|---|
| `filter.py` | `filter_foreign_stocks` accepts both the short keys (`'Germany'`) and GEOGN values (`'GERMANY'`, `'UNITED STATES'`) | Before, it raised an error when called with the same `country` argument as all other filters. It is needed for the EU data. |
| `filter.py` | `@staticmethod` added to `filter_duplicate_loc_codes` | Consistency (it worked before, because it is only called on the class). |
| `raw_data_processing.py` | Logs the number of `#ERROR` columns and of non-numeric cells coerced to NaN per file | Stocks Datastream could not deliver were dropped silently before. |
| `01_import_transform_to_panel_monthly.ipynb` | Bare `except:` replaced. Failures are printed with the error, written to `Paneldata_failed_files.csv`, and the notebook raises at the end. Only `.xlsx` files are read, in sorted order. | On `main`, **`WC02999_23.xlsx` (total assets, ~1,000 firms) failed silently** (visible in the saved notebook output on `main`). Cell outputs were cleared because they were stale. |
| `02_monthly_firm_panel_data.ipynb` | Only `.csv` files are merged, in sorted order. Dates are parsed. The drop of `EPSISURDTE`/`EPSISURSUE` uses `errors="ignore"`. | The parquet output is written into the same folder, so a second run read it with `read_csv` and failed. `os.listdir` order is arbitrary. |
| `pyproject.toml`, `uv.lock` | `tdqm` → `tqdm` and `requests` added. The lock file was regenerated; the only package change is the removal of `tdqm`, the rest of the diff is uv's newer lock format. | `tdqm` is a typo package. `requests` is imported in `ff_data.py` but was not declared. |
| `.gitignore` | `__pycache__/`, `*.pyc`, `.ipynb_checkpoints/` ignored; committed `.pyc` files removed | Housekeeping |
| `tests/test_filters.py` | New: 12 tests on synthetic data for all changed filters | Run with `uv run pytest -q` (`pytest` was added to the dev dependencies). |
| `scripts/diagnostics_review_changes.py` | New: compares filter (1) on your real statics under `main`, `legacy_or` and `landis` and writes the stocks that change status to CSV | So the consequences of 1.1 and 1.2 can be inspected stock by stock. |

---

## 3. Checked against L&S and left unchanged

These parts of the implementation match the paper. In my first review I wrongly flagged the holiday filter;
it follows the paper.

| Filter | Paper | Code |
|---|---|---|
| (14) Staleness | "if 30 consecutive prices are identical, all subsequent price observations are eliminated until the next price change" | Same (drops the 31st and later identical RI values). |
| (16) Holidays | days on which non-missing or non-zero returns are "less than 0.5% of the total number of stocks available for that country across all days" | Same: the denominator is all stocks in the panel, not the stocks available on that day. |
| (15) Outlier errors | +100% followed by −50% (or the reverse): remove both days | Same |
| (18) Adjustment inconsistencies | "UP is more than 5% different to P ∗ AF" | **Please check:** the code compares P with UP × AF, i.e. it assumes P = UP × AF, while the paper writes UP = P × AF. Which one is right depends on Datastream's definition of AF. The two versions only coincide when AF = 1. If the direction were wrong, the filter would remove nearly all stockdays with AF ≠ 1. L&S report 0.4% removed globally, so your log line for filter (18) shows which convention your data follows. |
| (12) Few observations | 120 valid daily observations, unless the first observation is within the last 120 days of the sample | Same. The exception window is measured in calendar days, and "observations" counts rows. The paper does not specify either. |
| (7)–(10) | as in paper | Same |

---

## 4. Deviations from L&S that remain (for the data description in papers)

These are intentional or design decisions. None were changed, but they are worth stating when citing L&S.

1. **Sequential instead of independent application.** L&S: *"We apply our filters after removing all stocks
   for which there is no return index data (our filter 11) [...]. Having done this, we apply each of the
   remaining filters independently of all others."* `02_filter.py` applies them one after another, each on
   the output of the previous one. This matters for filters whose statistics depend on the remaining sample:
   7, 8, 9, 10, 12 and 16 (e.g. volatility is computed after filter 13 has removed padded days).
2. **Penny stock threshold:** 15% (`penny_percentile = 0.15`) instead of the lowest quartile (25%) used by
   L&S. L&S also define filter 21 as a filter on each portfolio investment date (Section 3.3), whereas here it
   removes all stockdays of the month from the base panel.
3. **Own filters not in L&S:** implausible OHLC, no-trading-activity (Chaieb et al., 2021), `handle_missings`
   (dropping leading missing rows and forward filling), the delisting return, and the EDGE bid-ask spread.
4. **Filter 17 (survivorship start date):** not called. The sample starts on 1993-10-01, after the U.S. start
   date of Dec 1984 in L&S, so this is consistent.
5. **Filters 6 and 20** are not applied. Filter 6 is irrelevant for a single country. Filter 20 only matters
   when MTBV is used for value/size portfolios.
6. **For the EU data later:** L&S do not apply the cross-listing filter (2) to German XETRA stocks that are
   also listed in Frankfurt, and they merge Frankfurt (before 1999-12-31) and XETRA series (Internet
   Appendix B.1). This is not implemented yet.

---

## 5. Open questions, not checked from the code

The U.S. name pattern list in `filter.py` was not compared string by string with Table 2 of L&S.


L&S Section 2.2 stress that return indexes must be extracted **at maximum precision and in the local
(traded) currency**. At the default two decimals, many returns come out as zero, which inflates filters 8, 10
and 14. The Excel add-in requests are not part of the repository, so I could not check this. It is worth
confirming for both the US and the EU downloads.

---

## How to check the consequences

1. `uv sync` then `uv run pytest -q` (all tests should pass).
2. `uv run python scripts/diagnostics_review_changes.py`: counts and CSV of stocks whose filter-1 status
   changes.
3. Run `scripts/02_filter.py` on `main` and on this branch, write the outputs to different file names, and
   compare the log lines per filter, the number of stocks and stockdays, and the FF market correlation from
   `analyses/00_data_universe_check.ipynb`.
4. To reproduce individual parts of the old behaviour on this branch:
   - `filter_non_common_stocks(..., mode="legacy_or")` (OR logic, escaping still fixed)
   - `filter_padded_values_delistings(..., keep_padded=0)`
   - `adjust_for_delisting(..., delisting_return=None)` to switch the delisting return off
   - `handle_missings(..., ffill_cols=[..., 'Volume'])` to forward-fill volume again
