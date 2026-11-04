# Home Assistant

Home Assistant OS runs on a Raspberry Pi 4 on the home network. It adds data
the cloud loggers can't get: per-panel solar, Lennox HVAC zones, Flume water.

## Apps (`appdaemon/`)

Four AppDaemon apps run in the AppDaemon add-on:

- **`home_export`** records selected entities to a local daily CSV:
  whole-home and battery power in `sample_fast` every minute, other readings
  in `sample` every 5 minutes, and states in `on_change` every time
  they change (exact run times for HVAC zones, pool equipment, battery mode,
  grid status). Every hour it uploads any finished days, gzip-compressed, to the
  private `home_data` repo at `ha/YYYY-MM/YYYY-MM-DD.csv.gz` (long format:
  `timestamp, entity_id, attribute, value`), retrying failed days. It also
  uploads `ha/entities.txt` listing all entity ids, marked `f` (fast),
  `s` (sampled) or `c` (on change).
- **`outage_hvac`** turns the Lennox zones off when the Enphase grid-status
  sensor shows an outage (for a minute), so the battery lasts longer, and back
  to their previous modes once the grid has been back for 5 minutes. Zones
  someone changes during the outage are left alone. Each time it posts a Home
  Assistant notification and a push alert (with the battery level) to every
  phone signed in to the Home Assistant Companion app. To try it without an outage, create a Toggle helper
  named `simulate_grid_outage` (Settings → Devices & services → Helpers) and
  switch it on, then off.
- **`burst_recorder`** reads the Enphase gateway's meters and batteries
  every second, all day (until the `until` date in `apps.yaml`)
  and uploads each morning to `home_data` at `ha/burst/`. Read-only. Needs a
  gateway token from entrez.enphaseenergy.com in `secrets.yaml` as
  `envoy_token: <token>`; it stays idle without one.
- **`updater`** checks this repo every 30 minutes and pulls new versions of
  the files listed under it in `apps.yaml` from `main`, which AppDaemon then reloads.
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
   `Sampling N entities every 1 min and M every 5 min; recording every change of K`.
