from datetime import date

import watchdog


def test_newest_timestamp_reads_the_latest_row(tmp_path):
    path = tmp_path / 'pool_history.csv'
    path.write_text("timestamp,pool_temp\n2026-10-30 10:00:00,80\n2026-10-30 10:05:00,81\n")
    assert watchdog.newest_timestamp(str(path), 'timestamp').strftime('%H:%M') == '10:05'


def test_home_assistant_export_freshness(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    line, problem = watchdog.check_home_assistant(date(2026, 10, 30))
    assert problem is None and 'no days uploaded' in line   # pending until the first upload

    (tmp_path / 'ha' / '2026-10').mkdir(parents=True)
    (tmp_path / 'ha' / '2026-10' / '2026-10-28.csv.gz').write_bytes(b'')
    assert watchdog.check_home_assistant(date(2026, 10, 30))[1] is None      # one missed night is fine
    assert watchdog.check_home_assistant(date(2026, 10, 31))[1] is not None  # three days behind
