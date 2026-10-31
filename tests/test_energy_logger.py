from datetime import datetime, timedelta

import pandas as pd

import energy_logger as el


def test_merge_csv_replaces_rows_with_the_same_key(tmp_path):
    path = tmp_path / 'x.csv'
    el.merge_csv(str(path), [{'k': 1, 'v': 'a'}, {'k': 2, 'v': 'b'}], 'k')
    el.merge_csv(str(path), [{'k': 2, 'v': 'B'}, {'k': 0, 'v': 'z'}], 'k')
    assert pd.read_csv(path).to_dict('list') == {'k': [0, 1, 2], 'v': ['z', 'a', 'B']}


def test_intervals_flattens_nested_lists_and_first_value_follows_paths():
    payload = {'intervals': [[{'end_at': 1}], {'end_at': 2}], 'consumption': [{'end_at': 3}]}
    assert [i['end_at'] for i in el.intervals(payload)] == [1, 2, 3]
    assert el.first_value({'charge': {'enwh': 5}}, 'missing', 'charge.enwh') == 5


class FakeApi:
    """Answers every telemetry request with one interval and counts requests."""
    def __init__(self, history_start, used_this_month=0):
        self.state = {'history_start': history_start}
        self.used_this_month = used_this_month
        self.used_this_run = 0
        self.ev_serial = None

    def get(self, path, params=None, optional=False):
        self.used_this_run += 1
        self.used_this_month += 1
        start = params['start_at']
        return {'intervals': [{'end_at': start + 900, 'wh_del': 1, 'enwh': 1}]}


def _seed(tmp_path, monkeypatch, newest='2026-06-21 00:15:00'):
    monkeypatch.chdir(tmp_path)
    pd.DataFrame({'interval_end': [newest], 'production_wh': [0]}).to_csv(el.DATA_FILE, index=False)


def test_backfill_stays_within_the_per_run_limit(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    api = FakeApi('2023-04-05')
    el.backfill(api)
    per_week = len(el.TELEMETRY)
    assert api.used_this_run == (el.BACKFILL_REQUESTS_PER_RUN // per_week) * per_week
    earliest = pd.read_csv(el.DATA_FILE).interval_end.min()
    assert earliest < '2026-06-21'


def test_backfill_stops_at_the_monthly_budget(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    api = FakeApi('2023-04-05', used_this_month=el.MONTHLY_BUDGET - 1)
    el.backfill(api)
    assert api.used_this_run == 0


def test_backfill_does_nothing_once_history_reaches_the_install_date(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, newest='2023-04-05 00:15:00')
    api = FakeApi('2023-04-05')
    el.backfill(api)
    assert api.used_this_run == 0


def test_merge_csv_keeps_stored_values_a_refetch_lacks(tmp_path):
    path = tmp_path / 'x.csv'
    el.merge_csv(str(path), [{'k': 1, 'a': 5.0, 'b': 6.0}], 'k')
    el.merge_csv(str(path), [{'k': 1, 'a': 7.0, 'b': None}], 'k')
    assert pd.read_csv(path).to_dict('records') == [{'k': 1, 'a': 7.0, 'b': 6.0}]


def test_days_to_pull_refetches_incomplete_days_and_new_ones():
    today = el.local_midnight(datetime(2026, 10, 31))
    times = pd.date_range('2026-10-28 00:15', '2026-10-30 00:00', freq='15min')
    existing = pd.DataFrame({'interval_end': times.strftime(el.TIME_FORMAT)})
    for c in el.CORE_COLUMNS:
        existing[c] = 1.0
    existing.loc[existing.interval_end.str.startswith('2026-10-29 1'), 'grid_import_wh'] = None
    days = [d.strftime('%Y-%m-%d') for d in el.days_to_pull(existing, today)]
    # Oct 28 is complete; Oct 29 lacks imports; Oct 30 was never pulled; older days in the window were never stored.
    assert '2026-10-28' not in days and '2026-10-29' in days and '2026-10-30' in days
