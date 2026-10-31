"""
Summary tables for the Home energy dashboard, built from the home_data repo.

The dashboard reads small CSVs, not the raw history, so this script writes:

    monthly_energy.csv       solar, household use, grid and battery kWh per month
    arrays_2025.csv          2025 kWh per panel, by array (panel_layout.csv)
    washed_panel_ratio.csv   washed panels vs their string-mates, per month
    ev_monthly.csv           EV session kWh vs Enphase's daily figure, per month

Usage (from the home_data checkout):
    python ../tools/dashboard_data.py OUT_DIR
Then upload the files to the dashboard (ask Claude to refresh it).
"""
import os
import sys

import pandas as pd

WASHED = {'202239007222', '202239035769', '202239032861', '202239032948'}   # washed 2026-06-05


def monthly_energy():
    e = pd.read_csv('energy_daily.csv')
    e['month'] = e.date.str[:7]
    cols = ['production_wh', 'consumption_wh', 'grid_import_wh', 'grid_export_wh',
            'battery_charge_wh', 'battery_discharge_wh']
    m = (e.groupby('month')[cols].sum() / 1000).round(0)
    m.columns = [c.replace('_wh', '_kwh') for c in cols]
    m['days'] = e.groupby('month').size()   # a partial month has fewer days
    return m.reset_index()


def arrays_2025():
    lay = pd.read_csv('panel_layout.csv', dtype={'serial': str})
    return lay.groupby('array').agg(panels=('serial', 'size'), avg_kwh_2025=('kwh_2025', 'mean'),
                                    min_kwh_2025=('kwh_2025', 'min'), max_kwh_2025=('kwh_2025', 'max')).round(0).reset_index()


def washed_panel_ratio():
    """Daily output of the washed panels divided by the unwashed panels on the
    same string, averaged per month. Only clear days with the whole string
    online count: total output at least 70% of the best day within a week
    either side. Comparing the same months across years cancels seasonal
    shading (see analyses.md, Solar)."""
    lay = pd.read_csv('panel_layout.csv', dtype={'serial': str})
    string = set(lay[lay.on_failed_string == 1].serial)
    others = sorted(string - WASHED)
    p = pd.read_csv('panels_daily.csv.gz', dtype={'serial': str}).dropna()
    day = p.pivot(index='date', columns='serial', values='wh')
    total = day.sum(axis=1)
    clear = total > total.rolling(15, center=True, min_periods=5).max() * 0.7
    good = day[(day[sorted(string)] > 0).all(axis=1) & clear]
    r = (good[sorted(WASHED)].mean(axis=1) / good[others].mean(axis=1)).rename('ratio').reset_index()
    r['month'] = r.date.str[:7]
    m = r.groupby('month').agg(ratio=('ratio', 'mean'), clear_days=('ratio', 'size')).reset_index()
    m['ratio'] = m.ratio.round(4)
    return m


def ev_monthly(since='2024-11'):
    s = pd.read_csv('ev_sessions.csv')
    d = pd.read_csv('ev_daily.csv')
    s['month'] = s.start_time.str[:7]
    d['month'] = d.date.str[:7]
    ev = pd.DataFrame({'sessions': s.groupby('month').size(),
                       'session_kwh': s.groupby('month').energy_kwh.sum().round(0),
                       'reported_kwh': (d.groupby('month').ev_wh.sum() / 1000).round(0)})
    ev = ev.reset_index().rename(columns={'index': 'month'})
    return ev[ev.month >= since]


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else 'dashboard'
    os.makedirs(out, exist_ok=True)
    for name, build in [('monthly_energy', monthly_energy), ('arrays_2025', arrays_2025),
                        ('washed_panel_ratio', washed_panel_ratio), ('ev_monthly', ev_monthly)]:
        build().to_csv(os.path.join(out, f'{name}.csv'), index=False)
        print(f"wrote {out}/{name}.csv")


if __name__ == '__main__':
    main()
