"""
Weekly summary report, written to home_data/reports/weekly/<week start>.md.

Covers the last complete Monday-Sunday week (Pacific time):
- Solar: production against what that week's sunlight usually yields
  (the same calendar month in other years), and whether all microinverters
  reported.
- Battery: overnight grid charging and discharge, and the backup left each
  morning.
- Car: home charging sessions and when they happened.
- Pool: pump, gas heater and heat pump hours, and temperatures.
- Grid: energy bought and sent, and how much was bought in the 5-8 PM peak.
- Data: gaps in each source.

Runs inside the private data repo checkout. Prints only the report's path,
never its contents, because run logs on the public repo are visible to anyone.

Usage (from the data repo checkout): python ../weekly_report.py [YYYY-MM-DD]
(a date inside the week to report; default: last complete week)
"""
import os
import sys
from datetime import date, datetime, timedelta

import pandas as pd
import pytz

PACIFIC = pytz.timezone('America/Los_Angeles')
BATTERY_KWH = 20.16          # 2 x IQ Battery 10T
PEAK_HOURS = range(17, 20)   # SCE TOU-D-5-8PM peak, 5-8 PM
# Enphase's EV figure carried a phantom 234 Wh per 15 min until this date
# (see analyses.md, EV); it is removed before reporting.
PHANTOM_EV_END = pd.Timestamp('2026-08-08')
OUT_DIR = os.path.join('reports', 'weekly')


def week_bounds(day=None):
    """Monday and Sunday of the week containing `day` (default: last complete week)."""
    if day is None:
        today = datetime.now(PACIFIC).date()
        day = today - timedelta(days=today.weekday() + 7)
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=6)


def read(path, **kw):
    return pd.read_csv(path, **kw) if os.path.exists(path) else None


def fifteen_min(start, end):
    e = read('energy_history.csv')
    if e is None:
        return None
    e['t'] = pd.to_datetime(e.interval_end) - pd.Timedelta(minutes=15)   # interval start
    e = e[(e.t.dt.date >= start) & (e.t.dt.date <= end)].copy()
    if 'ev_charging_wh' in e:
        phantom = (e.t < PHANTOM_EV_END) * 234
        e['ev_charging_wh'] = (e.ev_charging_wh - phantom).clip(lower=0)
    return e


def solar_section(start, end, e):
    lines = ['## Solar']
    if e is None or e.empty:
        return lines + ['No production data for this week yet.']
    day = e.groupby(e.t.dt.date).production_wh.sum() / 1000
    lines.append(f"Produced **{day.sum():,.0f} kWh** ({day.mean():,.1f} kWh/day).")
    sun = read('sunlight_daily.csv')
    daily = read('energy_daily.csv')
    if sun is not None and daily is not None:
        both = daily.set_index('date').join(sun.set_index('date'), how='inner')
        both['ratio'] = both.production_wh / 1000 / both.sun_kwh_m2
        week = [d.isoformat() for d in pd.date_range(start, end).date]
        month = start.strftime('-%m-')
        history = both[(both.index.str[4:8] == month) & (~both.index.isin(week)) & (both.sun_kwh_m2 > 0)]
        wk_sun = sun.set_index('date').reindex(week).sun_kwh_m2
        if len(history) >= 20 and wk_sun.notna().sum() >= 5:
            expected = (wk_sun.dropna() * history.ratio.median()).sum()
            actual = day[[d for d in day.index if d.isoformat() in wk_sun.dropna().index]].sum()
            pct = actual / expected - 1
            flag = ' Worth a look: well below what the sun usually gives.' if pct < -0.10 else ''
            lines.append(f"Against this week's sunlight: **{pct:+.0%}** versus the usual for "
                         f"{start:%B} in other years ({actual:,.0f} vs {expected:,.0f} kWh expected).{flag}")
        else:
            lines.append("Sunlight comparison: not enough sunlight data yet for this week.")
    micro = read('production_5min.csv')
    if micro is not None and 'devices_reporting' in micro:
        micro['t'] = pd.to_datetime(micro.interval_end)
        mid = micro[(micro.t.dt.date >= start) & (micro.t.dt.date <= end) & micro.t.dt.hour.between(10, 13)]
        if not mid.empty:
            per_day = mid.groupby(mid.t.dt.date).devices_reporting.max()
            expected = int(micro.devices_reporting.max())
            short = per_day[per_day < expected]
            lines.append(f"Microinverters: all {expected} reported every day." if short.empty else
                         f"Microinverters: fewer than {expected} reported on {', '.join(f'{d:%a %b %-d} ({n})' for d, n in short.items())}.")
    return lines


def battery_section(start, end):
    """Nights are noon-to-noon; the week's 7 nights are those ending on its
    mornings, so the evening before the week is read too."""
    lines = ['## Battery']
    e = fifteen_min(start - timedelta(days=1), end)
    if e is None or e.empty or 'battery_soc_pct' not in e:
        return lines + ['No battery data for this week yet.']
    e = e.assign(night=(e.t - pd.Timedelta(hours=12)).dt.date, hour=e.t.dt.hour)
    e = e[(e.night >= start - timedelta(days=1)) & (e.night <= end - timedelta(days=1))]
    nights = e.groupby('night').apply(lambda g: pd.Series({
        'grid_charge': g[g.hour.isin([22, 23, 0, 1])].battery_charge_wh.sum() / 1000,
        'overnight_out': g[g.hour.between(0, 6)].battery_discharge_wh.sum() / 1000,
        'soc_7am': g[g.hour == 7].battery_soc_pct.min(),
        'peak_out': g[g.hour.isin(PEAK_HOURS)].battery_discharge_wh.sum() / 1000}))
    nights = nights.dropna(subset=['soc_7am'])
    topped = (nights.grid_charge > 2).sum()
    lines.append(f"Evening peak (5-8 PM): the battery supplied {nights.peak_out.mean():.1f} kWh a day on average.")
    lines.append(f"Overnight: charged from the grid on **{topped} of {len(nights)} nights** "
                 f"({nights.grid_charge.sum():.0f} kWh), discharged {nights.overnight_out.mean():.1f} kWh a night.")
    low = nights.soc_7am.min()
    lines.append(f"Backup left at 7 AM: {nights.soc_7am.mean():.0f}% on average "
                 f"(~{nights.soc_7am.mean() / 100 * BATTERY_KWH:.0f} kWh), lowest {low:.0f}%.")
    return lines


def car_section(start, end, e):
    lines = ['## Car']
    s = read('ev_sessions.csv')
    if s is None:
        return lines + ['No charging data.']
    s['start'], s['end'] = pd.to_datetime(s.start_time), pd.to_datetime(s.end_time)
    # Sessions overlapping the week (one started Sunday night runs into Monday).
    s = s[(s.end.dt.date >= start) & (s.start.dt.date <= end)]
    if s.empty:
        return lines + ['No home charging sessions this week.']
    if e is not None and not e.empty and e.ev_charging_wh.notna().any():
        lines.append(f"Charged **{e.ev_charging_wh.sum() / 1000:.0f} kWh** at home, in {len(s)} sessions.")
    else:
        lines.append(f"{len(s)} home charging sessions, {s.energy_kwh.sum():.0f} kWh.")
    if e is not None and not e.empty:
        ev = e[e.ev_charging_wh > 250]   # over 1 kW
        if not ev.empty:
            kwh = lambda m: ev[m].ev_charging_wh.sum() / 1000
            h = ev.t.dt.hour
            lines.append(f"When: {kwh(h.isin(PEAK_HOURS)):.0f} kWh in the 5-8 PM peak, "
                         f"{kwh(h.between(8, 16)):.0f} kWh in daylight (8 AM-5 PM), "
                         f"{kwh(~h.between(8, 19)):.0f} kWh overnight.")
            lines.append(f"From the battery while charging: {ev.battery_discharge_wh.sum() / 1000:.0f} kWh.")
    return lines


def pool_section(start, end):
    lines = ['## Pool']
    p = read('pool_history.csv', low_memory=False)
    if p is None:
        return lines + ['No pool data.']
    p['t'] = pd.to_datetime(p.timestamp)
    p = p[(p.t.dt.date >= start) & (p.t.dt.date <= end)].copy()
    if p.empty:
        return lines + ['No pool readings this week.']
    p['hours'] = (p.t.shift(-1) - p.t).dt.total_seconds().clip(upper=600).fillna(300) / 3600
    hrs = lambda col: (p[col].astype(str) == 'ON').mul(p.hours).sum() if col in p else 0.0
    peak = p.t.dt.hour.isin(PEAK_HOURS)
    run = (p.pool_pump != p.pool_pump.shift()).cumsum()
    settled = p.pool_temp.where((p.pool_pump == 'ON') & (p.groupby(run).cumcount() >= 6))
    days = (end - start).days + 1
    lines.append(f"Pump: {hrs('pool_pump') / days:.1f} hours a day.")
    gas, hp = hrs('pool_heater'), hrs('heat_pump')
    hp_peak = ((p.get('heat_pump', pd.Series(dtype=str)).astype(str) == 'ON') & peak).mul(p.hours).sum()
    hp_logged = 'heat_pump' in p and p.heat_pump.notna().any()
    hp_text = (f"heat pump {hp:.1f} h" + (f", of which {hp_peak:.1f} h in the 5-8 PM peak" if hp_peak > 0.1 else '')
               if hp_logged else "heat pump not logged this week (logging began Oct 26, 2026)")
    lines.append(f"Heating: gas heater {gas:.1f} h (~{gas * 4:.0f} therms), {hp_text}.")
    if settled.notna().any():
        lines.append(f"Pool temperature with the pump running: {settled.min():.0f}-{settled.max():.0f} °F.")
    return lines


def grid_section(e):
    lines = ['## Grid']
    if e is None or e.empty:
        return lines + ['No grid data for this week yet.']
    peak = e.t.dt.hour.isin(PEAK_HOURS)
    lines.append(f"Bought **{e.grid_import_wh.sum() / 1000:,.0f} kWh**, sent {e.grid_export_wh.sum() / 1000:,.0f} kWh; "
                 f"bought in the 5-8 PM peak: {e[peak].grid_import_wh.sum() / 1000:,.1f} kWh.")
    lines.append(f"Household use (incl. car): {e.consumption_wh.sum() / 1000:,.0f} kWh.")
    return lines


def data_section(start, end, e):
    lines = ['## Data']
    days = pd.date_range(start, end).date
    problems = []
    if e is None:
        problems.append('no 15-min energy data')
    else:
        counts = e.groupby(e.t.dt.date).size().reindex(days, fill_value=0)
        thin = counts[counts < 92]
        if not thin.empty:
            problems.append('15-min energy incomplete on ' + ', '.join(f'{d:%a %-d}' for d in thin.index))
        core = ['production_wh', 'consumption_wh', 'grid_import_wh', 'grid_export_wh']
        gaps = e[e[core].isna().any(axis=1)]
        if not gaps.empty:
            problems.append(f'{len(gaps)} 15-min intervals with missing values')
    p = read('pool_history.csv', low_memory=False)
    if p is not None:
        c = pd.to_datetime(p.timestamp).dt.date.value_counts().reindex(days, fill_value=0)
        thin = c[c < 250]   # 288 expected at 5-minute polling
        if not thin.empty:
            problems.append('pool/weather log thin on ' + ', '.join(f'{d:%a %-d} ({n})' for d, n in thin.items()))
    missing_ha = [d for d in days if not os.path.exists(os.path.join('ha', f'{d:%Y-%m}', f'{d:%Y-%m-%d}.csv.gz'))]
    if missing_ha:
        problems.append('Home Assistant export missing for ' + ', '.join(f'{d:%a %-d}' for d in missing_ha))
    return lines + (['All sources complete.'] if not problems else [f'- {x}' for x in problems])


def build(start, end):
    e = fifteen_min(start, end)
    parts = [[f"# Week of {start:%B %-d}, {start:%Y}",
              f"{start:%a %b %-d} to {end:%a %b %-d}. Written automatically by weekly_report.py; "
              "figures come from the files in this repo."],
             solar_section(start, end, e), battery_section(start, end), car_section(start, end, e),
             pool_section(start, end), grid_section(e), data_section(start, end, e)]
    return '\n\n'.join('\n'.join(p) for p in parts) + '\n'


def main():
    day = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else None
    start, end = week_bounds(day)
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f'{start.isoformat()}.md')
    text = build(start, end)
    with open(path, 'w') as f:
        f.write(text)
    with open(os.path.join(OUT_DIR, 'latest.md'), 'w') as f:
        f.write(text)
    print(f"Wrote {path}")


if __name__ == '__main__':
    main()
