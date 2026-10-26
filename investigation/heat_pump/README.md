# Heat pump investigation

Side experiment to find which iAquaLink field shows whether the heat pump is on.
It does not touch the main logger or its CSVs.

- `probe.py` captures the raw, unfiltered iAquaLink responses (read-only) and
  saves a redacted snapshot to `snapshots/`, labeled with the scenario.
- Run it via the **Heat Pump Probe** workflow (Actions tab, "Run workflow"),
  entering a scenario label such as `both_off`, `hp_on`, `gas_on`, `both_on`.
- Each run's summary shows which fields changed since the previous snapshot.

Once the field is identified, the finding gets integrated into `pool_logger.py`
and this folder can be deleted.
