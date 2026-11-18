# US sustainable-finance dataset (Datastream / Worldscope / Refinitiv ESG)

Status: November 2026. Repository: github.com/RalfKellner/datastream (scripts 22 → 40 → 41 → 43 → 44).
Output: `<firm root>/Paneldata/sustainability/sustainability_lag12_0.25.parquet` (+ `.json`, `_summary_by_year.csv`).

## 1. Sources

| Data | Source | Frequency of request |
|---|---|---|
| Prices, returns, market cap | LSEG Datastream (RI, P, UP, MV), daily | daily → month end |
| Fundamentals | Worldscope via Datastream (WC items, thousands of USD) | monthly |
| Fiscal period end | Worldscope `WC05350`, requested as a **monthly time series** | monthly |
| Emissions | Refinitiv ESG via Datastream: ENERDP023 (total CO2e), ENERDP024 (scope 1), ENERDP025 (scope 2), ENERDP123 (total, estimated / gap-filled) | monthly |
| Industry | TRBC economic sector, business sector, industry group (TR1N–TR3N, codes TR1–TR3), static (current classification) | static |

## 2. Universe

US common equity from Datastream lists, cleaned with the filters of Landis & Skouras (2021, *Journal of Banking & Finance*),
with documented deviations (repository: `CHANGES_review.md`, `CHANGES_filter1_name_screen.md`); penny-stock threshold:
25th percentile. The value-weighted market return of the filtered universe correlates 0.999 with the Fama-French market
factor. Monthly universe 1993-10 to 2025-12: 12,339 stocks; from 2002: 9,114 stocks.

## 3. Timing (point in time)

**Datastream dating convention.** Requested at monthly frequency, Datastream repeats an annual Worldscope or Refinitiv ESG
value in every month of the fiscal year it describes, i.e. from the **first month of that fiscal year** on – long before
the value is published. Evidence: American Airlines shows fiscal-2020 sales (USD 17.3bn, COVID) and emissions (20.1 Mt,
half of 2019) from January 2020; median sales growth at the January updates of December-FYE firms is +0.9% in 2020 and
+18.3% in 2021; the monthly `WC05350` series shows the fiscal year end of the value in each month (2020-01 … 2020-12:
2020-12-31). Reading the change month as the publication date would introduce about 12 months of look-ahead bias.

**Fiscal year end** of each annual value: monthly `WC05350` in the month the value first appears (99.97% of the
Worldscope snapshots; plausibility window report month to report month + 23); fallback: report month + 11.
`WC05350` equals the +11 rule in 91% of the cases; deviations are mostly 52/53-week fiscal years (+1/+2 months) and
fiscal-year changes / short transition years.

**Availability.**
- Worldscope fundamentals: from fiscal year end + 4 months (10-K deadlines 60–90 days), for at most 18 months after
  fiscal year end.
- Emissions: from fiscal year end + **12 months**, for at most 24 months after fiscal year end. Zhang ("Carbon Returns
  Across the Globe", *Journal of Finance*) reports a median lag of about 10 months between fiscal year end and the
  availability of emission data.

**Validation of the timing** (rolling panel, US): Fama-French factors rebuilt from the panel correlate with Ken French's
factors at 0.97 (SMB, HML), 0.93 (RMW), 0.91 (CMA). Event study around fiscal year ends (earnings-growth quintiles):
cumulative Q5–Q1 spread 43.1% during the fiscal year, 1.5% in months 1–3 after fiscal year end, 0.6% in months 4–6 and
−0.2% in months 7–12 – the information is priced before the availability month.

## 4. Variables

Emissions in tonnes CO2e; intensities in tonnes per USD million of sales.

| Variable | Definition |
|---|---|
| `co2e_scope1`, `co2e_scope2` | ENERDP024, ENERDP025 |
| `co2e_total_reported` | ENERDP023, else scope 1 + scope 2 |
| `co2e_total_estimated` | ENERDP123 (equals the reported total where one exists, model estimate otherwise) |
| `co2e_total_combined` | reported total, else estimate |
| `co2e_is_estimated` | 1 if `co2e_total_combined` comes from the estimate only |
| `sales_fy` | Worldscope net sales (WC01001) of the same fiscal year (matched via `WC05350`) |
| `co2e_intensity`, `co2e_scope1_intensity`, `co2e_scope12_intensity` | emissions / (sales_fy / 1000) |
| `co2e_intensity_ind_adj` | log intensity minus the monthly median log intensity of the TRBC industry group (≥ 5 firms) |
| `co2e_growth` | log change of `co2e_total_combined` vs. the previous fiscal year (same source only) |
| `log_co2e_total_combined`, `log_co2e_scope1`, `log_co2e_scope2` | logs (zeros → missing) |
| `is_financial` | TRBC economic sector = Financials |
| `log_me`, `log_bm`, `mom_12_2`, `str_1`, `op`, `ag`, `ep` | size, book-to-market (positive book equity), momentum t−11..t−1, short-term reversal, operating profitability, asset growth, earnings yield |
| `leverage`, `invest_ta`, `roe`, `sales_growth`, `vol_12m` | total debt / TA, capex / TA, NI / common equity, sales growth, std of monthly returns t−11..t |
| `ret`, `ret_next` | return of month t and t+1 |
| `fund_*`, `em_*` | report month, fiscal year end, availability month, age of the fundamentals / emissions |

**Cleaning.** Emissions: negative values dropped; annual values more than 500 times off both neighbouring years treated as
unit errors (21 cases, e.g. Walmart ENERDP123 fiscal 2024 = 18.7). Worldscope: sign rules, unit-error screens, maximum age.

## 5. Coverage (December cross-sections, point in time)

| Year | stocks with emissions | of which reported | EW share reported | VW share reported | VW share reported or estimated |
|---|---|---|---|---|---|
| 2005 | 604 | 79 | 2.4% | 23.7% | 73.6% |
| 2010 | 937 | 336 | 11.6% | 61.7% | 88.5% |
| 2015 | 957 | 383 | 13.2% | 61.6% | 88.5% |
| 2020 | 2,612 | 930 | 30.9% | 79.5% | 98.7% |
| 2025 | 2,768 | 1,339 | 42.2% | 92.4% | 99.1% |

From 2002: 4,490 stocks with emissions (1,919 with reported values at some point, 2,571 with estimates only);
392,341 stock-months, 64% of them from estimates. Fundamentals available for 99.3% of the rows; TRBC for 99.1%
(missing only for stocks delisted 2005–2008); financials 17.2% of the rows.

## 6. Recommendations and caveats

- Reported emissions: meaningful from about 2010 (VW coverage > 60%), broad cross-sections from 2018/19; equal-weighted
  coverage remains a large-cap sample.
- Estimated emissions are model-based and driven by size and industry (Aswani, Raghunandan & Rajgopal, *Review of
  Finance*, 2024). Use `co2e_is_estimated` to separate them or as a robustness check; do not pool silently.
- The coverage jump in 2016 (estimates from fiscal 2015 on) changes the sample composition; the median intensity of
  non-financials falls from ~55–60 (2003–06) to ~20 t/USD m (2021–25), partly through composition. Use constant samples
  or industry-adjusted intensity for time-series statements.
- Intensities of financials are not comparable (WC01001 contains interest / premium income); exclude or treat separately.
- The emission history is the current Refinitiv vintage (backfilled and revised); it is not a point-in-time database.
- TRBC is the current classification (not historical).

## 7. Open points

- Europe: monthly `WC05350`, rebuild baseline / emissions / dataset; convert sales to EUR before comparing intensities
  across countries.
- Scope 3 emissions; definition of ENERDP025 (market- vs. location-based scope 2).
- Quarterly fundamentals for the baseline panel.

## References (as used above)

- Landis, C. & Skouras, S. (2021). *Journal of Banking & Finance* – Datastream data filters.
- Zhang, S. "Carbon Returns Across the Globe." *Journal of Finance*.
- Aswani, J., Raghunandan, A. & Rajgopal, S. (2024). Are carbon emissions associated with stock returns? *Review of Finance*.
- Bolton, P. & Kacperczyk, M. (2021). Do investors care about carbon risk? *Journal of Financial Economics*.
