"""
Merge reports exported by hand from Enlighten Manager into the home_data repo.

Enlighten's report pages (Reports tab) can only be downloaded one period at a
time, so they were collected with browser-console scripts and emailed or saved
as CSVs. This script turns those CSVs into the tidy files in home_data:

    production  "Site Recent Power Production" (5 min, a week per report)
                -> production_meter_5min.csv.gz
    battery     "Battery" reports (5 min per battery, up to a week each)
                -> battery_5min.csv.gz
    panels      "Per Module One Day Energy Production" (a day per report)
                -> panels_daily.csv.gz
    ev          "EV Charging Sessions" and "Daily EV Charging"
                -> ev_sessions.csv, ev_daily.csv

Each command reads any number of CSV files (or folders, or .zip files of them),
merges them with what is already stored, and rewrites the output. Running it
again on the same files changes nothing. Keep the original exports in
imports/enlighten/ so every stored number can be traced back.

Times are Pacific local time, as Enlighten writes them. Where clocks fall back
and an hour repeats, a `tz` column (PDT/PST) tells the two apart.

Usage (from the home_data checkout):
    python ../tools/enlighten_import.py production imports/enlighten/emailed_reports_2026-10-30.zip
    python ../tools/enlighten_import.py battery path/to/battery_*.csv
    python ../tools/enlighten_import.py panels path/to/panels_*.csv
    python ../tools/enlighten_import.py ev path/to/ev_*.csv
"""
import argparse
import glob
import io
import os
import sys
import zipfile

import pandas as pd

TIME_FORMAT = '%Y-%m-%d %H:%M:%S'
GZIP = {'method': 'gzip', 'mtime': 0}   # mtime 0: same content, same bytes
PHANTOM_EV = ('2025-08-11', '2026-08-07')   # see analyses.md (EV)


def csv_texts(paths):
    """(name, text) for every CSV in the given files, folders and zips."""
    for path in paths:
        if os.path.isdir(path):
            yield from csv_texts(sorted(glob.glob(os.path.join(path, '**', '*'), recursive=True)))
        elif path.endswith('.zip'):
            with zipfile.ZipFile(path) as z:
                for name in sorted(z.namelist()):
                    if name.lower().endswith('.csv'):
                        yield name, z.read(name).decode('utf-8-sig')
        elif path.lower().endswith('.csv'):
            with open(path, encoding='utf-8-sig') as f:
                yield os.path.basename(path), f.read()


def read_table(text, header_starts):
    """A report's table, skipping any lines above the header row."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip('"').startswith(header_starts)), None)
    if start is None:
        return None
    return pd.read_csv(io.StringIO('\n'.join(lines[start:])), thousands=',', dtype=str)


def number(values):
    """Report numbers as floats; Enlighten writes thousands as '1,025'."""
    return pd.to_numeric(values.astype(str).str.replace(',', '', regex=False))


def with_tz(times, repeat):
    """PDT/PST for local times; `repeat` marks the second pass through a fall-back hour."""
    tz = times.dt.tz_localize('America/Los_Angeles', ambiguous=True, nonexistent='NaT').dt.strftime('%Z')
    return tz.where(~repeat, 'PST')


def sort_local(df, *keys):
    """Sort by true time order (PST after the matching PDT hour), then keys."""
    order = pd.to_datetime(df['interval_end']) + pd.to_timedelta((df['tz'] == 'PST').astype(int), unit='h')
    return df.assign(_order=order).sort_values(['_order', *keys]).drop(columns='_order')


# ---------------------------------------------------------------- production

def read_production(paths):
    """5-min production: `meter_w` (production meter) or `inverter_w` (older
    reports, microinverter basis). Report times end in ' PDT'/' PST'."""
    parts = []
    for name, text in csv_texts(paths):
        df = read_table(text, 'Date/Time')
        if df is None or df.shape[1] != 2:
            continue
        kind = {'Power Delivered (W)': 'meter_w', 'Average Real Power (W)': 'inverter_w'}.get(df.columns[1])
        if kind is None:
            continue
        df.columns = ['t', 'w']
        df['kind'] = kind
        parts.append(df)
    if not parts:
        return pd.DataFrame(columns=['interval_end', 'tz', 'meter_w', 'inverter_w'])
    a = pd.concat(parts)
    a['interval_end'] = pd.to_datetime(a.t.str.replace(r'\s+[A-Z]{3}$', '', regex=True),
                                       format='%m/%d/%Y %I:%M %p').dt.strftime(TIME_FORMAT)
    a['tz'] = a.t.str[-3:]
    a['w'] = number(a.w)
    p = a.groupby(['interval_end', 'tz', 'kind']).w.last().unstack().reset_index()
    for col in ('meter_w', 'inverter_w'):
        if col not in p:
            p[col] = pd.NA
    return p[['interval_end', 'tz', 'meter_w', 'inverter_w']]


def merge_production(new, path='production_meter_5min.csv.gz'):
    old = pd.read_csv(path) if os.path.exists(path) else new.iloc[0:0]
    merged = pd.concat([old, new]).groupby(['interval_end', 'tz'], as_index=False).last()
    for col in ('meter_w', 'inverter_w'):
        merged[col] = merged[col].astype('Int64')
    sort_local(merged).to_csv(path, index=False, compression=GZIP)
    return len(merged)


# ------------------------------------------------------------------- battery

def read_battery(paths):
    """Per-battery 5-min power (kW, + = charging) and state of charge. Reports
    list each battery in time order, so a repeated time is the fall-back hour."""
    parts = []
    for name, text in csv_texts(paths):
        df = read_table(text, 'Battery Serial #')
        if df is None:
            continue
        df = df.iloc[:, :5]
        df.columns = ['serial', 'interval_end', 'power_kw', 'kwh', 'soc_pct']
        t = pd.to_datetime(df.interval_end, format='%Y/%m/%d %H:%M:%S')
        df['tz'] = with_tz(t, df.assign(t=t).groupby(['serial', 't']).cumcount() == 1)
        df['interval_end'] = t.dt.strftime(TIME_FORMAT)
        parts.append(df[['interval_end', 'tz', 'serial', 'power_kw', 'soc_pct']])
    return pd.concat(parts) if parts else pd.DataFrame(columns=['interval_end', 'tz', 'serial', 'power_kw', 'soc_pct'])


def merge_battery(new, path='battery_5min.csv.gz'):
    old = pd.read_csv(path, dtype={'serial': str}) if os.path.exists(path) else new.iloc[0:0]
    new = new.assign(power_kw=number(new.power_kw), soc_pct=number(new.soc_pct))
    merged = pd.concat([old, new]).drop_duplicates(['interval_end', 'tz', 'serial'], keep='last')
    sort_local(merged, 'serial').to_csv(path, index=False, compression=GZIP)
    return len(merged)


# -------------------------------------------------------------------- panels

def meter_daily_wh(path='production_meter_5min.csv.gz'):
    p = pd.read_csv(path)
    return p.groupby(p.interval_end.str[:10]).meter_w.sum() / 12


def read_panels(paths, meter_wh):
    """Daily Wh per microinverter. Enlighten writes each day in kWh, or in Wh
    on low days, without saying which; the production meter decides, since the
    two differ by 1,000x. A day reported as all zeros while the meter shows
    production (e.g. 2026-03-08) is stored blank."""
    parts = []
    for name, text in csv_texts(paths):
        df = read_table(text, 'date')
        if df is None or df.shape[1] != 3:
            continue
        df.columns = ['date', 'serial', 'v']
        parts.append(df)
    if not parts:
        return pd.DataFrame(columns=['date', 'serial', 'wh'])
    d = pd.concat(parts).drop_duplicates(['date', 'serial'], keep='last')
    d['v'] = number(d.v)
    total = d[d.serial == 'Total'].set_index('date').v
    ratio = total / meter_wh.reindex(total.index)
    scale = (ratio > 0.1).map({True: 1, False: 1000})   # Wh days read ~1.03x the meter, kWh days ~0.001x
    d = d[d.serial != 'Total'].copy()
    d['wh'] = (d.v * d.date.map(scale)).round().astype('Int64')
    dead = total[(total == 0) & (meter_wh.reindex(total.index) > 0)].index
    d.loc[d.date.isin(dead), 'wh'] = pd.NA
    return d[['date', 'serial', 'wh']]


def merge_panels(new, path='panels_daily.csv.gz'):
    old = pd.read_csv(path, dtype={'serial': str}) if os.path.exists(path) else new.iloc[0:0]
    merged = pd.concat([old, new]).drop_duplicates(['date', 'serial'], keep='last')
    merged['wh'] = merged.wh.astype('Int64')
    merged.sort_values(['date', 'serial']).to_csv(path, index=False, compression=GZIP)
    return len(merged)


# ------------------------------------------------------------------------ EV

def read_ev(paths):
    """(sessions, daily) from the EV session and daily-charging reports."""
    sessions, daily = [], []
    for name, text in csv_texts(paths):
        s = read_table(text, 'Charger Name')
        if s is not None:
            sessions.append(s)
            continue
        d = read_table(text, 'Date')
        if d is not None and 'Total' in d.columns:
            daily.append(d)
    out_s = pd.DataFrame(columns=['start_time'])
    if sessions:
        x = pd.concat(sessions)
        fmt = lambda c: pd.to_datetime(x[c], format='%Y/%m/%d %H:%M:%S').dt.strftime(TIME_FORMAT)
        out_s = pd.DataFrame({
            'start_time': fmt('Start Date/Time'), 'end_time': fmt('End Date/Time'),
            'duration_s': x['Session Duration'].map(duration_s),
            'charge_time_s': x['Active Charge Time'].map(duration_s),
            'energy_kwh': (number(x['Energy consumed (Wh)']) / 1000).round(2)}).drop_duplicates('start_time')
    out_d = pd.DataFrame(columns=['date', 'ev_wh'])
    if daily:
        x = pd.concat(daily)
        out_d = pd.DataFrame({'date': pd.to_datetime(x.Date, format='%Y/%m/%d').dt.strftime('%Y-%m-%d'),
                              'ev_wh': number(x.Total).round().astype(int)}).drop_duplicates('date', keep='last')
    return out_s, out_d


def duration_s(text):
    """'13 hr 20 min' -> seconds."""
    units = {'hr': 3600, 'min': 60, 'sec': 1}
    words = str(text).split()
    return sum(int(n) * units[u] for n, u in zip(words[::2], words[1::2]) if u in units)


def merge_ev(sessions, daily, sessions_path='ev_sessions.csv', daily_path='ev_daily.csv'):
    """Sessions from the API (with miles and cost) win over the export's copy
    of the same session. The newest export of a day wins, since Enphase revises
    recent days (see analyses.md on the phantom load)."""
    old_s = pd.read_csv(sessions_path) if os.path.exists(sessions_path) else sessions.iloc[0:0]
    s = pd.concat([sessions, old_s]).drop_duplicates('start_time', keep='last').sort_values('start_time')
    s.to_csv(sessions_path, index=False)
    old_d = pd.read_csv(daily_path)[['date', 'ev_wh']] if os.path.exists(daily_path) else daily
    d = pd.concat([old_d, daily]).drop_duplicates('date', keep='last').sort_values('date')
    d['phantom'] = d.date.between(*PHANTOM_EV).astype(int)
    by_day = (s.assign(date=s.start_time.str[:10]).groupby('date').energy_kwh.sum() * 1000).round()
    d['session_wh'] = d.date.map(by_day).fillna(0).astype(int)
    d.to_csv(daily_path, index=False)
    return len(s), len(d)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('kind', choices=['production', 'battery', 'panels', 'ev'])
    ap.add_argument('paths', nargs='+', help='CSV files, folders or .zip files')
    args = ap.parse_args()
    if args.kind == 'production':
        print(f"production_meter_5min.csv.gz: {merge_production(read_production(args.paths))} rows")
    elif args.kind == 'battery':
        print(f"battery_5min.csv.gz: {merge_battery(read_battery(args.paths))} rows")
    elif args.kind == 'panels':
        print(f"panels_daily.csv.gz: {merge_panels(read_panels(args.paths, meter_daily_wh()))} rows")
    else:
        n_s, n_d = merge_ev(*read_ev(args.paths))
        print(f"ev_sessions.csv: {n_s} rows; ev_daily.csv: {n_d} rows")


if __name__ == '__main__':
    sys.exit(main())
