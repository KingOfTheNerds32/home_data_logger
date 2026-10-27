"""
Enphase energy logger: pulls solar, home consumption, battery, grid and EV
charger data from the Enphase v4 cloud API into the private data repo.

Files written (in the data repo checkout, the working directory):
  energy_history.csv   15-min production, consumption, battery, grid, EV charging
  production_5min.csv  5-min solar production (going forward only)
  ev_sessions.csv      EV charging sessions with energy, miles and cost
  energy_daily.csv     daily totals since the system was installed
  battery_days.csv     inferred daily battery mode (normal / storm_hold)
  enphase_state.json   request budget and backfill progress

The free plan allows 1,000 requests a month, so requests are counted and the
history backfill (a week per request, newest first) only uses what the daily
routine leaves spare.

Runs inside the private data repo checkout. Enphase rotates the refresh token
on every use, so the new one is written back to the ENPHASE_REFRESH_TOKEN
GitHub secret (via GH_SECRETS_TOKEN) each run. Tokens never touch git.

Usage:
    python energy_logger.py authorize <code>   # one-time setup
    python energy_logger.py                    # daily pull + history backfill
"""
import base64
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta

import pandas as pd
import pytz
import requests
from nacl import encoding, public

API_BASE = "https://api.enphaseenergy.com/api/v4"
TOKEN_URL = "https://api.enphaseenergy.com/oauth/token"
REDIRECT_URI = "https://api.enphaseenergy.com/oauth/redirect_uri"

DATA_FILE = 'energy_history.csv'
MICRO_FILE = 'production_5min.csv'
SESSIONS_FILE = 'ev_sessions.csv'
DAILY_FILE = 'energy_daily.csv'
STATE_FILE = 'enphase_state.json'
BATTERY_DAYS_FILE = 'battery_days.csv'
PACIFIC = pytz.timezone('America/Los_Angeles')
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# How far back the daily pull fills gaps if runs were missed.
MAX_CATCHUP_DAYS = 7
# The free plan allows 10 requests per minute and 1,000 per month. The daily
# routine uses ~8 a day; the backfill stops once the month reaches the budget.
REQUEST_SPACING_SECONDS = 7
MONTHLY_BUDGET = 850
BACKFILL_REQUESTS_PER_RUN = 20

# 15-minute telemetry endpoints and the columns each provides.
TELEMETRY = [
    ('telemetry/production_meter', {'production_wh': ['wh_del']}),
    ('telemetry/consumption_meter', {'consumption_wh': ['enwh']}),
    ('telemetry/battery', {'battery_charge_wh': ['charge.enwh'],
                           'battery_discharge_wh': ['discharge.enwh'],
                           'battery_soc_pct': ['soc.percent']}),
    ('energy_import_telemetry', {'grid_import_wh': ['wh_imported']}),
    ('energy_export_telemetry', {'grid_export_wh': ['wh_exported']}),
]
EV_COLUMNS = {'ev_charging_wh': ['consumption']}

# Lifetime endpoints return one value per day since start_date.
LIFETIME = [
    ('energy_lifetime', {'production_wh': 'production'}),
    ('consumption_lifetime', {'consumption_wh': 'consumption'}),
    ('battery_lifetime', {'battery_charge_wh': 'charge', 'battery_discharge_wh': 'discharge'}),
    ('energy_import_lifetime', {'grid_import_wh': 'import'}),
    ('energy_export_lifetime', {'grid_export_wh': 'export'}),
]


def safe_error(r):
    """A short description of a failed response that's safe for public logs.

    Run logs on this public repo are visible to anyone, and Enphase error
    bodies can echo the system ID, device serials or account details, so keep
    only the error fields and mask any long numbers.
    """
    try:
        body = r.json()
        text = ' '.join(str(body.get(k)) for k in ('error', 'error_description', 'message', 'reason')
                        if isinstance(body, dict) and body.get(k))
    except ValueError:
        text = ''
    return redact(f"({r.status_code}) {text}".strip())


def redact(text):
    """Mask long numbers (system IDs, serial numbers) in text bound for logs."""
    return re.sub(r'\d{6,}', '<id>', str(text))


def credentials():
    keys = ['ENPHASE_API_KEY', 'ENPHASE_CLIENT_ID', 'ENPHASE_CLIENT_SECRET']
    missing = [k for k in keys if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"Missing Environment Variables: {', '.join(missing)}")
    return [os.environ[k] for k in keys]


def request_tokens(params):
    _, client_id, client_secret = credentials()
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    r = requests.post(TOKEN_URL, params=params, headers={'Authorization': f"Basic {basic}"}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Enphase token request failed {safe_error(r)}")
    tokens = r.json()
    tokens['obtained_at'] = int(time.time())
    return tokens


def secrets_api():
    """Headers, base URL and public key for this repo's Actions secrets."""
    repo = os.environ.get('GITHUB_REPOSITORY')
    gh_token = os.environ.get('GH_SECRETS_TOKEN')
    if not repo or not gh_token:
        raise RuntimeError("Missing GITHUB_REPOSITORY or GH_SECRETS_TOKEN; cannot save Enphase token")
    headers = {'Authorization': f"Bearer {gh_token}", 'Accept': 'application/vnd.github+json'}
    api = f"https://api.github.com/repos/{repo}/actions/secrets"
    r = requests.get(f"{api}/public-key", headers=headers, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"GH_SECRETS_TOKEN can't manage secrets on {repo} ({r.status_code}). "
                           "It needs access to this repo with 'Secrets: Read and write'.")
    return headers, api, r.json()


def save_secret(name, value):
    """Create or update a GitHub Actions secret on this repository."""
    headers, api, key = secrets_api()
    sealed = public.SealedBox(public.PublicKey(key['key'].encode(), encoding.Base64Encoder()))
    encrypted = base64.b64encode(sealed.encrypt(value.encode())).decode()
    r = requests.put(f"{api}/{name}", headers=headers, timeout=30,
                     json={'encrypted_value': encrypted, 'key_id': key['key_id']})
    if r.status_code not in (201, 204):
        raise RuntimeError(f"Saving secret {name} failed ({r.status_code})")


class Enphase:
    """API client that counts requests against the monthly budget."""

    def __init__(self, system_id, access_token, state):
        self.base = f"/systems/{system_id}"
        self.access_token = access_token
        self.state = state
        self.month = datetime.now(PACIFIC).strftime('%Y-%m')
        self.used_this_run = 0
        self.ev_serial = None

    @property
    def used_this_month(self):
        return self.state.setdefault('usage', {}).get(self.month, 0)

    def get(self, path, params=None, optional=False):
        api_key = credentials()[0]
        r = requests.get(f"{API_BASE}{self.base}{path}", params={'key': api_key, **(params or {})},
                         headers={'Authorization': f"Bearer {self.access_token}"}, timeout=60)
        self.state.setdefault('usage', {})[self.month] = self.used_this_month + 1
        self.used_this_run += 1
        time.sleep(REQUEST_SPACING_SECONDS)
        if r.status_code in (400, 401, 403, 404) and optional:
            return None
        if r.status_code != 200:
            raise RuntimeError(f"Enphase API error on {redact(path)} {safe_error(r)}")
        return r.json()


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2, sort_keys=True)


def local_midnight(dt):
    return PACIFIC.localize(datetime(dt.year, dt.month, dt.day))


def to_local(epoch):
    return datetime.fromtimestamp(epoch, PACIFIC).strftime(TIME_FORMAT)


def merge_csv(path, new_rows, key):
    """Add rows to a CSV, replacing any with the same key, sorted by key."""
    if not new_rows:
        return 0
    new = pd.DataFrame(new_rows)
    existing = pd.read_csv(path) if os.path.exists(path) else pd.DataFrame()
    combined = pd.concat([existing, new]).drop_duplicates(key, keep='last').sort_values(key)
    combined.to_csv(path, index=False)
    return len(new)


def intervals(payload):
    """Telemetry intervals, flattening the nested lists some endpoints use."""
    out = []
    for item in payload.get('intervals', []) + payload.get('consumption', []):
        out.extend(item if isinstance(item, list) else [item])
    return out


def first_value(interval, *keys):
    for key in keys:
        value = interval
        for part in key.split('.'):
            value = value.get(part) if isinstance(value, dict) else None
        if value is not None:
            return value
    return None


def fetch_telemetry(api, start, granularity, include_ev):
    """15-min telemetry for a day or week starting at local midnight `start`."""
    params = {'granularity': granularity, 'start_at': int(start.timestamp())}
    rows = {}
    sources = [(f"/{endpoint}", columns) for endpoint, columns in TELEMETRY]
    if include_ev:
        sources.append((f"/ev_charger/{api.ev_serial}/telemetry", EV_COLUMNS))
    for path, columns in sources:
        payload = api.get(path, params, optional=True)
        if payload is None:
            print(f"Skipping {redact(path)}: not available")
            continue
        for interval in intervals(payload):
            row = rows.setdefault(interval['end_at'], {'interval_end': to_local(interval['end_at'])})
            for column, keys in columns.items():
                row[column] = first_value(interval, *keys)
    return [rows[end] for end in sorted(rows)]


def find_ev_charger(api):
    """The EV charger's serial number, needed for its endpoints.

    Kept in the ENPHASE_EV_SERIAL secret rather than the data repo, so no
    device identifiers are stored with the data. Looked up once if missing;
    'none' records that there is no charger.
    """
    serial = os.environ.get('ENPHASE_EV_SERIAL')
    if not serial:
        payload = api.get('/ev_charger/devices', optional=True) or {}
        devices = payload.get('devices') or {}
        chargers = devices.get('ev_chargers', []) if isinstance(devices, dict) else devices
        serial = (chargers[0].get('serial_number') if chargers else None) or 'none'
        save_secret('ENPHASE_EV_SERIAL', serial)
    api.ev_serial = None if serial == 'none' else serial


def catch_up(api, today):
    """Pull every complete day since the last stored one (up to a week)."""
    existing = pd.read_csv(DATA_FILE) if os.path.exists(DATA_FILE) else pd.DataFrame()
    if existing.empty:
        day = today - timedelta(days=1)
    else:
        last = PACIFIC.localize(datetime.strptime(existing['interval_end'].max(), TIME_FORMAT))
        day = max(local_midnight(last), today - timedelta(days=MAX_CATCHUP_DAYS))
    rows, micro = [], []
    while day < today:
        rows += fetch_telemetry(api, day, 'day', bool(api.ev_serial))
        payload = api.get('/telemetry/production_micro',
                          {'granularity': 'day', 'start_at': int(day.timestamp())}, optional=True)
        for interval in intervals(payload or {}):
            micro.append({'interval_end': to_local(interval['end_at']),
                          'production_wh': interval.get('enwh'), 'power_w': interval.get('powr')})
        day = local_midnight(day + timedelta(days=1, hours=2))
    print(f"Daily pull: {merge_csv(DATA_FILE, rows, 'interval_end')} 15-min and "
          f"{merge_csv(MICRO_FILE, micro, 'interval_end')} 5-min intervals")


def update_sessions(api):
    """Store EV sessions; the endpoint returns the most recent 20."""
    if not api.ev_serial:
        return
    payload = api.get(f"/ev_charger/{api.ev_serial}/sessions", optional=True) or {}
    rows = [{'start_time': to_local(s['start_time']), 'end_time': to_local(s['end_time']),
             'duration_s': s.get('duration'), 'charge_time_s': s.get('charge_time'),
             'energy_kwh': s.get('energy_added'), 'miles_added': s.get('miles_added'), 'cost': s.get('cost')}
            for s in payload.get('sessions', []) if s.get('start_time')]
    print(f"EV sessions: {merge_csv(SESSIONS_FILE, rows, 'start_time')} recent sessions merged")


def update_daily_totals(api, today):
    """Daily totals since install from the lifetime endpoints, refreshed weekly."""
    if os.path.exists(DAILY_FILE):
        last = pd.read_csv(DAILY_FILE)['date'].max()
        if last >= (today - timedelta(days=7)).strftime('%Y-%m-%d'):
            return
    days = {}
    for endpoint, columns in LIFETIME:
        payload = api.get(f"/{endpoint}", optional=True)
        if not payload:
            continue
        start = datetime.strptime(payload['start_date'], '%Y-%m-%d')
        api.state['history_start'] = min(payload['start_date'], api.state.get('history_start', '9999'))
        for column, key in columns.items():
            for i, value in enumerate(payload.get(key, [])):
                date = (start + timedelta(days=i)).strftime('%Y-%m-%d')
                days.setdefault(date, {'date': date})[column] = value
    # Today's value is partial, so leave it for next time.
    rows = [row for date, row in sorted(days.items()) if date < today.strftime('%Y-%m-%d')]
    print(f"Daily totals: {merge_csv(DAILY_FILE, rows, 'date')} days since {api.state.get('history_start')}")


def backfill(api):
    """Fill 15-min history a week at a time, newest first, within the budget."""
    if not api.state.get('history_start') or not os.path.exists(DATA_FILE):
        return
    history_start = PACIFIC.localize(datetime.strptime(api.state['history_start'], '%Y-%m-%d'))
    per_week = len(TELEMETRY) + (1 if api.ev_serial and not api.state.get('ev_history_done') else 0)
    rows, weeks, run_start = [], 0, api.used_this_run
    while True:
        earliest = local_midnight(datetime.strptime(
            min([r['interval_end'] for r in rows] + [pd.read_csv(DATA_FILE)['interval_end'].min()]), TIME_FORMAT))
        if earliest <= history_start:
            print("Backfill complete: 15-min history reaches the install date")
            break
        if api.used_this_run - run_start + per_week > BACKFILL_REQUESTS_PER_RUN or \
                api.used_this_month + per_week > MONTHLY_BUDGET:
            break
        start = max(local_midnight(earliest - timedelta(days=7)), history_start)
        include_ev = per_week > len(TELEMETRY)
        week = fetch_telemetry(api, start, 'week', include_ev)
        # Before the charger was installed there's no EV data; stop asking.
        if include_ev and all(r.get('ev_charging_wh') is None for r in week):
            api.state['ev_history_done'] = True
            per_week = len(TELEMETRY)
        if not week:
            print(f"No data for week of {start.date()}; treating history as complete")
            api.state['history_start'] = start.strftime('%Y-%m-%d')
            break
        rows += week
        weeks += 1
    if rows:
        merge_csv(DATA_FILE, rows, 'interval_end')
        print(f"Backfill: {weeks} week(s), now back to {min(r['interval_end'] for r in rows)[:10]}")


def label_battery_days():
    """Infer each day's battery mode from its behavior (no API requests).

    Enphase's free plan can't read the battery settings, so classify from the
    15-min data: 'storm_hold' when the battery stays full through the evening
    instead of discharging (Storm Guard / full backup), otherwise 'normal'.
    Also records how much it charged from the grid and how much the house
    imported during the 5-8 PM peak.
    """
    if not os.path.exists(DATA_FILE):
        return
    d = pd.read_csv(DATA_FILE)
    if 'battery_soc_pct' not in d:
        return
    d['start'] = pd.to_datetime(d['interval_end']) - pd.Timedelta(minutes=15)
    d['date'] = d['start'].dt.strftime('%Y-%m-%d')
    hour = d['start'].dt.hour
    rows = []
    for date, g in d.groupby('date'):
        if len(g) < 90:
            continue  # incomplete day
        evening = g[hour[g.index] >= 17]
        peak = g[(hour[g.index] >= 17) & (hour[g.index] < 20)]
        soc_5pm = g.loc[hour[g.index] == 17, 'battery_soc_pct'].iloc[0]
        evening_discharge_kwh = evening['battery_discharge_wh'].sum() / 1000
        held = soc_5pm >= 95 and evening_discharge_kwh < 1 and evening['battery_soc_pct'].min() >= 90
        rows.append({
            'date': date,
            'mode': 'storm_hold' if held else 'normal',
            'soc_5pm': soc_5pm,
            'min_soc': g['battery_soc_pct'].min(),
            'evening_discharge_kwh': round(evening_discharge_kwh, 2),
            # Battery charging beyond what solar produced came from the grid.
            'grid_charge_kwh': round((g['battery_charge_wh'] - g['production_wh']).clip(lower=0).sum() / 1000, 2),
            'peak_import_kwh': round(peak['grid_import_wh'].sum() / 1000, 2),
        })
    pd.DataFrame(rows).to_csv(BATTERY_DAYS_FILE, index=False)
    print(f"Battery days: {len(rows)} labeled, {sum(r['mode'] == 'storm_hold' for r in rows)} storm-hold")


def harvest_energy_data():
    refresh_token = os.environ.get('ENPHASE_REFRESH_TOKEN')
    system_id = os.environ.get('ENPHASE_SYSTEM_ID')
    if not refresh_token or not system_id:
        # Never set up yet: skip quietly rather than failing every night.
        # (An expired or revoked token is a real failure and still raises.)
        print("Enphase not authorized yet - run the Enphase Authorize workflow. Skipping.")
        return

    secrets_api()

    # Refresh every run: it keeps the refresh token from expiring, and the new
    # one is saved immediately so a later failure can't lose it.
    tokens = request_tokens({'grant_type': 'refresh_token', 'refresh_token': refresh_token})
    save_secret('ENPHASE_REFRESH_TOKEN', tokens['refresh_token'])

    state = load_state()
    state.pop('ev_serial', None)  # now kept in the ENPHASE_EV_SERIAL secret
    api = Enphase(system_id, tokens['access_token'], state)
    today = local_midnight(datetime.now(PACIFIC))
    try:
        find_ev_charger(api)
        catch_up(api, today)
        update_sessions(api)
        update_daily_totals(api, today)
        backfill(api)
        label_battery_days()
    finally:
        save_state(state)
        print(f"Requests: {api.used_this_run} this run, {api.used_this_month} this month (budget {MONTHLY_BUDGET})")


def authorize(code):
    """Exchange the one-time code from the Enphase approval page for tokens."""
    # Check we can store the result first, so a setup problem doesn't use up the code.
    secrets_api()
    tokens = request_tokens({'grant_type': 'authorization_code', 'redirect_uri': REDIRECT_URI, 'code': code})
    r = requests.get(f"{API_BASE}/systems", params={'key': credentials()[0]},
                     headers={'Authorization': f"Bearer {tokens['access_token']}"}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"Enphase API error listing systems {safe_error(r)}")
    systems = r.json().get('systems', [])
    if not systems:
        raise RuntimeError("Authorized, but no Enphase systems are visible to this account")
    save_secret('ENPHASE_REFRESH_TOKEN', tokens['refresh_token'])
    save_secret('ENPHASE_SYSTEM_ID', str(systems[0]['system_id']))
    print(f"Authorized. Found {len(systems)} system(s); using the first one.")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == 'authorize':
        authorize(sys.argv[2])
    else:
        harvest_energy_data()
