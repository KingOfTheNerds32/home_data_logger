from home_export import HomeExport


def test_rows_for_records_state_and_domain_attributes():
    assert HomeExport.rows_for('cover.great_room_left', {'state': 'open', 'attributes': {'current_position': 40, 'x': 1}}) == [
        ('cover.great_room_left', '', 'open'), ('cover.great_room_left', 'current_position', 40)]
    rows = HomeExport.rows_for('climate.bedrooms_noah', {'state': 'heat_cool', 'attributes': {'hvac_action': 'cooling'}})
    assert ('climate.bedrooms_noah', 'hvac_action', 'cooling') in rows
    assert HomeExport.rows_for('sensor.pool_temp', None) == [('sensor.pool_temp', '', None)]
