from datetime import date, timedelta

import pandas as pd

import sunlight_logger as sl


class FakeResponse:
    def __init__(self, days):
        self.days = days

    def raise_for_status(self):
        pass

    def json(self):
        n = len(self.days)
        return {'daily': {'time': self.days, 'shortwave_radiation_sum': [18.0] * (n - 1) + [None],
                          'cloud_cover_mean': [10] * n, 'temperature_2m_max': [70.0] * n,
                          'temperature_2m_min': [55.0] * n, 'precipitation_sum': [0.0] * n,
                          'sunshine_duration': [36000] * n}}


def test_fetches_whats_missing_and_skips_days_not_yet_in_the_archive(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('LAT', '0')
    monkeypatch.setenv('LONG', '0')
    calls = []

    def fake_get(url, timeout, params):
        calls.append(params)
        start, end = date.fromisoformat(params['start_date']), date.fromisoformat(params['end_date'])
        return FakeResponse([(start + timedelta(days=n)).isoformat() for n in range((end - start).days + 1)])

    monkeypatch.setattr(sl.requests, 'get', fake_get)
    sl.main()
    d = pd.read_csv(sl.FILE)
    assert calls[0]['start_date'] == sl.INSTALL_DATE.isoformat()
    assert d.date.min() == sl.INSTALL_DATE.isoformat()
    assert d.sun_kwh_m2.iloc[0] == 5.0                                   # 18 MJ/m2 = 5 kWh/m2
    assert d.date.max() == (date.today() - timedelta(days=2)).isoformat()  # yesterday not in the archive yet

    sl.main()   # a second run only refreshes the recent days
    assert date.fromisoformat(calls[1]['start_date']) >= date.today() - timedelta(days=sl.REFRESH_DAYS + 1)
