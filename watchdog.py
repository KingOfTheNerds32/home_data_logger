"""
Watchdog: fails if any data source has gone stale, so GitHub's failure email
catches problems that produce no failed runs at all (e.g. the external
cron-job.org trigger stopping, or an expired token).

Runs inside the private data repo checkout. Prints only ages and OK/STALE,
never data values, because run logs on the public repo are visible to anyone.

Usage (from the data repo checkout): python ../watchdog.py
"""
import os
import sys
from datetime import datetime, timedelta

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

    summary = "\n".join(lines) + "\n"
    print(summary)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as f:
            f.write(summary)
    if stale:
        print(f"::error::Stale data: {', '.join(stale)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
