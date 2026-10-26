"""
Heat pump investigation probe (read-only, separate from the main logger).

Captures the raw, unfiltered iAquaLink responses for one labeled scenario so
we can diff snapshots and find which field tracks the heat pump. Only read
requests are made; nothing is ever sent that changes equipment state.

Usage: python probe.py <scenario_label>
"""
import asyncio
import json
import os
import sys
from datetime import datetime

import httpx
import pytz
from iaqualink.client import AqualinkClient

# Snapshots go to the private data repo in CI (SNAPSHOT_DIR); local default otherwise.
SNAPSHOT_DIR = os.environ.get('SNAPSHOT_DIR') or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'snapshots')

# Read-only session commands. get_home / get_devices are what the library
# parses (minus the nested fields it drops); get_onetouch is captured too.
READ_COMMANDS = ['get_home', 'get_devices', 'get_onetouch']

# Newer Zodiac devices expose a "shadow" with full device state.
SHADOW_URL = "https://prod.zodiac-io.com/devices/v1/{serial}/shadow"

# Keys whose values are redacted before anything is written to the repo.
SENSITIVE_KEYS = {'serial', 'serial_number', 'sessionid', 'session_id', 'authentication_token',
                  'token', 'id_token', 'idtoken', 'email', 'username', 'password', 'user_id', 'id'}


def redact(obj, serial):
    if isinstance(obj, dict):
        return {k: ('<redacted>' if k.lower() in SENSITIVE_KEYS else redact(v, serial)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v, serial) for v in obj]
    if isinstance(obj, str) and serial and serial in obj:
        return obj.replace(serial, '<serial>')
    return obj


def flatten(obj, prefix=''):
    """Flatten nested JSON into {'a.b[0].c': value} for diffing."""
    items = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            items.update(flatten(v, f"{prefix}.{k}" if prefix else k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            items.update(flatten(v, f"{prefix}[{i}]"))
    else:
        items[prefix] = obj
    return items


def response_body(response):
    try:
        return response.json()
    except ValueError:
        return {'_non_json_body': response.text[:2000]}


async def capture():
    async with AqualinkClient(os.environ['IQUALINK_USER'], os.environ['IQUALINK_PASS']) as client:
        client._client.timeout = httpx.Timeout(60.0, connect=60.0)
        systems = await client.get_systems()
        if not systems:
            raise RuntimeError("No iAquaLink systems found on account")
        system = list(systems.values())[0]

        captured = {'system_info': system.data}
        for command in READ_COMMANDS:
            try:
                captured[command] = response_body(await system._send_session_request(command))
            except Exception as e:
                captured[command] = {'_error': str(e)}

        try:
            r = await client._client.get(SHADOW_URL.format(serial=system.serial),
                                         headers={'Authorization': client.id_token})
            captured['shadow'] = {'_status': r.status_code, 'body': response_body(r)}
        except Exception as e:
            captured['shadow'] = {'_error': str(e)}

        return redact(captured, system.serial)


def previous_snapshot():
    files = sorted(f for f in os.listdir(SNAPSHOT_DIR) if f.endswith('.json'))
    if not files:
        return None, None
    with open(os.path.join(SNAPSHOT_DIR, files[-1])) as f:
        return files[-1], json.load(f)


def diff_report(prev_name, prev, current):
    if prev is None:
        return "First snapshot - nothing to compare against.\n"
    old, new = flatten(prev['data']), flatten(current['data'])
    changed = [(k, old.get(k, '<missing>'), new.get(k, '<missing>'))
               for k in sorted(set(old) | set(new)) if old.get(k) != new.get(k)]
    lines = [f"Changes vs `{prev_name}` ({len(changed)} fields):\n",
             "| Field | Before | After |", "| :--- | :--- | :--- |"]
    lines += [f"| `{k}` | `{a}` | `{b}` |" for k, a, b in changed]
    return "\n".join(lines) + "\n"


async def main():
    scenario = sys.argv[1] if len(sys.argv) > 1 else 'unlabeled'
    safe_label = ''.join(c if c.isalnum() or c in '-_' else '_' for c in scenario)
    ts = datetime.now(pytz.timezone('America/Los_Angeles'))

    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    prev_name, prev = previous_snapshot()

    snapshot = {'timestamp': ts.strftime("%Y-%m-%d %H:%M:%S"), 'scenario': scenario, 'data': await capture()}
    out_name = f"{ts.strftime('%Y%m%d_%H%M%S')}_{safe_label}.json"
    with open(os.path.join(SNAPSHOT_DIR, out_name), 'w') as f:
        json.dump(snapshot, f, indent=2, default=str)

    with open('probe_summary.md', 'w') as f:
        f.write(f"## Heat Pump Probe: `{scenario}`\n**Saved:** `{out_name}`\n\n")
        f.write(diff_report(prev_name, prev, snapshot))
    print(f"Saved snapshot {out_name}")


if __name__ == "__main__":
    asyncio.run(main())
