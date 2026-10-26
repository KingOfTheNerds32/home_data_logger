# home_data_logger

Collects data about the house on GitHub Actions. This public repo holds only
code; all data is written to the private `home_data` repo.

| Source | Script | Data file (in `home_data`) | Schedule |
|---|---|---|---|
| Pool equipment (iAquaLink) | `pool_logger.py` | `pool_history.csv` | Every 5 min |
| Weather (OpenWeather) | `weather_logger.py` | `weather_history.csv` | Every 5 min |
| Run status for the above | `main.py` | `execution_log.csv` | Every 5 min |
| Solar, home use, battery, grid, EV charging (Enphase) | `energy_logger.py` | `energy_history.csv` | Daily, 15-min intervals |
| Solar production (Enphase) | `energy_logger.py` | `production_5min.csv` | Daily, 5-min intervals |
| EV charging sessions (Enphase) | `energy_logger.py` | `ev_sessions.csv` | Daily |
| Daily energy totals since install (Enphase) | `energy_logger.py` | `energy_daily.csv` | Weekly refresh |

## How it runs

- **Pool & Weather Logger** (`main.yml`) is triggered every 5 minutes by an
  external cron-job.org job sending a `poll-pool` repository dispatch.
  Sustained failures (3+ runs in a row) turn the run red, which sends
  GitHub's failure email.
- **Energy Logger** (`energy.yml`) runs daily, then spends spare API budget backfilling
  15-min history a week per request (Enphase free plan: 1,000 requests/month;
  usage is tracked in `enphase_state.json` and capped at 850).
- **Enphase Authorize** (`enphase_authorize.yml`) is a one-time setup, rerun
  only if Enphase authorization lapses.
- **Heat Pump Probe** (`heat_pump_probe.yml`) is a manual diagnostic; see
  `investigation/heat_pump/README.md`.

## Secrets

| Secret | Used for |
|---|---|
| `IQUALINK_USER`, `IQUALINK_PASS` | iAquaLink login |
| `OPENWEATHER_API_KEY`, `LAT`, `LONG` | Weather |
| `DATA_REPO_TOKEN` | Writing to `home_data` (Contents: read/write) |
| `ENPHASE_API_KEY`, `ENPHASE_CLIENT_ID`, `ENPHASE_CLIENT_SECRET` | Enphase developer app |
| `ENPHASE_REFRESH_TOKEN`, `ENPHASE_SYSTEM_ID` | Written automatically by the Enphase workflows |
| `GH_SECRETS_TOKEN` | Lets the Enphase workflows update the token secret (Secrets: read/write on this repo) |

## Notes

- `docs/heat_pump.md` explains how heat pump state is read.
