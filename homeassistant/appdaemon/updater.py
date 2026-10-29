"""
Self-updater (AppDaemon app): keeps the other Home Assistant apps in sync with
the home_data_logger repo, so changes deploy by merging on GitHub.

Every `interval_minutes` it downloads each file in `files` from the repo's
`branch` and overwrites the local copy only if the content changed; AppDaemon
then reloads the changed app on its own. This file is never updated itself,
so a bad change can't break the update mechanism, and secrets.yaml is never
touched.
"""
import os
import urllib.request

import appdaemon.plugins.hass.hassapi as hass


class Updater(hass.Hass):

    def initialize(self):
        self.base = (f"https://raw.githubusercontent.com/{self.args.get('repo', 'KingOfTheNerds32/home_data_logger')}"
                     f"/{self.args.get('branch', 'main')}/")
        self.files = self.args.get('files', {})
        self.run_every(self.update, 'now+10', int(self.args.get('interval_minutes', 30)) * 60)

    def update(self, kwargs):
        for remote, local in self.files.items():
            if os.path.basename(local) in ('updater.py', 'secrets.yaml'):
                continue
            path = os.path.join(self.app_dir, local)
            try:
                with urllib.request.urlopen(self.base + remote, timeout=30) as r:
                    content = r.read()
            except Exception as e:
                self.log(f"Could not fetch {remote}: {type(e).__name__}", level='WARNING')
                continue
            if not content.strip():
                continue
            try:
                with open(path, 'rb') as f:
                    if f.read() == content:
                        continue
            except FileNotFoundError:
                pass
            with open(path, 'wb') as f:
                f.write(content)
            self.log(f"Updated {local} from GitHub")
