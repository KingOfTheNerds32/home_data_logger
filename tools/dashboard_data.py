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
    solar_clear_days.csv     clear-day production per month and year (for degradation)
    solar_vs_2023.csv        output against 2023 on days with the same month and sunlight
    solar_year_change.csv    year-to-year change in output on days with matching sunlight
    battery_profile.csv      average battery charge by time of day, per season
    pool_daily.csv           pool and air temperatures, pump and heater hours, weather, per day
    hourly_flows.csv         average kWh per hour of day: car charging, battery, grid, solar, home

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


def car_monthly(since='2025-04'):
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
    return m[m.month >= since]   # the trip log starts Mar 29, 2025


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


def solar_clear_days():
    """Typical clear-day production per calendar month and year: the 90th
    percentile of daily production-meter kWh in that month. Comparing the same
    month across years shows lasting changes (ageing, soiling, shading) better
    than totals, which swing with the weather. Months with the 2026 string
    outage read low for that reason."""
    p = pd.read_csv('production_meter_5min.csv.gz')
    day = p.groupby(p.interval_end.str[:10]).meter_w.sum() / 12000
    d = day.rename('kwh').reset_index().rename(columns={'interval_end': 'date'})
    d['year'], d['month'] = d.date.str[:4], d.date.str[5:7].astype(int)
    out = d.groupby(['year', 'month']).agg(clear_day_kwh=('kwh', lambda x: x.quantile(0.9)), days=('kwh', 'size')).reset_index()
    out['clear_day_kwh'] = out.clear_day_kwh.round(1)
    return out[out.days >= 20]   # skip partial months (install, the current month)


def _sun_matched():
    """Daily output of the 39 panels that never went offline, with that day's
    sunlight (sunlight_daily.csv), grouped into cells of the same calendar
    month and the same sunlight (0.5 kWh/m2 bins). Comparing years within a
    cell removes weather, including the 15 kW clipping on very sunny days."""
    lay = pd.read_csv('panel_layout.csv', dtype={'serial': str})
    keep = sorted(lay[lay.on_failed_string == 0].serial)
    p = pd.read_csv('panels_daily.csv.gz', dtype={'serial': str}).dropna()
    day = p.pivot(index='date', columns='serial', values='wh')[keep]
    prod = day[(day > 0).all(axis=1)].sum(axis=1) / 1000
    sun = pd.read_csv('sunlight_daily.csv').set_index('date')
    d = pd.DataFrame({'kwh': prod}).join(sun, how='inner')
    d['year'], d['month'] = d.index.str[:4], d.index.str[5:7].astype(int)
    d['sun_bin'] = (d.sun_kwh_m2 * 2).round() / 2
    g = d.groupby(['month', 'sun_bin', 'year']).kwh
    # A cell needs at least MIN_DAYS days in a year to count for that year: single
    # cloudy days are where the modelled sunlight is least reliable.
    cells = g.median().where(g.size() >= MIN_DAYS).unstack()
    days = g.size().unstack().fillna(0)
    return cells, days


MIN_DAYS = 3


def _ratio(cells, days, a, b):
    """Change from year a to year b: the median ratio over cells both years
    have, each cell weighted by its days in the two years."""
    r = (cells[b] / cells[a]).dropna()
    w = (days[a] + days[b]).reindex(r.index)
    order = r.sort_values().index
    cum = w[order].cumsum() / w.sum()
    return r[order][cum >= 0.5].iloc[0], r.index, w


def solar_vs_2023():
    cells, days = _sun_matched()
    rows = []
    for year in [y for y in cells.columns if y != '2023']:
        for month in sorted(set(cells.index.get_level_values(0))):
            c, n = cells.loc[month], days.loc[month]
            if (c[year] / c['2023']).dropna().empty:
                continue
            v, idx, w = _ratio(c, n, '2023', year)
            rows.append({'year': year, 'month': month, 'vs_2023': round(v, 4), 'cells': len(idx), 'days': int(w.sum())})
    return pd.DataFrame(rows)


def solar_year_change():
    cells, days = _sun_matched()
    rows = []
    for a, b in [('2023', '2024'), ('2024', '2025'), ('2025', '2026'), ('2023', '2025'), ('2023', '2026')]:
        if a in cells and b in cells:
            v, idx, w = _ratio(cells, days, a, b)
            rows.append({'span': f'{a}-{b}', 'change': round(v - 1, 4), 'cells': len(idx), 'days': int(w.sum())})
    return pd.DataFrame(rows)


def battery_profile():
    """Average state of charge of the six batteries by time of day, per season,
    over all years (Apr 2023 on)."""
    b = pd.read_csv('battery_5min.csv.gz')
    s = b.groupby(['interval_end', 'tz']).soc_pct.mean().reset_index()
    t = pd.to_datetime(s.interval_end)
    seasons = {12: 'Winter', 1: 'Winter', 2: 'Winter', 3: 'Spring', 4: 'Spring', 5: 'Spring',
               6: 'Summer', 7: 'Summer', 8: 'Summer', 9: 'Fall', 10: 'Fall', 11: 'Fall'}
    s['season'] = t.dt.month.map(seasons)
    s['time'] = t.dt.strftime('%H:%M')
    out = s.groupby(['season', 'time']).soc_pct.agg(['mean', lambda x: x.quantile(0.1), lambda x: x.quantile(0.9)]).reset_index()
    out.columns = ['season', 'time', 'soc_pct', 'soc_p10', 'soc_p90']
    out[['soc_pct', 'soc_p10', 'soc_p90']] = out[['soc_pct', 'soc_p10', 'soc_p90']].round(1)
    return out


def pool_daily():
    """One row per day from the pool and weather logs (5-min, Apr 2026 on).
    Hours are summed from each reading's duration (capped at 10 minutes, so a
    logging gap doesn't count as running). Pool temperature only counts while
    the pump runs, since the sensor sits in the pipe."""
    p = pd.read_csv('pool_history.csv', low_memory=False)
    p['t'] = pd.to_datetime(p.timestamp)
    p['hours'] = (p.t.shift(-1) - p.t).dt.total_seconds().clip(upper=600).fillna(300) / 3600
    p['date'] = p.t.dt.strftime('%Y-%m-%d')
    on = lambda col, value='ON': (p[col] == value) * p.hours
    p['pump_h'], p['gas_h'], p['spill_h'] = on('pool_pump'), on('pool_heater'), on('spillover')
    p['hp_h'] = on('heat_pump')
    p['pool_temp_running'] = p.pool_temp.where(p.pool_pump == 'ON')
    # Water sitting in the pipe reads ~7 F cold when the pump starts; the day's low
    # counts only readings after the pump has run 30 minutes (6 readings).
    run = (p.pool_pump != p.pool_pump.shift()).cumsum()
    settled = p.groupby(run).cumcount() >= 6
    p['pool_temp_settled'] = p.pool_temp_running.where(settled)
    # The pool's own air sensor reads high in sun, so outdoor temperature comes from the weather log;
    d = p.groupby('date').agg(pool_temp_max=('pool_temp_running', 'max'), pool_temp_min=('pool_temp_settled', 'min'),
                              pump_hours=('pump_h', 'sum'), gas_heater_hours=('gas_h', 'sum'),
                              heat_pump_hours=('hp_h', 'sum'), spillover_hours=('spill_h', 'sum'))
    w = pd.read_csv('weather_history.csv')
    w['date'] = w.timestamp.str[:10]
    wd = w.groupby('date').agg(outdoor_temp_max=('temp_actual', 'max'), outdoor_temp_min=('temp_actual', 'min'),
                               clouds_pct=('clouds_percent', 'mean'), rain_mm=('rain_1h_mm', lambda x: x.sum() / 12))
    d = d.join(wd, how='left').reset_index().round(1)
    return d.iloc[1:-1]   # first and last days are partial


def hourly_flows(since='2026-08-08'):
    """Average kWh in each hour of the day for car charging, battery charge and
    discharge, grid import and export, solar and household use, from the
    15-minute data. Starts after the phantom EV reading ended (Aug 8, 2026)."""
    e = pd.read_csv('energy_history.csv')
    t = pd.to_datetime(e.interval_end) - pd.Timedelta(minutes=15)   # interval start
    e = e[t >= since].assign(hour=t[t >= since].dt.hour)
    days = t[t >= since].dt.date.nunique()
    cols = ['ev_charging_wh', 'battery_charge_wh', 'battery_discharge_wh', 'grid_import_wh',
            'grid_export_wh', 'production_wh', 'consumption_wh']
    h = (e.groupby('hour')[cols].sum() / days / 1000).round(3)
    h.columns = [c.replace('_wh', '_kwh') for c in cols]
    h['days'] = days
    return h.reset_index()


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
                        ('car_monthly', car_monthly), ('car_buckets', car_buckets),
                        ('solar_clear_days', solar_clear_days), ('solar_vs_2023', solar_vs_2023),
                        ('solar_year_change', solar_year_change), ('battery_profile', battery_profile),
                        ('pool_daily', pool_daily), ('hourly_flows', hourly_flows)]:
        build().to_csv(os.path.join(out, f'{name}.csv'), index=False)
        print(f"wrote {out}/{name}.csv")


if __name__ == '__main__':
    main()
