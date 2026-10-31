"""
Import trips exported from the Audi (myAudi "Export trips" zip) into home_data.

The export's "Short-term memory.csv" lists each trip as the car recorded it:
when it ended, miles, time driven, average speed and the battery energy the
car counted as used. The car's identification number in the file's first line
is not stored.

Usage (from the home_data checkout):
    python ../tools/audi_import.py path/to/ExportTrips-....zip
Writes or merges audi_trips.csv (one row per trip, by end time).
"""
import io
import os
import sys
import zipfile

import pandas as pd

PATH = 'audi_trips.csv'


def read_trips(text):
    """Trips from a 'Short-term memory.csv' (semicolon-separated, day-first dates)."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith('End of journey'))
    t = pd.read_csv(io.StringIO('\n'.join(lines[start:])), sep=';', thousands=',', dtype=str)
    t = t.iloc[:, :6]
    t.columns = ['end_time', 'miles', 'time_driven', 'avg_mph', 'kwh_per_100mi', 'kwh']
    t['end_time'] = pd.to_datetime(t.end_time, format='%d/%m/%Y, %H:%M').dt.strftime('%Y-%m-%d %H:%M')
    for col in ('miles', 'avg_mph', 'kwh_per_100mi', 'kwh'):
        t[col] = pd.to_numeric(t[col].str.replace(',', '', regex=False))
    hours, minutes = t.time_driven.str.split(':', expand=True).astype(int).T.values
    t['minutes_driven'] = hours * 60 + minutes
    return t[['end_time', 'miles', 'minutes_driven', 'avg_mph', 'kwh_per_100mi', 'kwh']]


def main():
    trips = []
    for path in sys.argv[1:]:
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if name.endswith('Short-term memory.csv'):
                    trips.append(read_trips(z.read(name).decode('utf-8-sig')))
    new = pd.concat(trips)
    old = pd.read_csv(PATH) if os.path.exists(PATH) else new.iloc[0:0]
    merged = pd.concat([old, new]).drop_duplicates('end_time', keep='last').sort_values('end_time')
    merged.to_csv(PATH, index=False)
    print(f"{PATH}: {len(merged)} trips, {merged.end_time.min()} to {merged.end_time.max()}")


if __name__ == '__main__':
    main()
