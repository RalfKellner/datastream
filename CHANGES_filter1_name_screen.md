# Filter (1) name screen: status suffix and ORD override (branch `filters/name-screen-status-suffix`)

Reference: Landis & Skouras (2021), Section 3.1.1, filter (1). Both changes are deviations from the plain
L&S screen and are switched on by default. `filter_non_common_stocks(..., strip_status_suffix=False,
ord_override=False)` reproduces the previous behaviour exactly.

## Why the check was done

On the U.S. statics, filter (1) removes 48.8% of all lines, compared with 13.4% reported by L&S. A breakdown
on the real U.S. statics (38,888 lines, October 2026) showed:

| | Lines | Removed |
|---|---|---|
| All lines | 38,888 | 48.8% |
| TYPE = EQ only | 31,043 | 37.0% |
| TYPE ≠ EQ (ETF, CEF, ETN, ADR, GDR) | 7,845 | 96.8% |

About 7,500 of the 19,000 removed lines are ETFs, closed-end funds and ETNs, which the U.S. "Equities"
category in Datastream contains. Removing them is correct. Among TYPE = EQ, the TRAC screen alone removes only
650 lines, all of them non-common (UNT, DEPOSITSHS, PREFERRED, CEF, notes). The name screen removes 7,445
lines, about 80% of them correctly (preferreds, warrants, units, rights, trust-preferred securities). Two
issues remained.

## Change 1: name patterns are applied to the company name without Datastream's status text

Datastream appends a status text to the names of dead, expired or suspended lines, e.g.
`ANDOVER TOGS DEAD - LASD 01/05/96`, `FIRST VIRGINIA BANKS DEAD - ACQUISITION BY 992305`,
`COMPUTER POWER UNIT 1/7/91 EXPIRED 01/07/91`. Codes in that text matched name patterns (`- LASD`, `EXPD.`,
`EXCH.`, `ACQUISITION`, ` TRUST ` via a trailing blank), so ordinary dead stocks were removed. This
biases the sample towards survivors, which is the effect L&S warn about for the TRAC screen.

* The status text is cut off with `STATUS_SUFFIX_REGEX` (`... DEAD ...`, `... EXPIRED ...`, `... SUSP - ...`,
  `(SUSP - ...)`). On the U.S. and EU statics no `DEAD`/`EXPIRED`/`SUSP` remains in the stripped names.
* The stripped name is padded with a blank on both sides, so patterns such as `" UNIT "` or `"FUND "` also
  match at the end of a name. This removes some funds and trusts that the previous screen missed.
* Lines whose status text marks them as duplicates (`DEAD - DUPLICATE SEE ...`, `DEAD - DUPL SEE ...`) are
  still removed. The abbreviation `DUPL` was not caught by the L&S pattern `DUPLICATE` before.

## Change 2: confirmed TRAC = "ORD" overrides a few generic name patterns (U.S. only)

Some patterns also occur in names of ordinary operating companies: `CONSOLIDATED` (Consolidated Edison,
Coca-Cola Consolidated), `" TRUST "` (Washington Trust Bancorp), `" SERIES "` (Warner Bros Discovery
Series A, Liberty Media lines), `ASSET MANAGEMENT`, `INVESTMENT MANAGEMENT`. For lines with TRAC = "ORD" these
patterns (`ORD_OVERRIDABLE_PATTERNS`) no longer lead to removal. All other patterns still apply, in particular
the SPAC (`ACQUISITION`, `CAPITAL INVESTMENT`, `EQUITY PARTNERS`) and REIT patterns (`REALTY `,
`REAL ESTATE`, ` MORTGAGE`), so those stay excluded as L&S intend. `PREFERRED` is deliberately not
overridable. The override is defined for the U.S. only; for other countries (e.g. U.K. investment trusts)
it would have to be checked on the data first.

## Effect on the U.S. statics

| | Before | After |
|---|---|---|
| Removed, all lines | 48.83% | 48.46% |
| Removed, TYPE = EQ | 37.0% | 36.4% |

302 lines are restored (70% of them dead, 234 with missing TRAC, 68 with TRAC = ORD) and 161 lines are newly
removed (funds and trusts caught by the padded name). Conclusion: the high removal share of filter (1) for
the U.S. is mainly due to ETFs and funds in the U.S. universe, not to a filter error.

## How to check the consequences

1. Run `scripts/02_filter.py` on this branch and on `main` and compare the number of stocks after filter (1)
   and at the end.
2. Compare the value-weighted market portfolio with the Fama-French market factor
   (`analyses/01_market_sanity_checks.ipynb`), in particular the drift until 2008, which involves dead stocks.

Tests: `tests/test_filters.py::test_non_common_status_suffix_is_not_screened`,
`test_non_common_ord_override_only_for_generic_patterns`.

---

## Change 3: filter (3) also removes secondary lines sharing an ISIN

L&S filter (3) keeps only the primary line (ISINID = "P") among lines with the same local code (LOC). Some
secondary quote lines have a different or missing LOC and survive, e.g. Irish stocks quoted in Dublin and
London (Glanbia, Kerry), Swiss second trading lines (ABB), German "(XET)" lines, Croatian OTC lines.
`filter_duplicate_loc_codes(..., also_by_isin=True)` applies the same rule a second time to lines sharing an
ISIN. Lines without ISIN are never removed by this step. `also_by_isin=False` gives the plain L&S filter.

Additional lines removed on the statics (October 2026): U.S. 368 (almost all fund NAV lines, which filter (1)
removes anyway); EU: Ireland 116, United Kingdom 71, Germany 74, Sweden 45, Croatia 35, Switzerland 24,
Romania 21, Spain 13, Belgium 11, others < 5.

## Change 4: French name pattern `'NR '` -> `' NR '`

`'NR '` also matched company names ending in "...NR" ("SEBDO ENR", "MNR GROUP", "CERVIN ENR", about 32 lines).
With a leading blank it only matches the "NR" (non-registered / nouvelles) marker as a separate word.

## Change 5: penny-stock threshold 0.25 in `scripts/02_filter.py`

`penny_percentile` is set to 0.25, the lowest quartile used by L&S for filter (21) (previously 0.20). The
output files carry the percentile in their names (`US_data_panel_filtered_0.25.feather`), so earlier 0.20
outputs are not overwritten.
