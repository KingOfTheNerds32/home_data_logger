from datetime import date, datetime, timedelta

import pandas as pd

import flume_logger as fl


def fake(usage_from):
    """A fetch that reports 0.5 gal/minute on days from usage_from on, zero before."""
    def fetch(start, end):
        ts = pd.date_range(start, end - timedelta(minutes=1), freq='1min')
        g = 0.5 if start.date() >= usage_from else 0.0
        return pd.DataFrame({'timestamp': ts.strftime('%Y-%m-%d %H:%M:%S'), 'gallons': g})
    return fetch


def test_recent_days_and_backfill_stop_before_install(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(fl, 'PAUSE', 0)
    monkeypatch.setattr(fl, 'BACKFILL_QUERIES', 4 * 30)
    state = fl.run(fake(date(2026, 11, 1)), date(2026, 11, 12), {})
    w = pd.concat([pd.read_csv(p) for p in sorted((tmp_path / 'water').glob('*.csv'))])
    assert w.timestamp.min() == '2026-11-01 00:00:00' and w.timestamp.max() == '2026-11-12 23:59:00'
    assert len(w) == 12 * 1440 and w.timestamp.is_unique
    assert state['backfill_done'] and (tmp_path / 'water' / '2026-11.csv').exists()
    assert not (tmp_path / 'water' / '2026-10.csv').exists()     # zero days before install not kept


def test_zero_days_after_install_are_kept_once_usage_is_found(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(fl, 'PAUSE', 0)
    monkeypatch.setattr(fl, 'BACKFILL_QUERIES', 4 * 20)
    base = fake(date(2026, 10, 1))
    away = {date(2026, 11, 5), date(2026, 11, 6)}            # trip: no water used

    def fetch(start, end):
        d = base(start, end)
        if start.date() in away:
            d['gallons'] = 0.0
        return d
    state = fl.run(fetch, date(2026, 11, 12), {})
    w = pd.read_csv(tmp_path / 'water' / '2026-11.csv')
    assert (w.timestamp.str[:10] == '2026-11-05').sum() == 1440   # zero day kept
    assert not state.get('backfill_done')


def test_budget_carries_over_between_runs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(fl, 'PAUSE', 0)
    monkeypatch.setattr(fl, 'BACKFILL_QUERIES', 4 * 3)
    s = fl.run(fake(date(2026, 1, 1)), date(2026, 11, 12), {})
    assert s['oldest'] == '2026-11-08'
    s = fl.run(fake(date(2026, 1, 1)), date(2026, 11, 13), s)
    assert s['oldest'] == '2026-11-05'
