"""
Outage HVAC (AppDaemon app): turns the Lennox zones off when the grid goes
down, so the battery lasts longer, and puts them back when the grid returns.

- Grid down (the Enphase grid-status sensor reads "off") for `off_after_seconds`:
  saves each zone's mode (e.g. heat_cool) to a file next to this app, then sets
  the zone to off.
- Grid back ("on") for `restore_after_seconds`: sets each saved zone back to
  its saved mode, unless someone changed that zone during the outage (it is
  no longer off), then deletes the file.
- "unavailable" (the Envoy briefly dropping off the network) is ignored.
- The saved file survives restarts: if AppDaemon or the Pi restarts mid-outage
  it won't save the "off" modes over the real ones, and once the grid is back
  it still restores them.
- Optional `test_entity` (an input_boolean helper you create in Home
  Assistant): turning it on acts like an outage and off like the grid
  returning, to try this without a real outage.

Everything runs on the home network (Enphase Envoy and Lennox S30 are local
integrations), so it works without internet, as long as the Pi, the network
and the thermostat have power from the battery.
"""
import fnmatch
import json
import os

import appdaemon.plugins.hass.hassapi as hass

UNSET = ('off', 'unavailable', 'unknown', None)


class OutageHvac(hass.Hass):

    def initialize(self):
        self.grid = self.args['grid_entity']
        self.test = self.args.get('test_entity')
        self.zones = self.args.get('zones', ['climate.*'])
        self.state_file = os.path.join(self.app_dir, 'outage_hvac_state.json')
        self.listen_state(self.on_grid, self.grid, new='off',
                          duration=int(self.args.get('off_after_seconds', 60)))
        self.listen_state(self.on_grid, self.grid, new='on',
                          duration=int(self.args.get('restore_after_seconds', 300)))
        if self.test:
            self.listen_state(self.on_grid, self.test)
        self.check()

    def on_grid(self, entity, attribute, old, new, kwargs):
        self.check()

    def outage(self):
        """True while the grid is down (or a test outage is on); None if unknown."""
        if self.test and self.get_state(self.test) == 'on':
            return True
        state = self.get_state(self.grid)
        return {'off': True, 'on': False}.get(state)

    def check(self):
        outage = self.outage()
        if outage is True and not self.saved():
            self.turn_off()
        elif outage is False and self.saved():
            self.restore()

    def saved(self):
        try:
            with open(self.state_file) as f:
                return json.load(f)
        except (FileNotFoundError, ValueError):
            return {}

    def zone_states(self):
        return {e: s.get('state') for e, s in (self.get_state('climate') or {}).items()
                if any(fnmatch.fnmatch(e, p) for p in self.zones)}

    def turn_off(self):
        modes = {e: s for e, s in self.zone_states().items() if s not in UNSET}
        if not modes:
            return
        with open(self.state_file, 'w') as f:
            json.dump(modes, f)
        for entity in modes:
            self.call_service('climate/set_hvac_mode', entity_id=entity, hvac_mode='off')
        self.announce(f"Grid is down: turned off {len(modes)} HVAC zones to save the battery. "
                      "They'll turn back on when the grid has been back for a few minutes.")

    def restore(self):
        now = self.zone_states()
        restored = 0
        for entity, mode in self.saved().items():
            if now.get(entity) == 'off':          # left alone during the outage
                self.call_service('climate/set_hvac_mode', entity_id=entity, hvac_mode=mode)
                restored += 1
        os.remove(self.state_file)
        self.announce(f"Grid is back: turned {restored} HVAC zones back on.")

    def announce(self, message):
        self.log(message)
        self.call_service('persistent_notification/create', title='Power outage', message=message)
        if self.args.get('notify_service'):
            self.call_service(self.args['notify_service'], title='Power outage', message=message)
