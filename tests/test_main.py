import pandas as pd

from main import consecutive_failures


def test_consecutive_failures_counts_only_the_latest_run_of_failures():
    status = pd.DataFrame({'pool_status': ['FAIL', 'OK', 'FAIL', 'FAIL']})
    assert consecutive_failures(status, 'pool_status') == 2
    assert consecutive_failures(pd.DataFrame({'pool_status': ['FAIL', 'OK']}), 'pool_status') == 0
