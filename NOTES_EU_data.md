# European price data: notes for processing and documentation

## Download (October 2026)

* 36 LSEG lists (`L#EU001` ... `L#EU036`), 35,616 lines from the domestic-exchange filter
  (`scripts/10_select_domestic_listings.py`, decisions in `config/eu_domestic_exchanges.csv`).
* Daily data from 1992-12-31, `DPL#(...,6)`. Variables per folder: AF, MTBV, MV, MV_EU, P, PH, PL, PO, RI,
  RI_EU, STATIC, UP, UP_EU, VO. `_EU` = Datastream `~E` (EUR at daily market rates).
* Import: `uv run python scripts/21_load_price_panels.py --region EU`. Local series keep the US column names
  (`ReturnIndex`, `MarketCAP`, `UnadjClose`, ...), EUR series get the suffix `_EUR`.

## Currency of the local series

Datastream restates the history of a line into the currency of its market at the time of a currency
change only for lines that were **alive** at that time. Example, list 01 (Austria and Belgium):

| Currency code in the file header | Lines | RI local / RI EUR |
|---|---|---|
| E (euro) | 609 | exactly 1 |
| AS (Austrian schilling) | 61 | median 13.7603 (fixed rate), varies before 1999 |
| BF (Belgian franc) | 122 | median 40.3399 (fixed rate), varies before 1999 |
| U$ | 1 | varies with EUR/USD |

* Lines that died before euro adoption stay in the legacy currency (statics `PCUR`: e.g. FF 793,
  DM 364, Croatian kuna KA 423, Bulgarian lev BL 424, Slovak koruna KK 356).
* Before 1999, `~E` converts legacy currencies with the synthetic euro (ECU-based) rate, so their EUR series
  move with that rate. From 1999 on, the fixed conversion rates apply.

Consequences:

1. Stale-price, zero-return and padding filters run on the local series: legacy-currency lines are
   constant multiples within their own currency, so these filters are not affected.
2. Returns, market caps and the penny threshold use the `_EUR` series.
3. **Later euro adopters (GR 2001, SI 2007, CY/MT 2008, SK 2009, EE 2011, LV 2014, LT 2015, HR 2023,
   BG 2026).** Surviving lines were restated at the fixed conversion rate over their whole history, so
   their EUR returns before adoption contain no exchange-rate movements, while lines that died before
   adoption are converted at market rates. Pegged currencies (EE, LV, LT, BG) are hardly affected; the
   koruna (SK), tolar (SI) and kuna (HR) moved against the euro before adoption. Options: document it, start
   these countries in EUR-based analyses at euro adoption, or rebuild pre-adoption EUR returns of
   survivors from the national-currency series and market exchange rates.

## Lines without data

In list 01, 207 of 1,000 lines return `#ERROR` for RI (`E100 INVALID CODE OR EXPRESSION ENTERED`,
`2381 NO DATA AVAILABLE`); they are mostly lines without a return index (e.g. unlisted or certificate lines)
and are dropped at import with a warning. Their number per folder appears in the import log.

## Filtering (`scripts/22_filter_prices_eu.py`)

`uv run python scripts/22_filter_prices_eu.py` (options: `--countries`, `--penny`, `--min-stocks`, `--start`, `--end`).
Same filters and order as `scripts/22_filter_prices_us.py`, with these European adjustments:

* Filters (1)-(5) per country on the statics (country lists for name patterns, cross-listing tags and
  currencies); filter (4) keeps lines whose GEOGN is a sample country; filter (6) drops countries with fewer
  than `--min-stocks` (20) stocks after filter (11).
* Filter (5) currency lists: Bulgaria also accepts `E` (euro since 2026, surviving lines restated);
  Ireland also accepts `£` (London quotes count as domestic for Ireland). All remaining removals are
  foreign-currency quotes (USD, JPY, ...).
* Return-based filters use local-currency returns (`Return` from `ReturnIndex`); `Return_EUR` from
  `ReturnIndex_EUR` gets the same row removals and the same delisting return (-0.35).
* Filters (16) holidays and (21) penny stocks per country; the penny threshold uses `UnadjClose_EUR`.
* `handle_missings` only requires `ReturnIndex`, `ReturnIndex_EUR` and `UnadjClose` (not Open, High, Low,
  Volume), so stocks are not cut where Datastream's OHLC coverage starts later. Spread estimates are missing
  where OHLC is missing.
* `filter_report_<p>.csv`: remaining stocks per country after every step (for the data section of papers).
* Memory: processed country by country (identical results). The panel files are split once into
  `processed/_split_by_country/`. The split is reused only while the panel files (size, modification time) and
  the lines passing the static filters (1)-(5) are unchanged (fingerprint in `_complete.json`); otherwise it is
  rebuilt automatically. `--resplit` forces a rebuild.
  Output: one file per country in `EU_data_panel_filtered_<p>/`, loaded with
  `datastream.utils.load_filtered_eu(out_dir, countries=..., columns=...)`.

For analyses in EUR use `Return_EUR`, `ReturnIndex_EUR`, `MarketCAP_EUR`; the local columns keep the US
names.

## Monthly universe and baseline panel (`40`, `41` with `--region EU`)

* `uv run python scripts/40_build_monthly_universe.py --region EU` reads the per-country files and writes
  `monthly_universe_<p>.parquet` with `Country`, local `MarketCAP`/`ReturnIndex`/`Close` and
  `MarketCAP_EUR`/`ReturnIndex_EUR`. `size_group`: quintiles of `MarketCAP_EUR` across all European stocks
  of the month; `size_group_country`: quintiles within the country (also on EUR market caps).
* `uv run python scripts/41_build_baseline_panel.py --region EU`: Worldscope items are in thousands of the
  local currency, so `bm` and `ep` use the local market cap (millions); `ret` is the local-currency monthly
  return, `ret_eur` the EUR return; output columns `country`, `market_cap_eur`, `return_index_eur`,
  `size_group_country` in addition to the U.S. columns.

## Currency of Worldscope data and bm/ep (checked October 2026, `scripts/42_check_bm_consistency.py`)

Check: Datastream's MTBV (market value / book value, one currency, per share) times our bm should be 1.
14,126 lines with data: 12,484 (88%) have a median ratio within [0.95, 1.05], most of them exactly 1.

* BP / Shell (USD reporters quoted in GBP): ratio 1, Worldscope values are in GBP like MV. No issue.
* Lines quoted in a legacy currency of a euro country (DM, FF, ITL, ATS, BEF, NLG, PTE, ESP, IEP, FIM,
  HRK, BGN, LTL, ...): Worldscope values are in EUR, MV in the legacy currency; the ratio equals 1/(conversion
  rate) (fixed rate after 1999, synthetic/market rate before). Fix: `41_build_baseline_panel.py --region EU`
  computes bm/ep with `MarketCAP_EUR` for all countries that use the euro today (`current_currency` = EUR in
  `config/eu_countries.csv`), and with local `MarketCAP` elsewhere.
* Share classes (A/B shares in SE, DK, NO, CH; Italian savings shares RNC/RSP; French CI/ADP; Shell A/B
  before 2022): MV is the value of one class, WC03501 the equity of the whole firm, so bm and ep are too
  high by (all shares / shares of the class). Open: per-share bm (book value per share / price) or firm-level
  market value; affects about 1,000-1,200 lines (also U.S. multi-class firms).
* Ratios between 0.7 and 1.4 without class or currency pattern: mostly timing (Datastream updates book value
  on its own schedule, the baseline uses the availability lag).
