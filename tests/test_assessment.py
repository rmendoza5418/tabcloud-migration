from assessment.cloud_compatibility import check_bridge_dependencies
from models import AssetRecord, ConnectionInfo, ContentType, IssueSeverity


def asset(host, connection_type="sqlserver", size_bytes=None):
    return AssetRecord(
        content_type=ContentType.WORKBOOK,
        luid="luid-1",
        name="Test Workbook",
        project_name="Finance",
        project_luid="proj-1",
        owner_name="analyst",
        owner_email="analyst@example.com",
        created_at=None,
        updated_at=None,
        last_accessed=None,
        size_bytes=size_bytes,
        connections=[ConnectionInfo(
            server_address=host, server_port=None, connection_type=connection_type,
            database_name="db", username=None,
        )],
    )


def test_on_prem_live_connection_is_a_blocker():
    issues = check_bridge_dependencies([asset("db01.sqlserver.internal")], ["sqlserver.internal"])
    assert len(issues) == 1
    assert issues[0].severity == IssueSeverity.BLOCKER


def test_extract_connection_is_not_flagged():
    issues = check_bridge_dependencies([asset("db01.sqlserver.internal", "hyper")], ["sqlserver.internal"])
    assert issues == []


def test_cloud_host_is_not_flagged():
    issues = check_bridge_dependencies([asset("warehouse.snowflakecomputing.com")], ["sqlserver.internal"])
    assert issues == []


def test_host_match_is_case_insensitive():
    issues = check_bridge_dependencies([asset("DB01.SQLSERVER.INTERNAL")], ["SqlServer.Internal"])
    assert len(issues) == 1


def test_size_mb_and_blocker_properties():
    a = asset("db01.sqlserver.internal", size_bytes=104_857_600)
    assert a.size_mb == 100.0
    assert not a.has_blockers
    a.compatibility_issues = check_bridge_dependencies([a], ["sqlserver.internal"])
    assert a.has_blockers
    assert a.blocker_count == 1
