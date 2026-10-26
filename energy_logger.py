"""
Enphase energy logger: pulls 15-minute solar production, home consumption,
battery and grid telemetry from the Enphase v4 cloud API.

Runs inside the private data repo checkout. Enphase rotates the refresh token
on every use, so the new one is written back to the ENPHASE_REFRESH_TOKEN
GitHub secret (via GH_SECRETS_TOKEN) each run. Tokens never touch git.

Usage:
    python energy_logger.py authorize <code>   # one-time setup
    python energy_logger.py                    # daily pull (backfills gaps)
"""
import base64
import os
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
PACIFIC = pytz.timezone('America/Los_Angeles')

# How far back to fill gaps if runs were missed. Each day costs ~5 requests.
MAX_BACKFILL_DAYS = 7
# The free plan allows 10 requests per minute.
REQUEST_SPACING_SECONDS = 7


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
        raise RuntimeError(f"Enphase token request failed ({r.status_code}): {r.text[:200]}")
    tokens = r.json()
    tokens['obtained_at'] = int(time.time())
    return tokens


def save_secret(name, value):
    """Create or update a GitHub Actions secret on this repository."""
    repo = os.environ.get('GITHUB_REPOSITORY')
    gh_token = os.environ.get('GH_SECRETS_TOKEN')
    if not repo or not gh_token:
        raise RuntimeError("Missing GITHUB_REPOSITORY or GH_SECRETS_TOKEN; cannot save Enphase token")
    headers = {'Authorization': f"Bearer {gh_token}", 'Accept': 'application/vnd.github+json'}
    api = f"https://api.github.com/repos/{repo}/actions/secrets"
    r = requests.get(f"{api}/public-key", headers=headers, timeout=30)
    r.raise_for_status()
    key = r.json()
    sealed = public.SealedBox(public.PublicKey(key['key'].encode(), encoding.Base64Encoder()))
    encrypted = base64.b64encode(sealed.encrypt(value.encode())).decode()
    r = requests.put(f"{api}/{name}", headers=headers, timeout=30,
                     json={'encrypted_value': encrypted, 'key_id': key['key_id']})
    if r.status_code not in (201, 204):
        raise RuntimeError(f"Saving secret {name} failed ({r.status_code}): {r.text[:200]}")


def api_get(path, access_token, params=None):
    api_key, _, _ = credentials()
    r = requests.get(f"{API_BASE}{path}", params={'key': api_key, **(params or {})},
                     headers={'Authorization': f"Bearer {access_token}"}, timeout=60)
    time.sleep(REQUEST_SPACING_SECONDS)
    if r.status_code != 200:
        raise RuntimeError(f"Enphase API error on {path} ({r.status_code}): {r.text[:200]}")
    return r.json()


def authorize(code):
    """Exchange the one-time code from the Enphase approval page for tokens."""
    tokens = request_tokens({'grant_type': 'authorization_code', 'redirect_uri': REDIRECT_URI, 'code': code})
    systems = api_get('/systems', tokens['access_token']).get('systems', [])
    if not systems:
        raise RuntimeError("Authorized, but no Enphase systems are visible to this account")
    save_secret('ENPHASE_REFRESH_TOKEN', tokens['refresh_token'])
    save_secret('ENPHASE_SYSTEM_ID', str(systems[0]['system_id']))
    print(f"Authorized. Found {len(systems)} system(s); using the first one.")


def intervals(payload):
    """Telemetry intervals, flattening the nested lists some endpoints use."""
    out = []
    for item in payload.get('intervals', []):
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


def fetch_day(system_id, access_token, day_start):
    """All telemetry for one local day, keyed by interval end (epoch seconds)."""
    params = {'granularity': 'day', 'start_at': int(day_start.timestamp())}
    base = f"/systems/{system_id}"
    rows = {}

    def merge(endpoint, columns):
        for interval in intervals(api_get(f"{base}/{endpoint}", access_token, params)):
            row = rows.setdefault(interval['end_at'], {})
            for column, keys in columns.items():
                row[column] = first_value(interval, *keys)

    merge('telemetry/production_meter', {'production_wh': ['wh_del']})
    merge('telemetry/consumption_meter', {'consumption_wh': ['enwh']})
    merge('telemetry/battery', {'battery_charge_wh': ['charge.enwh'],
                                'battery_discharge_wh': ['discharge.enwh'],
                                'battery_soc_pct': ['soc.percent']})
    merge('energy_import_telemetry', {'grid_import_wh': ['wh_imported']})
    merge('energy_export_telemetry', {'grid_export_wh': ['wh_exported']})
    return rows


def harvest_energy_data():
    refresh_token = os.environ.get('ENPHASE_REFRESH_TOKEN')
    system_id = os.environ.get('ENPHASE_SYSTEM_ID')
    if not refresh_token or not system_id:
        # Never set up yet: skip quietly rather than failing every night.
        # (An expired or revoked token is a real failure and still raises.)
        print("Enphase not authorized yet - run the Enphase Authorize workflow. Skipping.")
        return

    # Refresh every run: it keeps the refresh token from expiring, and the new
    # one is saved immediately so a later failure can't lose it.
    tokens = request_tokens({'grant_type': 'refresh_token', 'refresh_token': refresh_token})
    save_secret('ENPHASE_REFRESH_TOKEN', tokens['refresh_token'])

    existing = pd.read_csv(DATA_FILE) if os.path.exists(DATA_FILE) else pd.DataFrame()
    today = datetime.now(PACIFIC).replace(hour=0, minute=0, second=0, microsecond=0)
    if existing.empty:
        start = today - timedelta(days=1)
    else:
        last = PACIFIC.localize(datetime.strptime(existing['interval_end'].max(), "%Y-%m-%d %H:%M:%S"))
        start = max(last.replace(hour=0, minute=0, second=0, microsecond=0),
                    today - timedelta(days=MAX_BACKFILL_DAYS))

    # Only complete days are pulled, so every stored day is final.
    rows = {}
    day = start
    while day < today:
        rows.update(fetch_day(system_id, tokens['access_token'], day))
        day = PACIFIC.normalize(day + timedelta(days=1))

    if not rows:
        print("Energy data already up to date.")
        return

    new = pd.DataFrame([{'interval_end': datetime.fromtimestamp(end, PACIFIC).strftime("%Y-%m-%d %H:%M:%S"), **values}
                        for end, values in sorted(rows.items())])
    combined = pd.concat([existing, new]).drop_duplicates('interval_end', keep='last').sort_values('interval_end')
    combined.to_csv(DATA_FILE, index=False)
    print(f"Logged {len(new)} energy intervals from {new['interval_end'].min()} to {new['interval_end'].max()}")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == 'authorize':
        authorize(sys.argv[2])
    else:
        harvest_energy_data()
