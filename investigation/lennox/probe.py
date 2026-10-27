"""
Lennox iComfort probe (read-only): connects to the Lennox cloud with the
lennoxs30api library, listens for ~30 seconds, and records what each system,
zone and piece of equipment reports, to decide what to log.

- Full values go to lennox_probe.json in the private data repo (cwd), with
  personal fields dropped and serial numbers masked.
- The public run summary lists only field names and whether they have values.

Usage (from the data repo checkout): python ../investigation/lennox/probe.py
"""
import asyncio
import json
import os

from lennoxs30api import s30api_async

SIMPLE = (str, int, float, bool, type(None))
# Never saved, even privately.
PERSONAL = {'address', 'streetAddress', 'city', 'state', 'zip', 'zipCode', 'postalCode', 'country',
            'latitude', 'longitude', 'email', 'phone', 'firstName', 'lastName', 'homeName'}
MASKED = {'serialNumber', 'unit_serial_number', 'sysId', 'homeId', 'productSerialNumber'}
LISTEN_SECONDS = 30


def attributes(obj):
    """Public, simple-valued attributes of a library object."""
    out = {}
    for key, value in vars(obj).items():
        if key.startswith('_') or key in PERSONAL:
            continue
        if key in MASKED and value:
            out[key] = '<masked>'
        elif isinstance(value, SIMPLE):
            out[key] = value
    return out


def equipment_summary(eq):
    info = attributes(eq)
    info['diagnostics'] = [{'name': d.name, 'value': d.value, 'unit': d.unit} for d in eq.diagnostics.values()]
    info['parameter_count'] = len(eq.parameters)
    return info


async def main():
    api = s30api_async(os.environ['LENNOX_USER'], os.environ['LENNOX_PASS'], None,
                       pii_message_logs=True, message_debug_logging=False)
    await api.serverConnect()
    try:
        for system in api.system_list:
            await api.subscribe(system)
        # Data arrives as messages after subscribing; keep pumping for a while.
        loop_end = asyncio.get_event_loop().time() + LISTEN_SECONDS
        while asyncio.get_event_loop().time() < loop_end:
            await api.messagePump()
            await asyncio.sleep(2)

        result = {'systems': []}
        for system in api.system_list:
            result['systems'].append({
                'system': attributes(system),
                'zones': [attributes(z) for z in system.zone_list],
                'equipment': [equipment_summary(eq) for eq in system.equipment.values()],
            })
    finally:
        await api.shutdown()

    with open('lennox_probe.json', 'w') as f:
        json.dump(result, f, indent=2, default=str)

    lines = ["## Lennox Probe", ""]
    for i, s in enumerate(result['systems'], 1):
        populated = lambda d: sorted(k for k, v in d.items() if v not in (None, '', [], {}))
        lines.append(f"### System {i}: {s['system'].get('productType')}, {len(s['zones'])} zones, "
                     f"{len(s['equipment'])} equipment")
        lines.append(f"**System fields with values:** {', '.join(populated(s['system']))}")
        if s['zones']:
            lines.append(f"**Zone fields with values:** {', '.join(populated(s['zones'][0]))}")
        for eq in s['equipment']:
            names = [d['name'] for d in eq['diagnostics'] if d['name']]
            lines.append(f"- Equipment `{eq.get('equipment_type_name')}`: {len(names)} diagnostics"
                         + (f" ({', '.join(names[:15])}{'...' if len(names) > 15 else ''})" if names else ''))
        lines.append("")
    with open('lennox_probe_summary.md', 'w') as f:
        f.write("\n".join(lines) + "\n")
    print(f"Probed {len(result['systems'])} system(s)")


if __name__ == "__main__":
    asyncio.run(main())
