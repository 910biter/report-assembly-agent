import pytest

from app.auth.ownership import ownership_query


@pytest.mark.parametrize("kind", [
    "task", "material", "template", "report", "report_version", "thread",
    "comparison", "report_delta", "proposal", "notification",
])
def test_every_root_lookup_binds_owner(kind):
    query = ownership_query(kind, "resource-id", 17)
    sql = str(query.compile(compile_kwargs={"literal_binds": True})).lower()
    assert "owner_user_id = 17" in sql


def test_unknown_resource_kind_fails_closed():
    with pytest.raises(ValueError, match="UNKNOWN_RESOURCE_KIND"):
        ownership_query("anything", 42, 17)
