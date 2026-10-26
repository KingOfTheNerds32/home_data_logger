# Detecting the heat pump

**Answer:** the heat pump's state is `heatpump_info.heatpumpstatus` in the
iAquaLink `get_home` response. `pool_logger.py` logs it as the `heat_pump`
column (ON/OFF), with `heatpumpmode` as `heat_pump_mode` (`heat` or `chill`).

## Why it was hard to find

The pool has two independent heaters: a gas heater and a heat pump. Either or
both can be on. The gas heater appears as `pool_heater`, but the heat pump is
reported inside a nested block:

```json
"heatpump_info": {
  "isheatpumpPresent": true,
  "heatpumpstatus": "off",
  "isChillAvailable": true,
  "heatpumpmode": "heat",
  "heatpumptype": "4-wired"
}
```

`iaqualink==0.6.0` silently discards any home screen field whose value is a
dict or list (`IaquaDevice.from_data`), so it never appears in
`system.devices`. The logger therefore makes one extra raw `get_home` request
to read it.

An earlier theory that a `pool_heat_mode` field (0=off, 1=gas, 2=solar,
3=heat pump) encodes the heat source was wrong: that field does not exist on
this system, and a single mode value couldn't represent both heaters on.

## How it was confirmed (2026-10-26)

Using the probe in `investigation/heat_pump/`, raw snapshots were taken while
toggling each heater from the iAquaLink app, with the filter pump on:

| Step | Setting | `heatpumpstatus` | `pool_heater` (gas) |
|---|---|---|---|
| 0 | Both off | `off` | `0` |
| 1 | Heat pump on | `on` | `0` |
| 2 | Both on | `on` | `1` |
| 3 | Gas only | `off` | `1` |
| 4 | Both off | `off` | `0` |

No other fields changed apart from temperatures. The raw snapshots were
removed afterwards; they are preserved in git history at commit `004e471`
(`investigation/heat_pump/snapshots/`).

## Limitations

- `heatpumpstatus` changed to `on` immediately and stayed `on` through the
  heat pump's startup delay. It means the heat pump is switched on, not that
  its compressor is running at that moment. iAquaLink does not appear to
  report compressor activity; a power monitor on the heat pump's circuit
  would be needed for that.
- The gas heater only ever showed `0`/`1`. The library also defines `3`
  (enabled but not firing), which the logger currently records as OFF.
