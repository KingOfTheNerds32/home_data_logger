# Home Assistant

Home Assistant OS runs on a Raspberry Pi 4 on the home network. It adds data
the cloud loggers can't get: per-panel solar, Lennox HVAC zones, Flume water.

## Exporter (`appdaemon/`)

An AppDaemon app records selected entities every 15 minutes and uploads one
CSV per day to the private `home_data` repo at `ha/YYYY-MM/YYYY-MM-DD.csv`
(long format: `timestamp, entity_id, attribute, value`). It also uploads
`ha/entities.txt`, listing all entity ids with `*` marking exported ones.

### Setup (all in the Home Assistant web UI)

1. **GitHub token:** create a fine-grained token with access to only
   `home_data`, permission **Contents: Read and write**.
2. **Install add-ons** (Settings → Add-ons/Apps → Store): **AppDaemon** and
   **File editor**. In File editor's Configuration, turn off
   **Enforce basepath**, then start both.
3. In File editor, open `/addon_configs/a0d7b954_appdaemon/`:
   - `apps/home_export.py`: paste `appdaemon/home_export.py` from this repo
   - `apps/apps.yaml`: paste `appdaemon/apps.yaml` from this repo
   - `secrets.yaml`: add `github_home_data_token: <the token>`
4. Restart the AppDaemon add-on and check its **Log** for
   `Exporting N entities every 15 min`.

The first daily file arrives the night after setup; `entities.txt` within a
minute of starting.
