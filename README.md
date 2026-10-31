# home_data_logger

Code that collects data about the house: solar, battery, grid, EV charging,
pool, weather, HVAC, water and more. This public repo holds only code; all
data goes to the private `home_data` repo, whose README describes every file.

| Folder / file | What it is | Runs on |
|---|---|---|
| `main.py`, `pool_logger.py`, `weather_logger.py` | Pool equipment (iAquaLink) and weather (OpenWeather), every 5 min | GitHub Actions |
| `energy_logger.py` | Enphase cloud: daily 15-min and 5-min data, EV sessions, daily totals, history backfill | GitHub Actions |
| `watchdog.py` | Alerts when any source goes stale or part of the solar array stops reporting | GitHub Actions |
| `homeassistant/` | AppDaemon apps on the Home Assistant Pi: 1- and 5-min snapshots and every state change, uploaded nightly (see its README) | Raspberry Pi |
| `tools/` | Scripts run by hand: importing Enlighten report exports, building the dashboard's summary tables | Locally |
| `tests/` | Unit tests | GitHub Actions, on every pull request |
| `investigation/`, `docs/` | One-off diagnostics and notes from figuring out each data source | By hand |

## Workflows

- **Pool & Weather Logger** (`main.yml`): triggered every 5 minutes by an
  external cron-job.org job sending a `poll-pool` repository dispatch.
  Sustained failures (3+ runs in a row) turn the run red, which sends
  GitHub's failure email.
- **Energy Logger** (`energy.yml`): daily. Pulls the last complete day, then
  spends spare API budget backfilling 15-min history a week at a time back to
  install (Enphase free plan: 1,000 requests/month; usage is tracked in
  `enphase_state.json` and capped at 850).
- **Data Watchdog** (`watchdog.yml`): every 4 hours. Fails (emailing you) if
  pool/weather data is over 45 minutes old, Enphase data over ~2 days old,
  the Home Assistant export more than 2 days behind, or fewer microinverters
  reported at midday than normal. It also re-enables the scheduled workflows,
  which GitHub otherwise disables in a public repo after 60 days without
  commits.
- **Tests** (`tests.yml`): runs `tests/` on every pull request and push to main.
- **Enphase Authorize** (`enphase_authorize.yml`): one-time setup, rerun only
  if Enphase authorization lapses.
- **Enphase / Lennox / Heat Pump Probe**: manual diagnostics; see
  `investigation/`.

Every workflow that touches data uses the shared steps in `.github/actions/`:
`checkout-data` clones `home_data` into `data/`, and `push-data` commits the
named files and pushes with retries. Python dependencies are in
`requirements.txt`.

## Home Assistant

The Pi's AppDaemon apps (`homeassistant/appdaemon/`) keep themselves up to
date: the `updater` app pulls `apps.yaml` and `home_export.py` from `main`
every 30 minutes, so **changes deploy by merging to main**. What is recorded
is set by the patterns in `apps.yaml`. Setup is in `homeassistant/README.md`.

## Tools

Run from a checkout of `home_data`:

```
python ../tools/enlighten_import.py production|battery|panels|ev FILES...
python ../tools/dashboard_data.py OUT_DIR
```

`enlighten_import.py` merges Enlighten Manager report exports (CSVs, folders
or zips) into the stored files; running it twice on the same files changes
nothing. Keep each original export in `home_data/imports/`. `dashboard_data.py`
writes the small tables the Home energy dashboard reads.

## Tests

```
pip install -r requirements-dev.txt
python -m pytest -q
```

## Secrets

| Secret | Used for |
|---|---|
| `IQUALINK_USER`, `IQUALINK_PASS` | iAquaLink login |
| `OPENWEATHER_API_KEY`, `LAT`, `LONG` | Weather |
| `DATA_REPO_TOKEN` | Writing to `home_data` (Contents: read/write) |
| `ENPHASE_API_KEY`, `ENPHASE_CLIENT_ID`, `ENPHASE_CLIENT_SECRET` | Enphase developer app |
| `ENPHASE_REFRESH_TOKEN`, `ENPHASE_SYSTEM_ID`, `ENPHASE_EV_SERIAL` | Written automatically by the Enphase workflows |
| `GH_SECRETS_TOKEN` | Lets the Enphase workflows update the token secret (Secrets: read/write on this repo) |
| `LENNOX_USER`, `LENNOX_PASS` | Lennox probe only |

The Home Assistant export uses its own token, stored on the Pi in
AppDaemon's `secrets.yaml`, not here.
