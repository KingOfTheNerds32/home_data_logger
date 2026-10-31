import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (ROOT, os.path.join(ROOT, 'tools'), os.path.join(ROOT, 'homeassistant', 'appdaemon')):
    sys.path.insert(0, path)

# home_export.py imports AppDaemon, which only exists on the Pi.
_hassapi = types.ModuleType('appdaemon.plugins.hass.hassapi')
_hassapi.Hass = object
for name, module in [('appdaemon', types.ModuleType('appdaemon')),
                     ('appdaemon.plugins', types.ModuleType('appdaemon.plugins')),
                     ('appdaemon.plugins.hass', types.ModuleType('appdaemon.plugins.hass')),
                     ('appdaemon.plugins.hass.hassapi', _hassapi)]:
    sys.modules.setdefault(name, module)
