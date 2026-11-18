# Emissions module (November 2026)

Branch `sustainability/emissions-panel` (contains `sustainability/emissions-availability` and
`firm-data/fiscal-period-end`).

## Availability check (`32_check_emissions.py`, US, November 2026)

* Reported emissions (ENERDP023): VW coverage of the price universe > 65% from 2009, 91-93% in 2022/23; EW
  13-44% (large firms). Estimates (ENERDP123): 64-89% EW, 96-99% VW from 2015 (Refinitiv coverage expansion).
* From 2002: 9,114 stocks with prices; 2,066 with reported emissions, 4,745 reported or estimated, 4,512 of them
  estimates only.
* ENERDP123 equals the reported total where one exists (> 97% until 2021) and holds model estimates otherwise;
  it lags the reported series by about a year and diverges more in 2022-24. Total = scope 1 + scope 2 in > 99%
  since 2012.
* Timing: 98% of the emission updates fall in the Worldscope report month, and emission growth correlates best
  with sales growth of the same update; with the COVID test this showed that Datastream shows annual values
  from the first month of their fiscal year (`CHANGES_ws_dating.md`).

## Panel (`43_build_emissions_panel.py`, `src/datastream/emissions_panel.py`)

* Fiscal year end per snapshot from monthly WC05350 (fallback report month + 11).
* Used from fiscal year end + 12 months (`--lag`; Zhang, "Carbon Returns Across the Globe": median lag between
  fiscal year end and emission data about 10 months), for at most 24 months after fiscal year end.
* `co2e_total` = reported total (ENERDP023, else scope 1 + 2), else estimate; `co2e_is_estimated` flag.
  Intensities per million of sales of the same fiscal year; growth only between years of the same source.
* Cleaning: negative values; annual values more than 500 times off both neighbours (unit errors, e.g. Walmart
  ENERDP123 fiscal 2024 = 18.7).
* Output keeps prices, market cap and returns of the universe (price-emissions merge); join the baseline panel
  on (DSCD, Date) for fundamentals.

Open: industry classification for intensity comparisons, scope 3, EU (sales in local currency -> convert before
comparing intensities across countries).
