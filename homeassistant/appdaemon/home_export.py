"""
Home Assistant -> home_data exporter (AppDaemon app).

Records two kinds of data to a local daily CSV:
- `on_change` entities (zone heating/cooling, switches, battery mode, ...):
  a row every time they change, with the exact time, so run times are exact.
- `sample` entities (power, temperature, ...): a snapshot every
  `interval_minutes` minutes, aligned to the clock; `sample_fast` entities
  (whole-home and battery power) every `fast_interval_minutes`.

Once a day it uploads finished days, gzip-compressed, to the private
home_data repo via the GitHub API; uploads that fail are retried on the next run, so no day is lost.
On startup it also uploads ha/entities.txt: every entity id (no values),
marked `f` (fast), `s` (sampled) or `c` (on change), to help tune the patterns.

Standard library only. Configured in apps.yaml; see homeassistant/README.md.
"""
import base64
import csv
import fnmatch
import gzip
import json
import os
import urllib.error
import urllib.request
from datetime import timedelta

import appdaemon.plugins.hass.hassapi as hass

# Attributes recorded alongside the state, by entity domain.
ATTRIBUTES = {
    'climate.': ['current_temperature', 'current_humidity', 'hvac_action',
                 'temperature', 'target_temp_low', 'target_temp_high'],
    'cover.': ['current_position'],   # shade position, 0-100
    'water_heater.': ['current_temperature', 'temperature', 'operation_mode'],
}


class HomeExport(hass.Hass):

    def initialize(self):
        self.exclude = self.args.get('exclude', [])
        self.interval = int(self.args.get('interval_minutes', 5))
        self.fast_interval = int(self.args.get('fast_interval_minutes', 1))
        self.repo = self.args.get('repo', 'KingOfTheNerds32/home_data')
        self.folder = self.args.get('folder', 'ha')
        self.token = self.args['github_token']
        self.data_dir = os.path.join(os.path.dirname(self.app_dir), 'home_export')
        os.makedirs(self.data_dir, exist_ok=True)

        self.fast = self.matching(self.args.get('sample_fast', []))
        self.sampled = [e for e in self.matching(self.args.get('sample', [])) if e not in self.fast]
        self.watched = [e for e in self.matching(self.args.get('on_change', []))
                        if e not in self.sampled and e not in self.fast]
        for entity_id in self.watched:
            self.listen_state(self.changed, entity_id, attribute='all')

        # Snapshots on clock-aligned intervals (:00, :05, ...). AppDaemon's
        # clock uses the time_zone in appdaemon.yaml, not the container's.
        for entities, minutes in ((self.sampled, self.interval), (self.fast, self.fast_interval)):
            if entities:
                now = self.datetime()
                first = now.replace(second=0, microsecond=0) + timedelta(minutes=minutes - now.minute % minutes)
                self.run_every(self.sample, first, minutes * 60, entities=entities)
        self.run_daily(self.upload_finished_days, '00:20:00')

        # Record where every watched entity starts, so on/off spans are known
        # from the first minute rather than from its first change.
        self.run_in(self.record_watched, 5)
        self.run_in(self.upload_entity_list, 30)
        self.run_in(self.upload_finished_days, 60)
        self.log(f"Sampling {len(self.fast)} entities every {self.fast_interval} min and "
                 f"{len(self.sampled)} every {self.interval} min; recording every change of {len(self.watched)}")

    def matching(self, patterns):
        ids = sorted(self.get_state().keys())
        keep = [e for e in ids if any(fnmatch.fnmatch(e, p) for p in patterns)]
        return [e for e in keep if not any(fnmatch.fnmatch(e, p) for p in self.exclude)]

    @staticmethod
    def rows_for(entity_id, state):
        state = state or {}
        rows = [(entity_id, '', state.get('state'))]
        attrs = state.get('attributes', {})
        for domain, names in ATTRIBUTES.items():
            if entity_id.startswith(domain):
                rows += [(entity_id, a, attrs.get(a)) for a in names if a in attrs]
        return rows

    def write(self, when, rows):
        path = os.path.join(self.data_dir, f"{when:%Y-%m-%d}.csv")
        new = not os.path.exists(path)
        with open(path, 'a', newline='') as f:
            writer = csv.writer(f)
            if new:
                writer.writerow(['timestamp', 'entity_id', 'attribute', 'value'])
            ts = f"{when:%Y-%m-%d %H:%M:%S}"
            writer.writerows((ts, e, a, v) for e, a, v in rows)

    def sample(self, kwargs):
        now = self.datetime().replace(microsecond=0)
        states = self.get_state()
        rows = []
        for entity_id in kwargs['entities']:
            rows += self.rows_for(entity_id, states.get(entity_id))
        self.write(now, rows)

    def record_watched(self, kwargs):
        states = self.get_state()
        rows = []
        for entity_id in self.watched:
            rows += self.rows_for(entity_id, states.get(entity_id))
        self.write(self.datetime().replace(microsecond=0), rows)

    def changed(self, entity_id, attribute, old, new, kwargs):
        # attribute='all' also fires on unrelated attribute updates; only
        # record when something we track actually changed.
        rows = self.rows_for(entity_id, new)
        if rows != self.rows_for(entity_id, old):
            self.write(self.datetime().replace(microsecond=0), rows)

    def upload_finished_days(self, kwargs):
        today = f"{self.datetime():%Y-%m-%d}.csv"
        for name in sorted(os.listdir(self.data_dir)):
            if not name.endswith('.csv') or name >= today:
                continue  # today's file is still being written
            path = os.path.join(self.data_dir, name)
            with open(path, 'rb') as f:
                # Compressed ~8x; pandas reads .csv.gz directly.
                content = gzip.compress(f.read(), mtime=0)
            if self.put_file(f"{self.folder}/{name[:7]}/{name}.gz", content, f"HA export {name[:-4]}"):
                os.rename(path, path + '.uploaded')

    def upload_entity_list(self, kwargs):
        ids = sorted(self.get_state().keys())
        mark = lambda e: 'f' if e in self.fast else 's' if e in self.sampled else 'c' if e in self.watched else ' '
        lines = [f"{mark(e)} {e}" for e in ids]
        header = "# All Home Assistant entities; f = sampled fast, s = sampled, c = recorded on change\n"
        self.put_file(f"{self.folder}/entities.txt", (header + "\n".join(lines) + "\n").encode(),
                      "HA entity list")

    def put_file(self, path, content, message):
        """Create or replace a file in the repo. Returns True on success."""
        url = f"https://api.github.com/repos/{self.repo}/contents/{path}"
        headers = {'Authorization': f"Bearer {self.token}", 'Accept': 'application/vnd.github+json',
                   'User-Agent': 'home-export'}
        sha = None
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
                sha = json.load(r).get('sha')
        except urllib.error.HTTPError as e:
            if e.code != 404:
                self.log(f"GitHub check failed for {path}: HTTP {e.code}", level='WARNING')
                return False
        except Exception as e:
            self.log(f"GitHub check failed for {path}: {type(e).__name__}", level='WARNING')
            return False
        body = {'message': message, 'content': base64.b64encode(content).decode()}
        if sha:
            body['sha'] = sha
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method='PUT',
                                     headers={**headers, 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=60):
                self.log(f"Uploaded {path}")
                return True
        except urllib.error.HTTPError as e:
            self.log(f"Upload failed for {path}: HTTP {e.code} (check the token's access to {self.repo})",
                     level='WARNING')
        except Exception as e:
            self.log(f"Upload failed for {path}: {type(e).__name__}", level='WARNING')
        return False
