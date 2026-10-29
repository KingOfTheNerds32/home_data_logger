"""
Watchdog: fails if any data source has gone stale, so GitHub's failure email
catches problems that produce no failed runs at all (e.g. the external
cron-job.org trigger stopping, or an expired token). Also fails if fewer
microinverters reported at midday than normal, which means part of the solar
array is offline (as happened for two months in spring 2026), or if the Home
Assistant Pi has stopped uploading its nightly ha/ export.

Runs inside the private data repo checkout. Prints only ages and OK/STALE,
never data values, because run logs on the public repo are visible to anyone.

Usage (from the data repo checkout): python ../watchdog.py
"""
import os
import sys
from datetime import datetime, timedelta

import pandas as pd
import pytz

PACIFIC = pytz.timezone('America/Los_Angeles')

# (file, timestamp column, how old the newest row may get)
CHECKS = [
    # Polled every 5 minutes.
    ('pool_history.csv', 'timestamp', timedelta(minutes=45)),
    ('weather_history.csv', 'timestamp', timedelta(minutes=45)),
    # Pulled nightly for complete days, so the newest interval is the most
    # recent midnight; allow for one missed night.
    ('energy_history.csv', 'interval_end', timedelta(hours=54)),
]


def newest_timestamp(path, column):
    """Newest value of a timestamp column, reading only the file's tail."""
    with open(path, 'rb') as f:
        header = f.readline().decode().strip().split(',')
        f.seek(0, os.SEEK_END)
        f.seek(max(0, f.tell() - 65536))
        lines = f.read().decode(errors='ignore').splitlines()[1:]
    index = header.index(column)
    values = [l.split(',')[index] for l in lines if l.count(',') >= index]
    return PACIFIC.localize(datetime.strptime(max(values), "%Y-%m-%d %H:%M:%S"))


def check_home_assistant(today):
    """Newest uploaded Home Assistant day (ha/YYYY-MM/YYYY-MM-DD.csv.gz).

    The Pi uploads each finished day after 00:20, so yesterday is normally the
    newest; one missed night is allowed. Returns (status line, problem or None).
    """
    days = [name[:10] for root, _, files in os.walk('ha') for name in files if name.endswith('.csv.gz')]
    if not days:
        return "| Home Assistant `ha/` | no days uploaded yet | | ⏳ |", None
    newest = datetime.strptime(max(days), "%Y-%m-%d").date()
    behind = (today - newest).days
    ok = behind <= 2
    line = f"| Home Assistant `ha/` | {newest} | {behind} d | {'✅ OK' if ok else '❌ STALE'} |"
    return line, None if ok else f"Home Assistant export last uploaded {newest}"


def check_microinverters():
    """Midday microinverter count on the latest complete day vs the usual count.

    Returns (status line, problem or None). The expected count is the most
    ever seen reporting, so it adapts if panels are added.
    """
    path = 'production_5min.csv'
    if not os.path.exists(path):
        return "| Microinverters | no data yet | | ⏳ |", None
    d = pd.read_csv(path)
    if 'devices_reporting' not in d or d['devices_reporting'].dropna().empty:
        return "| Microinverters | not recorded yet | | ⏳ |", None
    d['t'] = pd.to_datetime(d['interval_end'])
    expected = int(d['devices_reporting'].max())
    latest_day = d['t'].max().normalize() - pd.Timedelta(days=1) if d['t'].max().hour == 0 else d['t'].max().normalize()
    midday = d[(d['t'].dt.normalize() == latest_day) & d['t'].dt.hour.between(10, 13)]
    if midday.empty:
        return "| Microinverters | no midday data | | ⏳ |", None
    reporting = int(midday['devices_reporting'].max())
    ok = reporting >= expected
    line = f"| Microinverters | {latest_day:%Y-%m-%d} midday | {reporting} of {expected} | {'✅ OK' if ok else '❌ OFFLINE'} |"
    return line, None if ok else f"only {reporting} of {expected} microinverters reporting on {latest_day:%Y-%m-%d}"


def main():
    now = datetime.now(PACIFIC)
    stale = []
    lines = ["## Data Watchdog", "", "| Data | Newest | Age | Status |", "| :--- | :--- | :--- | :--- |"]
    for path, column, limit in CHECKS:
        if not os.path.exists(path):
            stale.append(path)
            lines.append(f"| `{path}` | missing | | ❌ STALE |")
            continue
        try:
            newest = newest_timestamp(path, column)
        except Exception as e:
            stale.append(path)
            lines.append(f"| `{path}` | unreadable ({type(e).__name__}) | | ❌ STALE |")
            continue
        age = now - newest
        hours = age.total_seconds() / 3600
        ok = age <= limit
        if not ok:
            stale.append(path)
        lines.append(f"| `{path}` | {newest:%Y-%m-%d %H:%M} | {hours:.1f} h | {'✅ OK' if ok else '❌ STALE'} |")

    try:
        line, problem = check_microinverters()
    except Exception as e:
        line, problem = f"| Microinverters | unreadable ({type(e).__name__}) | | ❌ |", "microinverter data unreadable"
    lines.append(line)
    if problem:
        stale.append(problem)

    try:
        line, problem = check_home_assistant(now.date())
    except Exception as e:
        line, problem = f"| Home Assistant `ha/` | unreadable ({type(e).__name__}) | | ❌ |", "ha/ unreadable"
    lines.append(line)
    if problem:
        stale.append(problem)

    summary = "\n".join(lines) + "\n"
    print(summary)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as f:
            f.write(summary)
    if stale:
        print(f"::error::Problems: {', '.join(stale)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
