import io
import json
import re
import requests
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd


# ── configuration ──────────────────────────────────────────────────────────

# All valid (num_factors, frequency) combinations and their remote descriptors
_FF_CONFIGS = {
    (3, "daily"): {
        "url": "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_Factors_daily_TXT.zip",
        "filename": "F-F_Research_Data_Factors_daily.txt",
        "columns": ["date", "Mkt-RF", "SMB", "HML", "RF"],
        "date_fmt": "daily",
        "stop_at_annual": False,
    },
    (3, "weekly"): {
        "url": "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_Factors_weekly_TXT.zip",
        "filename": "F-F_Research_Data_Factors_weekly.txt",
        "columns": ["date", "Mkt-RF", "SMB", "HML", "RF"],
        "date_fmt": "daily",   # same YYYYMMDD format as daily
        "stop_at_annual": False,
    },
    (3, "monthly"): {
        "url": "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_Factors_TXT.zip",
        "filename": "F-F_Research_Data_Factors.txt",
        "columns": ["date", "Mkt-RF", "SMB", "HML", "RF"],
        "date_fmt": "monthly",
        "stop_at_annual": True,
    },
    (5, "daily"): {
        "url": "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_5_Factors_2x3_daily_TXT.zip",
        "filename": "F-F_Research_Data_5_Factors_2x3_daily.txt",
        "columns": ["date", "Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF"],
        "date_fmt": "daily",
        "stop_at_annual": False,
    },
    (5, "monthly"): {
        "url": "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_5_Factors_2x3_TXT.zip",
        "filename": "F-F_Research_Data_5_Factors_2x3.txt",
        "columns": ["date", "Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF"],
        "date_fmt": "monthly",
        "stop_at_annual": True,
    },
}

_CACHE_STALENESS_DAYS = 90
_METADATA_FILE = "ff_cache_metadata.json"


# ── internal helpers ───────────────────────────────────────────────────────

def _parse_ff_txt(raw_bytes, columns, date_fmt, stop_at_annual):
    """Parse raw bytes from a French data txt file into a DataFrame."""
    data_lines = []
    for line in raw_bytes:
        s = line.decode("UTF-8")
        if stop_at_annual and re.search("Annual", s):
            break
        if s and s[0].isdigit():
            data_lines.append(s.split())

    df = pd.DataFrame(data_lines, columns=columns)

    if date_fmt == "daily":
        df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    else:
        df["date"] = pd.to_datetime(df["date"], format="%Y%m").dt.to_period("M")

    df = df.set_index("date").astype(float)
    return df


def _download_one(num_factors, frequency):
    """Download and parse a single FF combination. Returns raw DataFrame in %."""
    cfg = _FF_CONFIGS[(num_factors, frequency)]
    r = requests.get(cfg["url"], timeout=30)
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    with z.open(cfg["filename"]) as f:
        raw = f.readlines()
    return _parse_ff_txt(raw, cfg["columns"], cfg["date_fmt"], cfg["stop_at_annual"])


def _cache_key(num_factors, frequency):
    return f"ff{num_factors}_{frequency}"


def _cache_path(cache_dir, num_factors, frequency):
    return Path(cache_dir) / f"{_cache_key(num_factors, frequency)}.parquet"


def _metadata_path(cache_dir):
    return Path(cache_dir) / _METADATA_FILE


def _load_metadata(cache_dir):
    p = _metadata_path(cache_dir)
    if p.exists():
        with open(p) as f:
            return json.load(f)
    return {}


def _save_metadata(cache_dir, metadata):
    with open(_metadata_path(cache_dir), "w") as f:
        json.dump(metadata, f, indent=2)


def _is_stale(metadata, key):
    """Return True if the cached entry is missing or older than the threshold."""
    if key not in metadata:
        return True
    downloaded_at = datetime.fromisoformat(metadata[key]["downloaded_at"])
    return datetime.now() - downloaded_at > timedelta(days=_CACHE_STALENESS_DAYS)


def _write_to_cache(df, cache_dir, num_factors, frequency, metadata):
    """Persist a DataFrame to parquet and update the metadata timestamp."""
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    df.to_parquet(_cache_path(cache_dir, num_factors, frequency))
    key = _cache_key(num_factors, frequency)
    metadata[key] = {"downloaded_at": datetime.now().isoformat()}
    _save_metadata(cache_dir, metadata)


def _read_from_cache(cache_dir, num_factors, frequency):
    return pd.read_parquet(_cache_path(cache_dir, num_factors, frequency))


# ── public API ─────────────────────────────────────────────────────────────

def prefetch_ff_cache(
    cache_dir="~/.ff_cache",
    force_refresh=False,
    silent=False,
):
    """
    Download and cache all five FF combinations (FF3 daily/weekly/monthly,
    FF5 daily/monthly). On subsequent calls, stale entries (older than
    _CACHE_STALENESS_DAYS days) prompt the user for confirmation before
    refreshing.

    Parameters
    ----------
    cache_dir     : str | Path  — where parquet files and metadata are stored
    force_refresh : bool        — skip staleness check and re-download everything
    silent        : bool        — skip the interactive prompt and always refresh
                                  stale data (useful in non-interactive scripts)
    """
    cache_dir = Path(cache_dir).expanduser()
    metadata = _load_metadata(cache_dir)

    stale = [
        (nf, freq)
        for (nf, freq) in _FF_CONFIGS
        if force_refresh or _is_stale(metadata, _cache_key(nf, freq))
    ]

    if not stale:
        print("FF factor cache is up to date.")
        return

    # Ask the user once if any entry is stale
    if not force_refresh and not silent:
        oldest = min(
            datetime.fromisoformat(metadata[_cache_key(nf, fr)]["downloaded_at"])
            for nf, fr in stale
            if _cache_key(nf, fr) in metadata
        ) if any(_cache_key(nf, fr) in metadata for nf, fr in stale) else None

        if oldest:
            age_days = (datetime.now() - oldest).days
            msg = (
                f"\nSome cached FF data is stale (oldest entry: {age_days} days ago).\n"
                f"Affected: {[f'FF{nf} {fr}' for nf, fr in stale]}\n"
                "Refresh now? [y/n]: "
            )
        else:
            msg = (
                f"\nNo FF cache found for: {[f'FF{nf} {fr}' for nf, fr in stale]}\n"
                "Download now? [y/n]: "
            )

        if input(msg).strip().lower() != "y":
            print("Skipped. Using existing cache where available.")
            return

    for num_factors, frequency in stale:
        label = f"FF{num_factors} {frequency}"
        try:
            print(f"  Downloading {label}...", end=" ", flush=True)
            df = _download_one(num_factors, frequency)
            _write_to_cache(df, cache_dir, num_factors, frequency, metadata)
            print(f"done ({len(df):,} rows).")
        except Exception as e:
            print(f"FAILED — {e}")


def get_ff_factors(
    num_factors=3,
    frequency="monthly",
    in_percentages=False,
    cache_dir="~/.ff_cache",
    force_refresh=False,
):
    """
    Return a Fama-French factor DataFrame, using a local parquet cache.

    If the requested combination is not yet cached (or is stale), it is
    downloaded automatically. Use prefetch_ff_cache() to bulk-refresh all
    combinations at once.

    Parameters
    ----------
    num_factors    : int  — 3 or 5
    frequency      : str  — 'daily', 'weekly' (FF3 only), or 'monthly'
    in_percentages : bool — True → values in %; False (default) → decimals
    cache_dir      : str | Path
    force_refresh  : bool — ignore cache and re-download
    """
    if (num_factors, frequency) not in _FF_CONFIGS:
        valid = [(nf, fr) for nf, fr in _FF_CONFIGS]
        raise ValueError(
            f"Invalid combination (num_factors={num_factors}, frequency='{frequency}'). "
            f"Valid options: {valid}"
        )

    cache_dir = Path(cache_dir).expanduser()
    metadata = _load_metadata(cache_dir)
    key = _cache_key(num_factors, frequency)

    if force_refresh or _is_stale(metadata, key):
        label = f"FF{num_factors} {frequency}"
        print(f"Cache miss or stale for {label} — downloading...", end=" ", flush=True)
        df = _download_one(num_factors, frequency)
        _write_to_cache(df, cache_dir, num_factors, frequency, metadata)
        print(f"done ({len(df):,} rows).")
    else:
        df = _read_from_cache(cache_dir, num_factors, frequency)

    return df if in_percentages else df / 100

# ── international (Europe) factors ─────────────────────────────────────────
# Kenneth French's European factors: Austria, Belgium, Denmark, Finland, France, Germany, Greece, Ireland,
# Italy, the Netherlands, Norway, Portugal, Spain, Sweden, Switzerland and the United Kingdom.
# The international factors are, as far as documented in the data library, in U.S. dollars; RF is the U.S.
# one-month T-bill rate. analyses/04_eu_market_sanity_checks.ipynb checks the currency empirically.
_FF_REGION_CONFIGS = {
    ("Europe", "monthly"): {
        "url": "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/Europe_3_Factors_TXT.zip",
        "columns": ["date", "Mkt-RF", "SMB", "HML", "RF"], "date_fmt": "monthly", "stop_at_annual": True,
    },
    ("Europe", "daily"): {
        "url": "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/Europe_3_Factors_Daily_TXT.zip",
        "columns": ["date", "Mkt-RF", "SMB", "HML", "RF"], "date_fmt": "daily", "stop_at_annual": False,
    },
}


def _download_region(region, frequency):
    cfg = _FF_REGION_CONFIGS[(region, frequency)]
    r = requests.get(cfg["url"], timeout=30)
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    name = next(n for n in z.namelist() if n.lower().endswith((".txt", ".csv")))   # one data file per zip
    with z.open(name) as f:
        raw = f.readlines()
    return _parse_ff_txt(raw, cfg["columns"], cfg["date_fmt"], cfg["stop_at_annual"])


def get_ff_region_factors(region="Europe", frequency="monthly", in_percentages=False, cache_dir="~/.ff_cache",
                          force_refresh=False):
    """Fama-French 3 factors of an international region (currently 'Europe'), cached like get_ff_factors.

    Returns Mkt-RF, SMB, HML, RF (decimals unless in_percentages). Monthly index: Period 'M'.
    """
    if (region, frequency) not in _FF_REGION_CONFIGS:
        raise ValueError(f"Not available: {(region, frequency)}. Options: {list(_FF_REGION_CONFIGS)}")
    cache_dir = Path(cache_dir).expanduser()
    metadata = _load_metadata(cache_dir)
    key = f"ff3_{region.lower()}_{frequency}"
    path = cache_dir / f"{key}.parquet"
    if force_refresh or _is_stale(metadata, key) or not path.exists():
        print(f"Downloading FF3 {region} {frequency}...", end=" ", flush=True)
        df = _download_region(region, frequency)
        cache_dir.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path)
        metadata[key] = {"downloaded_at": datetime.now().isoformat()}
        _save_metadata(cache_dir, metadata)
        print(f"done ({len(df):,} rows).")
    else:
        df = pd.read_parquet(path)
    return df if in_percentages else df / 100
