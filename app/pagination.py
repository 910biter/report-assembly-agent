def page_ids(ids: list[int], *, page: int, page_size: int) -> tuple[list[int], int]:
    start = (page - 1) * page_size
    return ids[start:start + page_size], len(ids)
