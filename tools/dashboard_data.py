"""
Summary tables for the Home energy dashboard, built from the home_data repo.

The dashboard reads small CSVs, not the raw history, so this script writes:

    monthly_energy.csv       solar, household use, grid, battery and EV-session kWh per month
    arrays_2025.csv          2025 kWh per panel, by array (panel_layout.csv)
    panels_2025.csv          2025 kWh for each panel and how it compares with its array
    washed_panel_ratio.csv   washed panels vs their string-mates, per month
    string_outage.csv        estimated production lost while a string was offline, per month
    ev_monthly.csv           EV session kWh vs Enphase's daily figure, per month
    car_monthly.csv          miles and the car's own kWh (Audi trips) vs home charging, per month
    car_buckets.csv          the car's efficiency by trip speed and by trip length

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
    s = pd.read_csv('ev_sessions.csv')
    m['ev_session_kwh'] = s.groupby(s.start_time.str[:7]).energy_kwh.sum().round(0)
    m['ev_session_kwh'] = m.ev_session_kwh.fillna(0)   # no home charger before Oct 2024
    return m.reset_index()


def arrays_2025():
    lay = pd.read_csv('panel_layout.csv', dtype={'serial': str})
    return lay.groupby('array').agg(panels=('serial', 'size'), avg_kwh_2025=('kwh_2025', 'mean'),
                                    min_kwh_2025=('kwh_2025', 'min'), max_kwh_2025=('kwh_2025', 'max')).round(0).reset_index()


def panels_2025():
    lay = pd.read_csv('panel_layout.csv', dtype={'serial': str})
    lay['vs_array_pct'] = (lay.kwh_2025 / lay.groupby('array').kwh_2025.transform('mean') - 1).round(4)
    return lay[['serial', 'array', 'kwh_2025', 'vs_array_pct', 'on_failed_string', 'washed_2026']].sort_values(['array', 'kwh_2025'])


def string_outage():
    """Estimated production lost while the 13-panel string was offline (2026).
    On each outage day: the other 39 panels' output times the string's usual
    share of it, taken as the median share within 10 days of the same date in
    other years. An estimate: it assumes the string would have performed as it
    usually does relative to the rest of the roof."""
    lay = pd.read_csv('panel_layout.csv', dtype={'serial': str})
    string = sorted(lay[lay.on_failed_string == 1].serial)
    p = pd.read_csv('panels_daily.csv.gz', dtype={'serial': str}).dropna()
    day = p.pivot(index='date', columns='serial', values='wh')
    on, rest = day[string].sum(axis=1), day.drop(columns=string).sum(axis=1)
    share = (on / rest)[(day[string] > 0).all(axis=1)]
    share.index = pd.to_datetime(share.index)
    out = pd.to_datetime(day.index[(on == 0) & (rest > 0)])
    rows = []
    for d in out:
        usual = share[(share.index.year != d.year) & ((share.index.dayofyear - d.dayofyear).map(abs) <= 10)].median()
        rows.append({'month': d.strftime('%Y-%m'), 'lost_kwh': usual * rest.loc[d.strftime('%Y-%m-%d')] / 1000})
    m = pd.DataFrame(rows).groupby('month').agg(lost_kwh=('lost_kwh', 'sum'), outage_days=('lost_kwh', 'size')).reset_index()
    m['lost_kwh'] = m.lost_kwh.round(0)
    return m


def car_monthly(since='2025-06'):
    """Miles and the car's own energy count (Audi trip log) against energy the
    home charger delivered (sessions). Charging away from home lowers the
    ratio; charging losses and energy used while parked raise it."""
    t = pd.read_csv('audi_trips.csv')
    s = pd.read_csv('ev_sessions.csv')
    t['month'] = t.end_time.str[:7]
    m = pd.DataFrame({'trips': t.groupby('month').size(), 'miles': t.groupby('month').miles.sum().round(0),
                      'car_kwh': t.groupby('month').kwh.sum().round(0),
                      'home_charging_kwh': s.groupby(s.start_time.str[:7]).energy_kwh.sum().round(0)})
    m = m.reset_index().rename(columns={'index': 'month'}).dropna(subset=['trips'])
    return m[m.month >= since]   # the trip log starts May 21, 2025


def car_buckets():
    t = pd.read_csv('audi_trips.csv')
    rows = []
    for dim, col, edges, labels in [
            ('speed', 'avg_mph', [0, 15, 25, 35, 50, 200], ['Under 15 mph', '15-25 mph', '25-35 mph', '35-50 mph', 'Over 50 mph']),
            ('length', 'miles', [0, 3, 10, 25, 1000], ['Under 3 mi', '3-10 mi', '10-25 mi', 'Over 25 mi'])]:
        t['bucket'] = pd.cut(t[col], edges, labels=labels, right=False)
        g = t.groupby('bucket', observed=True).agg(trips=('miles', 'size'), miles=('miles', 'sum'), kwh=('kwh', 'sum')).reset_index()
        g['dimension'] = dim
        g['order'] = range(len(g))
        g['mi_per_kwh'] = (g.miles / g.kwh).round(2)
        rows.append(g)
    out = pd.concat(rows)
    out[['miles', 'kwh']] = out[['miles', 'kwh']].round(0)
    return out[['dimension', 'order', 'bucket', 'trips', 'miles', 'kwh', 'mi_per_kwh']]


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
                        ('panels_2025', panels_2025), ('washed_panel_ratio', washed_panel_ratio),
                        ('string_outage', string_outage), ('ev_monthly', ev_monthly),
                        ('car_monthly', car_monthly), ('car_buckets', car_buckets)]:
        build().to_csv(os.path.join(out, f'{name}.csv'), index=False)
        print(f"wrote {out}/{name}.csv")


if __name__ == '__main__':
    main()
