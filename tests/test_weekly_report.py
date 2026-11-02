from datetime import date

import pandas as pd

import weekly_report as wr


def test_week_bounds_are_monday_to_sunday():
    assert wr.week_bounds(date(2026, 10, 29)) == (date(2026, 10, 26), date(2026, 11, 1))


def test_report_from_small_sample(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    t = pd.date_range('2026-10-25 12:15', '2026-11-02 00:00', freq='15min')   # from the evening before
    hour = (t - pd.Timedelta(minutes=15)).hour
    pd.DataFrame({'interval_end': t.strftime('%Y-%m-%d %H:%M:%S'), 'production_wh': 500, 'consumption_wh': 600,
                  'battery_charge_wh': [2000 if h == 23 else 0 for h in hour],
                  'battery_discharge_wh': [300 if h < 7 else 0 for h in hour], 'battery_soc_pct': 60.0,
                  'grid_import_wh': 100, 'grid_export_wh': 0,
                  'ev_charging_wh': [2000 if h in (1, 2) else 0 for h in hour]}).to_csv('energy_history.csv', index=False)
    pd.DataFrame({'start_time': ['2026-10-27 01:00:00'], 'end_time': ['2026-10-27 03:00:00'], 'energy_kwh': [16.0]}).to_csv('ev_sessions.csv', index=False)
    text = wr.build(date(2026, 10, 26), date(2026, 11, 1))
    assert 'Produced **336 kWh**' in text                    # 500 Wh x 96 x 7
    assert 'charged from the grid on **7 of 7 nights**' in text
    assert 'Charged **112 kWh** at home, in 1 sessions' in text
    assert 'Home Assistant export missing' in text           # no ha/ files in the sample
