"""
Home Assistant -> home_data exporter (AppDaemon app).

Every `interval_minutes` it records the state of matching entities (plus the
useful attributes of climate entities) to a local daily CSV, and once a day it
uploads finished days to the private home_data repo via the GitHub API.
Uploads that fail are retried on the next run, so no day is lost.

On startup it also uploads ha/entities.txt: a list of entity ids (no values),
so the include patterns can be tuned without guessing.

Uses only the Python standard library. Configure in apps.yaml; see
homeassistant/README.md.
"""
import base64
import csv
import fnmatch
import json
import os
import urllib.error
import urllib.request
from datetime import timedelta

import appdaemon.plugins.hass.hassapi as hass

# Climate attributes worth keeping alongside each zone's state.
CLIMATE_ATTRIBUTES = ['current_temperature', 'current_humidity', 'hvac_action',
                      'temperature', 'target_temp_low', 'target_temp_high']


class HomeExport(hass.Hass):

    def initialize(self):
        self.include = self.args.get('include', [])
        self.exclude = self.args.get('exclude', [])
        self.interval = int(self.args.get('interval_minutes', 15))
        self.repo = self.args.get('repo', 'KingOfTheNerds32/home_data')
        self.folder = self.args.get('folder', 'ha')
        self.token = self.args['github_token']
        self.data_dir = os.path.join(os.path.dirname(self.app_dir), 'home_export')
        os.makedirs(self.data_dir, exist_ok=True)

        # Sample on clock-aligned intervals (:00, :15, ...). AppDaemon's clock
        # uses the time_zone in appdaemon.yaml, not the container's.
        now = self.datetime()
        first = now.replace(second=0, microsecond=0) + timedelta(
            minutes=self.interval - now.minute % self.interval)
        self.run_every(self.sample, first, self.interval * 60)
        self.run_daily(self.upload_finished_days, '00:20:00')

        self.run_in(self.upload_entity_list, 30)
        self.run_in(self.upload_finished_days, 60)
        self.log(f"Exporting {len(self.matching_entities())} entities every {self.interval} min")

    def matching_entities(self):
        ids = sorted(self.get_state().keys())
        keep = [e for e in ids if any(fnmatch.fnmatch(e, p) for p in self.include)]
        return [e for e in keep if not any(fnmatch.fnmatch(e, p) for p in self.exclude)]

    def sample(self, kwargs):
        now = self.datetime()
        states = self.get_state()
        rows = []
        for entity_id in self.matching_entities():
            state = states.get(entity_id) or {}
            rows.append((entity_id, '', state.get('state')))
            if entity_id.startswith('climate.'):
                attrs = state.get('attributes', {})
                rows += [(entity_id, a, attrs.get(a)) for a in CLIMATE_ATTRIBUTES if a in attrs]
        path = os.path.join(self.data_dir, f"{now:%Y-%m-%d}.csv")
        new = not os.path.exists(path)
        with open(path, 'a', newline='') as f:
            writer = csv.writer(f)
            if new:
                writer.writerow(['timestamp', 'entity_id', 'attribute', 'value'])
            ts = f"{now:%Y-%m-%d %H:%M:00}"
            writer.writerows((ts, e, a, v) for e, a, v in rows)

    def upload_finished_days(self, kwargs):
        today = f"{self.datetime():%Y-%m-%d}.csv"
        for name in sorted(os.listdir(self.data_dir)):
            if not name.endswith('.csv') or name >= today:
                continue  # today's file is still being written
            path = os.path.join(self.data_dir, name)
            with open(path, 'rb') as f:
                content = f.read()
            if self.put_file(f"{self.folder}/{name[:7]}/{name}", content, f"HA export {name[:-4]}"):
                os.rename(path, path + '.uploaded')

    def upload_entity_list(self, kwargs):
        ids = sorted(self.get_state().keys())
        exported = set(self.matching_entities())
        lines = [f"{'*' if e in exported else ' '} {e}" for e in ids]
        body = ("# All Home Assistant entities; * = currently exported\n" + "\n".join(lines) + "\n").encode()
        self.put_file(f"{self.folder}/entities.txt", body, "HA entity list")

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
