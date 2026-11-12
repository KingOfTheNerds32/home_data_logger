from home_export import HomeExport


def test_rows_for_records_state_and_domain_attributes():
    assert HomeExport.rows_for('cover.great_room_left', {'state': 'open', 'attributes': {'current_position': 40, 'x': 1}}) == [
        ('cover.great_room_left', '', 'open'), ('cover.great_room_left', 'current_position', 40)]
    rows = HomeExport.rows_for('climate.bedrooms_noah', {'state': 'heat_cool', 'attributes': {'hvac_action': 'cooling'}})
    assert ('climate.bedrooms_noah', 'hvac_action', 'cooling') in rows
    assert HomeExport.rows_for('sensor.pool_temp', None) == [('sensor.pool_temp', '', None)]


def test_remove_uploaded_keeps_pending_files(tmp_path):
    from home_export import remove_uploaded
    (tmp_path / '2026-11-01.csv.uploaded').write_text('x')
    (tmp_path / '2026-11-02.csv').write_text('y')
    remove_uploaded(str(tmp_path))
    assert sorted(p.name for p in tmp_path.iterdir()) == ['2026-11-02.csv']
