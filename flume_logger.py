"""
Flume water logger: gallons used each minute, from Flume's cloud API.

Minute buckets are the finest Flume's API offers. Each daily run:
1. re-fetches yesterday and today (so a partly-uploaded day gets completed),
2. then spends a small request budget backfilling older days, going back until
   it reaches days before the Flume was installed (no usage at all).

Writes water/YYYY-MM.csv (columns: timestamp, gallons; timestamp is the start
of the minute, local time) and keeps progress in flume_state.json. Prints only
counts and dates, never values, because run logs on the public repo are
visible to anyone.

Needs FLUME_CLIENT_ID, FLUME_CLIENT_SECRET (Flume portal -> Settings -> API
access), FLUME_USERNAME and FLUME_PASSWORD; without them it skips quietly.

Usage (from the data repo checkout): python ../flume_logger.py
"""
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests

API = 'https://api.flumetech.com'
FOLDER = 'water'
STATE = 'flume_state.json'
CHUNK_HOURS = 6          # 360 minutes per query
BACKFILL_QUERIES = 48    # older days per run (12 days at 4 queries a day)
PAUSE = 2                # seconds between queries
EMPTY_DAYS_TO_STOP = 7   # a week in a row with no usage at all = before install


def token():
    r = requests.post(f'{API}/oauth/token', timeout=30, json={
        'grant_type': 'password',
        'client_id': os.environ['FLUME_CLIENT_ID'],
        'client_secret': os.environ['FLUME_CLIENT_SECRET'],
        'username': os.environ['FLUME_USERNAME'],
        'password': os.environ['FLUME_PASSWORD']})
    r.raise_for_status()
    access = r.json()['data'][0]['access_token']
    payload = access.split('.')[1]
    claims = json.loads(__import__('base64').urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
    return access, claims['user_id']


def sensor(access, user_id):
    """The Flume sensor (type 2, not the bridge) and its time zone."""
    r = requests.get(f'{API}/users/{user_id}/devices', timeout=30,
                     headers={'Authorization': f'Bearer {access}'}, params={'location': 'true'})
    r.raise_for_status()
    devices = r.json()['data']
    dev = next((d for d in devices if d.get('type') == 2), devices[0])
    tz = (dev.get('location') or {}).get('tz') or 'America/Los_Angeles'
    return dev['id'], tz


def minutes(access, user_id, device_id, start, end):
    """Gallons per minute from start (inclusive) to end (exclusive), naive local datetimes."""
    q = {'queries': [{'request_id': 'm', 'bucket': 'MIN', 'operation': 'SUM', 'units': 'GALLONS',
                      'since_datetime': start.strftime('%Y-%m-%d %H:%M:%S'),
                      'until_datetime': (end - timedelta(minutes=1)).strftime('%Y-%m-%d %H:%M:%S')}]}
    r = requests.post(f'{API}/users/{user_id}/devices/{device_id}/query', json=q, timeout=60,
                      headers={'Authorization': f'Bearer {access}'})
    r.raise_for_status()
    rows = r.json()['data'][0].get('m') or []
    return pd.DataFrame([{'timestamp': x['datetime'][:19], 'gallons': x['value']} for x in rows],
                        columns=['timestamp', 'gallons'])


def fetch_day(day, fetch):
    parts = []
    for h in range(0, 24, CHUNK_HOURS):
        start = datetime.combine(day, datetime.min.time()) + timedelta(hours=h)
        parts.append(fetch(start, start + timedelta(hours=CHUNK_HOURS)))
        time.sleep(PAUSE)
    return pd.concat(parts, ignore_index=True)


def save(rows):
    """Merge rows into the monthly files; new values replace stored ones for the same minute."""
    if rows.empty:
        return 0
    os.makedirs(FOLDER, exist_ok=True)
    for month, part in rows.groupby(rows.timestamp.str[:7]):
        path = os.path.join(FOLDER, f'{month}.csv')
        if os.path.exists(path):
            part = pd.concat([pd.read_csv(path), part])
        part = part.drop_duplicates('timestamp', keep='last').sort_values('timestamp')
        part.to_csv(path, index=False)
    return len(rows)


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def run(fetch, today, state):
    """Recent days, then backfill within the budget. Returns the updated state."""
    for day in (today - timedelta(days=1), today):
        n = save(fetch_day(day, fetch))
        print(f'{day}: {n} minutes')
    oldest = date.fromisoformat(state.get('oldest', str(today - timedelta(days=1))))
    empty = state.get('empty_streak', 0)
    pending = []          # zero-use days kept back until we know they're after install
    budget = BACKFILL_QUERIES
    while not state.get('backfill_done') and budget >= 24 // CHUNK_HOURS:
        day = oldest - timedelta(days=1)
        rows = fetch_day(day, fetch)
        budget -= 24 // CHUNK_HOURS
        used = not rows.empty and rows.gallons.fillna(0).sum() > 0
        if used:
            for p in pending:
                save(p)
            pending, empty = [], 0
            save(rows)
        else:
            pending.append(rows)
            empty += 1
            if empty >= EMPTY_DAYS_TO_STOP:
                state['backfill_done'] = True
                pending = []
        oldest = day
        print(f'backfill {day}: {"usage" if used else "none"}')
    # Zero-use days not yet resolved are fetched again next run (oldest stays before them).
    if pending:
        oldest = oldest + timedelta(days=len(pending))
        empty -= len(pending)
    state.update(oldest=str(oldest), empty_streak=empty)
    return state


def main():
    if not all(os.environ.get(k) for k in ('FLUME_CLIENT_ID', 'FLUME_CLIENT_SECRET', 'FLUME_USERNAME', 'FLUME_PASSWORD')):
        print('Flume: no API credentials set; skipping')
        return
    access, user_id = token()
    device_id, tz = sensor(access, user_id)
    today = datetime.now(ZoneInfo(tz)).date()
    state = run(lambda s, e: minutes(access, user_id, device_id, s, e), today, load_state())
    with open(STATE, 'w') as f:
        json.dump(state, f, indent=1)
    print(f"Flume: history back to {state['oldest']}{' (complete)' if state.get('backfill_done') else ''}")


if __name__ == '__main__':
    sys.exit(main())
