"""
Daily sunlight and weather history from Open-Meteo's free archive (no key).

Solar output swings with the weather, so comparing production between years
(ageing, soiling, the washed-panel test) needs to know how much sun there was.
This keeps sunlight_daily.csv in home_data: one row per day since install with
the sunlight energy reaching the ground, cloud cover, temperature and rain.

The archive runs a few days behind, so each run re-fetches the last
REFRESH_DAYS days (filling and correcting them) and everything missing since
install. Prints only row counts and dates, never values or the location,
because run logs on the public repo are visible to anyone.

Usage (from the data repo checkout, LAT and LONG set): python ../sunlight_logger.py
"""
import os
from datetime import date, timedelta

import pandas as pd
import requests

URL = 'https://archive-api.open-meteo.com/v1/archive'
FILE = 'sunlight_daily.csv'
INSTALL_DATE = date(2023, 4, 5)
REFRESH_DAYS = 14
DAILY = ['shortwave_radiation_sum', 'cloud_cover_mean', 'temperature_2m_max',
         'temperature_2m_min', 'precipitation_sum', 'sunshine_duration']


def fetch(start, end):
    """Daily rows from start to end inclusive."""
    r = requests.get(URL, timeout=60, params={
        'latitude': os.environ['LAT'], 'longitude': os.environ['LONG'],
        'start_date': start.isoformat(), 'end_date': end.isoformat(),
        'daily': ','.join(DAILY), 'timezone': 'America/Los_Angeles',
        'temperature_unit': 'fahrenheit', 'precipitation_unit': 'inch'})
    r.raise_for_status()
    d = pd.DataFrame(r.json()['daily'])
    return pd.DataFrame({
        'date': d.time,
        # MJ/m2 per day -> kWh/m2 per day (the solar industry's "peak sun hours")
        'sun_kwh_m2': (d.shortwave_radiation_sum / 3.6).round(3),
        'cloud_cover_pct': d.cloud_cover_mean.round(0),
        'sunshine_hours': (d.sunshine_duration / 3600).round(2),
        'temp_max_f': d.temperature_2m_max.round(1),
        'temp_min_f': d.temperature_2m_min.round(1),
        'rain_in': d.precipitation_sum.round(2),
    }).dropna(subset=['sun_kwh_m2'])   # the most recent days aren't in the archive yet


def main():
    yesterday = date.today() - timedelta(days=1)
    old = pd.read_csv(FILE) if os.path.exists(FILE) else pd.DataFrame(columns=['date'])
    have = set(old.date)
    missing = [INSTALL_DATE + timedelta(days=n) for n in range((yesterday - INSTALL_DATE).days + 1)]
    missing = [d for d in missing if d.isoformat() not in have or d > yesterday - timedelta(days=REFRESH_DAYS)]
    if not missing:
        print("Sunlight: up to date")
        return
    new = fetch(min(missing), yesterday)
    merged = pd.concat([old, new]).drop_duplicates('date', keep='last').sort_values('date')
    merged.to_csv(FILE, index=False)
    print(f"Sunlight: {len(new)} days fetched, history {merged.date.min()} to {merged.date.max()}")


if __name__ == '__main__':
    main()
