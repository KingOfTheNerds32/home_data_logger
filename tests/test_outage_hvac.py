import json

from outage_hvac import OutageHvac

GRID = 'binary_sensor.grid_status'
TEST = 'input_boolean.simulate_grid_outage'


def make_app(tmp_path, grid='on', zones=None, test='off'):
    app = OutageHvac()
    app.args = {'grid_entity': GRID, 'test_entity': TEST,
                'zones': ['climate.bedrooms_*', 'climate.shared_spaces_*']}
    app.app_dir = str(tmp_path)
    app.states = {GRID: grid, TEST: test}
    app.climate = zones if zones is not None else {
        'climate.bedrooms_aiden': 'heat_cool', 'climate.bedrooms_office': 'off',
        'climate.shared_spaces_loft': 'cool', 'climate.pool_set_point': 'heat'}
    app.calls, app.listeners = [], []
    app.log = lambda *a, **k: None
    app.listen_state = lambda cb, entity, **kw: app.listeners.append((entity, kw))

    def get_state(entity):
        if entity == 'climate':
            return {e: {'state': s} for e, s in app.climate.items()}
        return app.states.get(entity)

    def call_service(service, **kw):
        app.calls.append((service, kw))
        if service == 'climate/set_hvac_mode':
            app.climate[kw['entity_id']] = kw['hvac_mode']

    app.get_state, app.call_service = get_state, call_service
    return app


def hvac_calls(app):
    return [(kw['entity_id'], kw['hvac_mode']) for s, kw in app.calls if s == 'climate/set_hvac_mode']


def test_outage_turns_zones_off_and_restores_them(tmp_path):
    app = make_app(tmp_path)
    app.initialize()
    assert hvac_calls(app) == []                       # grid up: nothing to do

    app.states[GRID] = 'off'
    app.on_grid(GRID, None, 'on', 'off', {})
    assert sorted(hvac_calls(app)) == [('climate.bedrooms_aiden', 'off'), ('climate.shared_spaces_loft', 'off')]
    assert json.load(open(app.state_file)) == {'climate.bedrooms_aiden': 'heat_cool', 'climate.shared_spaces_loft': 'cool'}
    assert app.climate['climate.pool_set_point'] == 'heat'   # pool isn't an HVAC zone

    app.calls.clear()
    app.states[GRID] = 'on'
    app.on_grid(GRID, None, 'off', 'on', {})
    assert sorted(hvac_calls(app)) == [('climate.bedrooms_aiden', 'heat_cool'), ('climate.shared_spaces_loft', 'cool')]
    assert app.climate['climate.bedrooms_office'] == 'off'   # was already off: stays off
    assert not app.saved()


def test_restart_during_outage_keeps_the_real_modes(tmp_path):
    app = make_app(tmp_path, grid='off')
    app.initialize()                                   # catches an outage already in progress
    saved = app.saved()
    assert saved == {'climate.bedrooms_aiden': 'heat_cool', 'climate.shared_spaces_loft': 'cool'}

    restarted = make_app(tmp_path, grid='off', zones=dict(app.climate))
    restarted.initialize()                             # zones now read "off"; must not overwrite
    assert restarted.saved() == saved and hvac_calls(restarted) == []

    restarted.states[GRID] = 'on'
    restarted.initialize()                             # restarted after the grid came back
    assert ('climate.bedrooms_aiden', 'heat_cool') in hvac_calls(restarted)


def test_zone_changed_during_outage_is_left_alone(tmp_path):
    app = make_app(tmp_path, grid='off')
    app.initialize()
    app.climate['climate.bedrooms_aiden'] = 'cool'     # someone turned it back on
    app.calls.clear()
    app.states[GRID] = 'on'
    app.check()
    assert hvac_calls(app) == [('climate.shared_spaces_loft', 'cool')]


def test_unavailable_grid_does_nothing_and_test_switch_works(tmp_path):
    app = make_app(tmp_path, grid='unavailable')
    app.initialize()
    assert hvac_calls(app) == []

    app.states[GRID] = 'on'
    app.states[TEST] = 'on'
    app.check()
    assert len(hvac_calls(app)) == 2
    app.states[TEST] = 'off'
    app.check()
    assert len(hvac_calls(app)) == 4 and not app.saved()
