import pandas as pd

import monthly


def test_append_writes_one_file_per_month_with_header(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monthly.append('pool', pd.DataFrame([{'timestamp': '2026-11-30 23:55:00', 'pool_temp': 77}]), '2026-11-30 23:55:00')
    monthly.append('pool', pd.DataFrame([{'timestamp': '2026-12-01 00:00:00', 'pool_temp': 76}]), '2026-12-01 00:00:00')
    monthly.append('pool', pd.DataFrame([{'timestamp': '2026-12-01 00:05:00', 'pool_temp': 76}]), '2026-12-01 00:05:00')
    assert monthly.files('pool') == ['pool/2026-11.csv', 'pool/2026-12.csv']
    assert (tmp_path / 'pool' / '2026-12.csv').read_text().count('timestamp') == 1
    assert monthly.latest('pool') == 'pool/2026-12.csv'
    assert list(monthly.read('pool').pool_temp) == [77, 76, 76]
    assert len(monthly.read('pool', months=1)) == 2


def test_legacy_file_is_split_exactly_and_removed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    legacy = tmp_path / 'weather_history.csv'
    legacy.write_text("timestamp,temp_actual,rain_1h_mm\n"
                      "2026-10-31 23:55:00,61.5,\n"
                      "2026-11-01 00:00:00,61.0,0.25\n")
    assert monthly.latest('weather') == 'weather_history.csv'      # before migration
    assert len(monthly.read('weather')) == 2
    monthly.append('weather', pd.DataFrame([{'timestamp': '2026-11-01 00:05:00', 'temp_actual': 60.5,
                                             'rain_1h_mm': None}]), '2026-11-01 00:05:00')
    assert not legacy.exists()
    assert (tmp_path / 'weather' / '2026-10.csv').read_text() == "timestamp,temp_actual,rain_1h_mm\n2026-10-31 23:55:00,61.5,\n"
    nov = (tmp_path / 'weather' / '2026-11.csv').read_text().splitlines()
    assert nov == ['timestamp,temp_actual,rain_1h_mm', '2026-11-01 00:00:00,61.0,0.25', '2026-11-01 00:05:00,60.5,']
    assert len(monthly.read('weather')) == 3


def test_split_keeps_lines_exactly_and_puts_existing_month_rows_after(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'runs').mkdir()
    (tmp_path / 'runs' / '2026-11.csv').write_text("timestamp,pool_status,weather_status\n2026-11-12 09:05:00,OK,OK\n")
    (tmp_path / 'execution_log.csv').write_text("timestamp,pool_status,weather_status\n"
                                                "2026-11-01 01:55:00,OK,OK\n2026-11-01 01:00:00,OK,OK\n"
                                                "2026-11-01 01:00:00,OK,OK\n2026-11-12 09:00:00,OK,FAIL")
    monthly.migrate('runs')
    assert (tmp_path / 'runs' / '2026-11.csv').read_text().splitlines() == [
        'timestamp,pool_status,weather_status', '2026-11-01 01:55:00,OK,OK', '2026-11-01 01:00:00,OK,OK',
        '2026-11-01 01:00:00,OK,OK', '2026-11-12 09:00:00,OK,FAIL', '2026-11-12 09:05:00,OK,OK']
    assert not (tmp_path / 'execution_log.csv').exists()
