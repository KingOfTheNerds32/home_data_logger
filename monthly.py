"""
Monthly CSV files for the logs written every 5 minutes.

The pool, weather and run logs used to be single, ever-growing CSVs
(pool_history.csv, ...). They are committed ~280 times a day, and git keeps a
new version of the whole file each time, so the data repo grew fastest from
them. Each log now lives in one file per month, e.g. pool/2026-11.csv, so each
commit rewrites only the current month.

    append('pool', row_dataframe, '2026-11-12 08:35:00')
    read('pool')               # the whole history as one DataFrame
    read('runs', months=2)     # just the last two monthly files
    latest('weather')          # path of the newest monthly file

A leftover single file (pool_history.csv, ...) is split into monthly files
the first time append() runs, then deleted.
"""
import glob
import os

import pandas as pd

# log name -> the single file it used to live in
LEGACY = {'pool': 'pool_history.csv', 'weather': 'weather_history.csv', 'runs': 'execution_log.csv'}


def path(name, ts):
    """Monthly file for a 'YYYY-MM-DD ...' timestamp."""
    return os.path.join(name, f"{str(ts)[:7]}.csv")


def files(name):
    return sorted(glob.glob(os.path.join(name, '????-??.csv')))


def latest(name):
    """Newest monthly file, or the old single file before migration."""
    f = files(name)
    if f:
        return f[-1]
    legacy = LEGACY.get(name)
    return legacy if legacy and os.path.exists(legacy) else None


def read(name, months=None, **kw):
    """All monthly files (or the last `months`) as one DataFrame; None if there are none."""
    f = files(name)
    if months:
        f = f[-months:]
    legacy = LEGACY.get(name)
    if legacy and os.path.exists(legacy):
        f = [legacy] + f
    if not f:
        return None
    return pd.concat([pd.read_csv(p, **kw) for p in f], ignore_index=True)


def migrate(name):
    """Split the old single file into monthly files, then delete it.

    Copies lines exactly as written (same text, same order, nothing dropped):
    each data line goes to the month in its first 7 characters (the timestamp
    is the first column). Any lines already in a monthly file follow the old
    ones."""
    legacy = LEGACY.get(name)
    if not legacy or not os.path.exists(legacy):
        return
    with open(legacy, newline='') as f:
        header, *lines = f.read().splitlines(keepends=True)
    if not header.startswith('timestamp'):
        raise ValueError(f"{legacy}: expected the timestamp as the first column")
    if not header.endswith('\n'):
        header += '\n'
    months = {}
    for line in lines:
        if line.strip():
            months.setdefault(line[:7], []).append(line if line.endswith('\n') else line + '\n')
    os.makedirs(name, exist_ok=True)
    for month, rows in months.items():
        p = path(name, month)
        existing = []
        if os.path.exists(p):
            with open(p, newline='') as f:
                existing = f.read().splitlines(keepends=True)[1:]
        with open(p, 'w', newline='') as f:
            f.writelines([header] + rows + existing)
    os.remove(legacy)


def append(name, df, ts):
    """Append rows to the month of `ts`, writing the header if the file is new."""
    migrate(name)
    p = path(name, ts)
    os.makedirs(name, exist_ok=True)
    new = not os.path.exists(p) or os.stat(p).st_size == 0
    df.to_csv(p, mode='a', header=new, index=False)
    return p
