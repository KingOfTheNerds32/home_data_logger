"""
Burst recorder (AppDaemon app): reads the Enphase gateway's local meters and
batteries every second (all day by default, or a set daily window), to see fast swings that the
1-minute export can't (e.g. grid import jumping ~3 kW every 3 s in the
morning while the battery charges).

Each second it reads, from the gateway on the home network:
- /ivp/meters/readings   instant power per meter (production, net grid)
- /ivp/ensemble/power    instant power per battery
and appends rows to a local CSV (long format: timestamp, source, value; watts,
or % for soc; signs as the gateway reports them, normally net grid
+ import and battery + discharge). After the window it uploads the day, gzip-compressed, to
home_data at ha/burst/YYYY-MM-DD.csv.gz, retrying hourly.

Read-only: it changes nothing. Needs `envoy_token: <token>` in AppDaemon's
secrets.yaml (a gateway token from entrez.enphaseenergy.com); without it the
app logs a warning and does nothing. The token is read from the file here,
not via !secret in apps.yaml, so a missing token can't break the other apps.
"""
import datetime as dt
import gzip
import json
import os
import ssl
import threading
import time
import urllib.request

import appdaemon.plugins.hass.hassapi as hass

from home_export import HomeExport


def parse_meters(readings, types):
    """[(source, watts)] from /ivp/meters/readings, named by meter type."""
    out = []
    for m in readings or []:
        name = types.get(m.get('eid'), str(m.get('eid')))
        if 'activePower' in m:
            out.append((name, round(float(m['activePower']), 1)))
    return out


def parse_batteries(payload):
    """[(source, value)] from /ivp/ensemble/power: watts (+ discharge) and soc per battery."""
    devices = (payload or {}).get('devices:') or (payload or {}).get('devices') or []
    out = []
    for b in devices:
        serial = str(b.get('serial_num', '?'))
        if 'real_power_mw' in b:
            out.append((f"battery_{serial}", round(float(b['real_power_mw']) / 1000, 1)))
        if 'soc' in b:
            out.append((f"battery_{serial}_soc", b['soc']))
    return out


class BurstRecorder(hass.Hass):

    def initialize(self):
        self.host = self.args.get('envoy_host', 'envoy.local')
        self.start = dt.time.fromisoformat(self.args.get('start', '00:00'))
        self.end = dt.time.fromisoformat(self.args.get('end', '23:59:59'))
        self.until = dt.date.fromisoformat(str(self.args['until'])) if self.args.get('until') else None
        self.repo = self.args.get('repo', 'KingOfTheNerds32/home_data')
        self.folder = self.args.get('folder', 'ha/burst')
        self.data_dir = os.path.join(os.path.dirname(self.app_dir), 'burst_recorder')
        os.makedirs(self.data_dir, exist_ok=True)
        config_dir = os.path.dirname(self.app_dir)
        self.envoy_token = read_token_key(os.path.join(config_dir, 'secrets.yaml'), 'envoy_token')
        self.token = read_token_key(os.path.join(config_dir, 'secrets.yaml'), 'github_home_data_token')
        self.stop = threading.Event()
        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False          # the gateway uses a self-signed certificate
        self.ctx.verify_mode = ssl.CERT_NONE
        if not self.envoy_token:
            self.log("No envoy_token in secrets.yaml; burst recorder idle", level='WARNING')
            return
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()
        self.run_every(self.upload, 'now+300', 3600)
        self.log(f"Burst recorder: every second {self.start}-{self.end} from {self.host}"
                 + (f" until {self.until}" if self.until else ""))

    def terminate(self):
        self.stop.set()

    def get(self, path):
        req = urllib.request.Request(f"https://{self.host}{path}",
                                     headers={'Authorization': f"Bearer {self.envoy_token}",
                                              'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=2, context=self.ctx) as r:
            return json.load(r)

    def meter_types(self):
        try:
            return {m['eid']: m.get('measurementType', str(m['eid'])) for m in self.get('/ivp/meters')}
        except Exception as e:
            self.log(f"Could not read meter list: {type(e).__name__}", level='WARNING')
            return {}

    def loop(self):
        types, errors = {}, 0
        while not self.stop.is_set():
            now = dt.datetime.now()
            if (self.until and now.date() > self.until) or not (self.start <= now.time() < self.end):
                types = {}
                self.stop.wait(30)
                continue
            if not types:
                types = self.meter_types()
            rows = []
            try:
                rows += parse_meters(self.get('/ivp/meters/readings'), types)
                rows += parse_batteries(self.get('/ivp/ensemble/power'))
                errors = 0
            except Exception as e:
                errors += 1
                if errors in (1, 60):
                    self.log(f"Gateway read failed: {type(e).__name__}", level='WARNING')
                if errors >= 10:          # gateway struggling or offline: back off
                    self.stop.wait(min(300, errors * 5))
            if rows:
                stamp = now.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
                with open(os.path.join(self.data_dir, f"{now:%Y-%m-%d}.csv"), 'a') as f:
                    if f.tell() == 0:
                        f.write("timestamp,source,value\n")
                    f.writelines(f"{stamp},{s},{v}\n" for s, v in rows)
            # next whole second
            self.stop.wait(max(0.05, 1 - (time.time() % 1)))

    def upload(self, kwargs):
        if not self.token:
            return
        now = dt.datetime.now()
        for name in sorted(os.listdir(self.data_dir)):
            if not name.endswith('.csv'):
                continue
            day = name[:-4]
            if day == f"{now:%Y-%m-%d}" and now.time() < self.end:
                continue  # still recording
            path = os.path.join(self.data_dir, name)
            with open(path, 'rb') as f:
                content = gzip.compress(f.read(), mtime=0)
            if HomeExport.put_file(self, f"{self.folder}/{name}.gz", content, f"HA burst {day}"):
                os.rename(path, path + '.uploaded')


def read_token_key(path, key):
    try:
        with open(path) as f:
            for line in f:
                k, _, value = line.partition(':')
                if k.strip() == key:
                    return value.strip().strip('"\'') or None
    except FileNotFoundError:
        pass
    return None
