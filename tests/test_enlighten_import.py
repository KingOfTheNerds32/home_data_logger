import pandas as pd

import enlighten_import as ei


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text)
    return str(path)


def test_duration_and_numbers():
    assert ei.duration_s('13 hr 20 min') == 13 * 3600 + 20 * 60
    assert ei.duration_s('39 min 19 sec') == 39 * 60 + 19
    assert list(ei.number(pd.Series(['1,025', '7']))) == [1025, 7]


def test_production_keeps_both_measurements_and_the_repeated_hour(tmp_path):
    a = _write(tmp_path, 'a.csv', 'Date/Time,Power Delivered (W)\n'
               '11/05/2023 01:05 AM PDT,0\n11/05/2023 01:05 AM PST,0\n11/05/2023 12:00 PM PST,"1,500"\n')
    b = _write(tmp_path, 'b.csv', 'Date/Time,Average Real Power (W)\n11/05/2023 12:00 PM PST,"1,540"\n')
    p = ei.read_production([a, b])
    noon = p[p.interval_end == '2023-11-05 12:00:00'].iloc[0]
    assert (noon.meter_w, noon.inverter_w) == (1500, 1540)
    assert sorted(p[p.interval_end == '2023-11-05 01:05:00'].tz) == ['PDT', 'PST']


def test_battery_skips_the_device_list_and_marks_the_fall_back_hour(tmp_path):
    text = ('IQ Battery 1,122239082170\n\nBattery Serial #,Created Date,Power (kW),Energy (kWh),State of Charge (%)\n'
            '122239082170,2023/11/05 01:00:00,0.5,0.0,50\n122239082170,2023/11/05 01:00:00,0.4,0.0,51\n'
            '122239082170,2023/11/05 02:00:00,0.3,0.0,52\n')
    b = ei.read_battery([_write(tmp_path, 'b.csv', text)])
    assert list(b.tz) == ['PDT', 'PST', 'PST']


def test_panels_unit_is_decided_by_the_production_meter(tmp_path):
    text = ('"date","Serial Number","Energy Production (kWh)"\n'
            '"2026-05-01","1","2.00"\n"2026-05-01","Total","2.00"\n'          # a kWh day
            '"2026-05-02","1","300"\n"2026-05-02","Total","300"\n'            # a Wh day (low production)
            '"2026-05-03","1","0.00"\n"2026-05-03","Total","0.00"\n')         # all zeros, but the meter saw sun
    meter = pd.Series({'2026-05-01': 1950.0, '2026-05-02': 290.0, '2026-05-03': 40000.0})
    p = ei.read_panels([_write(tmp_path, 'p.csv', text)], meter).set_index('date').wh
    assert p['2026-05-01'] == 2000 and p['2026-05-02'] == 300 and pd.isna(p['2026-05-03'])


def test_ev_merge_keeps_api_sessions_and_flags_the_phantom_year(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pd.DataFrame({'start_time': ['2025-09-01 18:00:00'], 'end_time': ['2025-09-02 07:00:00'], 'duration_s': [1],
                  'charge_time_s': [1], 'energy_kwh': [10.0], 'miles_added': [30.0], 'cost': [2.0]}).to_csv('ev_sessions.csv', index=False)
    export = _write(tmp_path, 's.csv', '"Charger Name","Serial Number","Start Date/Time","End Date/Time",'
                    '"Session Duration","Active Charge Time","Energy consumed (Wh)"\n'
                    '"C","1","2025/09/01 18:00:00","2025/09/02 07:00:00","13 hr","2 hr","10,000"\n'
                    '"C","1","2024/07/01 18:00:00","2024/07/02 07:00:00","13 hr","2 hr","5,000"\n')
    daily = _write(tmp_path, 'd.csv', '"Note: Values indicate daily energy consumption in Wh"\n'
                   '"Date","Total","IQ EV Charger_6241"\n"2025/09/01","16912.00","16912.00"\n"2024/07/01","5000.00","5000.00"\n')
    ei.merge_ev(*ei.read_ev([export, daily]))
    s = pd.read_csv('ev_sessions.csv').set_index('start_time')
    assert s.loc['2025-09-01 18:00:00', 'miles_added'] == 30.0   # the API copy wins
    assert len(s) == 2
    d = pd.read_csv('ev_daily.csv').set_index('date')
    assert d.loc['2025-09-01', 'phantom'] == 1 and d.loc['2024-07-01', 'phantom'] == 0
    assert d.loc['2025-09-01', 'session_wh'] == 10000
