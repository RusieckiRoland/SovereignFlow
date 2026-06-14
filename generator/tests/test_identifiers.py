from dataset_generator.identifiers import (
    chunk_id,
    cluster_name,
    domain_name,
    source_id,
    tenant_id,
    type_label,
    version_id,
)


def test_identifiers_are_stable_and_readable() -> None:
    assert domain_name(0) == "Orders_000001"
    assert domain_name(8) == "Orders_000002"
    assert cluster_name(1) == "invoices"
    assert type_label("log_table") == "LogTable"
    assert tenant_id(3, 2) == "tenant_0002"
    assert version_id(3) == "v3"
    assert source_id("Orders_000001", "log_table") == "Orders_000001_LogTable"
    assert chunk_id("Orders_000001", "service", 2, 3) == ("Orders_000001_Service_V0003_0002")
