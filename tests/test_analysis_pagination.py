from app.pagination import page_ids


def test_page_ids_preserves_task_order_and_reports_total():
    ids = [9, 2, 7, 4, 1]
    assert page_ids(ids, page=2, page_size=2) == ([7, 4], 5)
    assert page_ids(ids, page=4, page_size=2) == ([], 5)


def test_page_ids_does_not_mutate_source():
    ids = [3, 1]
    assert page_ids(ids, page=1, page_size=1) == ([3], 2)
    assert ids == [3, 1]
