# Home Assistant

Home Assistant OS runs on a Raspberry Pi 4 on the home network. It adds data
the cloud loggers can't get: per-panel solar, Lennox HVAC zones, Flume water.

## Apps (`appdaemon/`)

Two AppDaemon apps run in the AppDaemon add-on:

- **`home_export`** records selected entities to a local daily CSV:
  readings in `sample` every 5 minutes, and states in `on_change` every time
  they change (exact run times for HVAC zones, pool equipment, battery mode,
  grid status). Each night it uploads finished days, gzip-compressed, to the
  private `home_data` repo at `ha/YYYY-MM/YYYY-MM-DD.csv.gz` (long format:
  `timestamp, entity_id, attribute, value`), retrying failed days. It also
  uploads `ha/entities.txt` listing all entity ids, marked `s` (sampled) or
  `c` (on change).
- **`updater`** checks this repo every 30 minutes and pulls new versions of
  `apps.yaml` and `home_export.py` from `main`, which AppDaemon then reloads.
  **Changes deploy by merging to `main`.** The updater never updates itself or
  `secrets.yaml`.

### Setup (all in the Home Assistant web UI)

1. **GitHub token:** a fine-grained token with access to only `home_data`,
   permission **Contents: Read and write**.
2. **Add-ons** (Settings → Add-ons/Apps → Store): install **AppDaemon** and
   **File editor** (turn off **Enforce basepath** in File editor's
   configuration).
3. In File editor, in `/app_configs/a0d7b954_appdaemon/`:
   - `appdaemon.yaml`: set `time_zone: America/Los_Angeles` (and rough
     latitude/longitude)
   - `secrets.yaml`: `github_home_data_token: <the token>`
   - `apps/updater.py`, `apps/home_export.py`, `apps/apps.yaml`: paste from
     `appdaemon/` in this repo
4. Restart AppDaemon (with Watchdog on) and check its Log for
   `Sampling N entities every 5 min; recording every change of M`.
