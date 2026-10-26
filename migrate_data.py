"""
One-time migration helper: brings the CSV history that used to live in this
public repo into the private data repo checkout. Copies a file if the data
repo lacks it, otherwise appends any rows newer than its last timestamp.
Safe to run every time; does nothing once the old CSVs are removed here.

Usage: python migrate_data.py <data_dir>
"""
import os
import sys

FILES = ['pool_history.csv', 'weather_history.csv', 'execution_log.csv']


def last_timestamp(path):
    with open(path) as f:
        lines = [l for l in f.read().splitlines() if l.strip()]
    return lines[-1].split(',')[0] if len(lines) > 1 else ''


def main(data_dir):
    for name in FILES:
        if not os.path.exists(name):
            continue
        target = os.path.join(data_dir, name)
        with open(name) as f:
            source = f.read().splitlines()
        if not os.path.exists(target):
            with open(target, 'w') as f:
                f.write('\n'.join(source) + '\n')
            print(f"Seeded {name} ({len(source) - 1} rows)")
            continue
        cutoff = last_timestamp(target)
        newer = [l for l in source[1:] if l.strip() and l.split(',')[0] > cutoff]
        if newer:
            with open(target, 'a') as f:
                f.write('\n'.join(newer) + '\n')
            print(f"Appended {len(newer)} newer rows to {name}")


if __name__ == "__main__":
    main(sys.argv[1])
