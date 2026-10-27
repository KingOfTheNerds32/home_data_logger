"""
Enphase API probe (read-only): tries every candidate endpoint once to learn
which ones this app's plan allows and what shape of data each returns.

- Responses go to probe_results/ in the private data repo (cwd), with
  personal fields (name, address, ...) removed and identifiers masked.
- The public run summary (probe_summary.md) lists only status codes, field
  names and interval counts/spans, never values or identifiers.

Usage (from the data repo checkout): python ../investigation/enphase/probe.py [ev|config]
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
import energy_logger as E  # noqa: E402

OUT_DIR = 'probe_results'


# Personal fields dropped before anything is saved, and identifiers masked.
PERSONAL_KEYS = {'name', 'public_name', 'address', 'owner', 'email', 'phone', 'nmi', 'reference'}
IDENTIFIER_KEYS = {'serial_number', 'id', 'system_id'}


def strip_personal(obj):
    if isinstance(obj, dict):
        return {k: ('<masked>' if k in IDENTIFIER_KEYS else strip_personal(v))
                for k, v in obj.items() if k not in PERSONAL_KEYS}
    if isinstance(obj, list):
        return [strip_personal(v) for v in obj]
    return E.redact(obj) if isinstance(obj, str) else obj


def schema(obj, depth=0):
    """Describe JSON structure without values: keys, types, list lengths."""
    if depth > 4:
        return '...'
    if isinstance(obj, dict):
        return {k: schema(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        return [f"list[{len(obj)}]", schema(obj[0], depth + 1)] if obj else 'list[0]'
    return type(obj).__name__


def interval_span(payload):
    """Count and time span of telemetry intervals, if present."""
    ivs = E.intervals(payload) if isinstance(payload, dict) else []
    ends = [i.get('end_at') for i in ivs if isinstance(i, dict) and i.get('end_at')]
    if not ends:
        return ''
    fmt = lambda t: datetime.fromtimestamp(t, E.PACIFIC).strftime('%Y-%m-%d %H:%M')
    step = f", step {(ends[1] - ends[0]) // 60} min" if len(ends) > 1 else ''
    return f"{len(ends)} intervals, {fmt(min(ends))} to {fmt(max(ends))}{step}"


def lifetime_span(payload):
    """Length of a lifetime-style daily array, if present."""
    if not isinstance(payload, dict):
        return ''
    for key, value in payload.items():
        if isinstance(value, list) and value and isinstance(value[0], (int, float)):
            return f"{key}: {len(value)} daily values from {payload.get('start_date', '?')}"
    return ''


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    system_id = os.environ['ENPHASE_SYSTEM_ID']
    E.secrets_api()
    tokens = E.request_tokens({'grant_type': 'refresh_token', 'refresh_token': os.environ['ENPHASE_REFRESH_TOKEN']})
    E.save_secret('ENPHASE_REFRESH_TOKEN', tokens['refresh_token'])
    access = tokens['access_token']
    api_key = E.credentials()[0]

    today = datetime.now(E.PACIFIC).replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = int((today - timedelta(days=7)).timestamp())
    yesterday = int((today - timedelta(days=1)).timestamp())
    s = f"/systems/{system_id}"

    candidates = [
        ('system', s, None),
        ('summary', f"{s}/summary", None),
        ('devices', f"{s}/devices", None),
        ('latest_telemetry', f"{s}/latest_telemetry", None),
        ('production_meter_week', f"{s}/telemetry/production_meter", {'granularity': 'week', 'start_at': week_ago}),
        ('consumption_meter_week', f"{s}/telemetry/consumption_meter", {'granularity': 'week', 'start_at': week_ago}),
        ('battery_week', f"{s}/telemetry/battery", {'granularity': 'week', 'start_at': week_ago}),
        ('import_week', f"{s}/energy_import_telemetry", {'granularity': 'week', 'start_at': week_ago}),
        ('export_week', f"{s}/energy_export_telemetry", {'granularity': 'week', 'start_at': week_ago}),
        ('production_micro_day', f"{s}/telemetry/production_micro", {'granularity': 'day', 'start_at': yesterday}),
        ('energy_lifetime', f"{s}/energy_lifetime", None),
        ('consumption_lifetime', f"{s}/consumption_lifetime", None),
        ('battery_lifetime', f"{s}/battery_lifetime", None),
        ('import_lifetime', f"{s}/energy_import_lifetime", None),
        ('export_lifetime', f"{s}/energy_export_lifetime", None),
        ('ev_charger_devices', f"{s}/ev_charger/devices", None),
        # System configuration (battery profile, reserve, Storm Guard).
        ('config_battery_settings', f"/systems/config/{system_id}/battery_settings", None),
        ('config_storm_guard', f"/systems/config/{system_id}/storm_guard", None),
        ('config_grid_status', f"/systems/config/{system_id}/grid_status", None),
        ('config_load_control', f"/systems/config/{system_id}/load_control", None),
    ]

    results = []

    def probe(name, path, params, shown_path=None):
        r = requests.get(f"{E.API_BASE}{path}", params={'key': api_key, **(params or {})},
                         headers={'Authorization': f"Bearer {access}"}, timeout=60)
        time.sleep(E.REQUEST_SPACING_SECONDS)
        try:
            body = r.json()
        except ValueError:
            body = {'_non_json': r.text[:500]}
        with open(os.path.join(OUT_DIR, f"{name}.json"), 'w') as f:
            json.dump({'path': E.redact(path), 'params': params, 'status': r.status_code,
                       'body': strip_personal(body)}, f, indent=2)
        detail = interval_span(body) or lifetime_span(body) if r.status_code == 200 else ''
        results.append((name, shown_path or path.replace(system_id, '{id}'), r.status_code, detail, schema(body)))
        return body if r.status_code == 200 else None

    # "python probe.py ev" re-runs only the EV charger calls (~4 requests).
    # "python probe.py config" runs only the system configuration calls (~4).
    if 'ev' in sys.argv[1:]:
        candidates = [c for c in candidates if c[0] == 'ev_charger_devices']
    elif 'config' in sys.argv[1:]:
        candidates = [c for c in candidates if c[0].startswith('config_')]

    for name, path, params in candidates:
        body = probe(name, path, params)
        # EV charger detail endpoints need the charger's serial number.
        if name == 'ev_charger_devices' and body:
            devices = body.get('devices') or {}
            chargers = devices.get('ev_chargers', []) if isinstance(devices, dict) else devices
            serial = chargers[0].get('serial_number') if chargers and isinstance(chargers[0], dict) else None
            if serial:
                for sub, params in [('sessions', None), ('lifetime', None),
                                    ('telemetry', {'granularity': 'week', 'start_at': week_ago})]:
                    probe(f"ev_charger_{sub}", f"{s}/ev_charger/{serial}/{sub}", params,
                          shown_path=f"/systems/{{id}}/ev_charger/{{serial}}/{sub}")

    with open('probe_summary.md', 'w') as f:
        f.write("## Enphase API Probe\n\n| Endpoint | Status | Data |\n| :--- | :--- | :--- |\n")
        for name, path, status, detail, _ in results:
            f.write(f"| `{path}` | {'✅' if status == 200 else '❌'} {status} | {detail} |\n")
        f.write("\n### Response structure (field names only)\n")
        for name, _, status, _, struct in results:
            if status == 200:
                f.write(f"\n**{name}**\n```json\n{json.dumps(struct, indent=1)[:3000]}\n```\n")
    print(f"Probed {len(results)} endpoints")


if __name__ == "__main__":
    main()
