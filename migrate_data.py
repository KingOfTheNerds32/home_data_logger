"""
One-time migration helper: brings the CSV history that used to live in this
public repo into the private data repo checkout. Copies a file if the data
repo lacks it, otherwise merges in any rows it is missing.
Safe to run every time; does nothing once the old CSVs are removed here.

Usage: python migrate_data.py <data_dir>
"""
import os
import sys

FILES = ['pool_history.csv', 'weather_history.csv', 'execution_log.csv']


def main(data_dir):
    for name in FILES:
        if not os.path.exists(name):
            continue
        target = os.path.join(data_dir, name)
        with open(name) as f:
            source = [l for l in f.read().splitlines() if l.strip()]
        if not os.path.exists(target):
            with open(target, 'w') as f:
                f.write('\n'.join(source) + '\n')
            print(f"Seeded {name} ({len(source) - 1} rows)")
            continue
        with open(target) as f:
            existing = [l for l in f.read().splitlines() if l.strip()]
        present = set(existing[1:])
        missing = [l for l in source[1:] if l not in present]
        if missing:
            # Rows can arrive out of order, so re-sort by the leading timestamp.
            rows = sorted(existing[1:] + missing, key=lambda l: l.split(',')[0])
            with open(target, 'w') as f:
                f.write('\n'.join([existing[0]] + rows) + '\n')
            print(f"Merged {len(missing)} missing rows into {name}")


if __name__ == "__main__":
    main(sys.argv[1])
