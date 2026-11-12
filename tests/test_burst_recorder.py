from burst_recorder import parse_batteries, parse_meters, read_token_key


def test_parse_meters_names_by_type():
    rows = parse_meters([{'eid': 1, 'activePower': 4012.34}, {'eid': 2, 'activePower': -150.0}],
                        {1: 'production', 2: 'net-consumption'})
    assert rows == [('production', 4012.3), ('net-consumption', -150.0)]


def test_parse_batteries_handles_colon_key():
    rows = parse_batteries({'devices:': [{'serial_num': 122239082170, 'real_power_mw': -3500000, 'soc': 61}]})
    assert rows == [('battery_122239082170', -3500.0), ('battery_122239082170_soc', 61)]
    assert parse_batteries(None) == []


def test_read_token_key(tmp_path):
    p = tmp_path / 'secrets.yaml'
    p.write_text('github_home_data_token: abc\nenvoy_token: "eyJ.x.y"\n')
    assert read_token_key(str(p), 'envoy_token') == 'eyJ.x.y'
    assert read_token_key(str(tmp_path / 'missing.yaml'), 'envoy_token') is None


def test_upload_waits_for_hour_to_settle(tmp_path, monkeypatch):
    import datetime as dt
    import burst_recorder as br

    class Clock(dt.datetime):
        @classmethod
        def now(cls):
            return cls(2026, 11, 12, 9, 1, 0)       # 1 minute into the 9 AM hour

    monkeypatch.setattr(br.dt, 'datetime', Clock)
    app = br.BurstRecorder()
    app.token, app.data_dir, app.folder = 'x', str(tmp_path), 'ha/burst'
    for h in ('07', '08', '09'):
        (tmp_path / f"2026-11-12_{h}.csv").write_text('timestamp,source,value\n')
    sent = []
    monkeypatch.setattr(br.HomeExport, 'put_file', lambda self, path, content, msg: sent.append(path) or True)
    app.upload({})
    assert sent == ['ha/burst/2026-11-12/07.csv.gz']   # 8 AM hour ended < 2 min ago: waits
