# iAquaLink probe

Reusable, read-only diagnostic for finding which iAquaLink field tracks a
piece of equipment. Originally built to find the heat pump field (see
`docs/heat_pump.md`); useful again for new equipment such as a chlorinator.

- `probe.py` captures the raw, unfiltered iAquaLink responses, including the
  nested fields the `iaqualink` library discards, and saves a redacted snapshot
  to `snapshots/`, labeled with a scenario.
- Run it via the **Heat Pump Probe** workflow (Actions tab, "Run workflow").
- Each run's summary shows which fields changed since the previous snapshot.

Method: take a baseline, toggle one thing in the iAquaLink app, run the probe
again, and repeat. Delete `snapshots/` once the field is identified and
documented.
