from __future__ import annotations

DOMAIN_FAMILIES = (
    "Orders",
    "Invoices",
    "Shipping",
    "Payments",
    "Customers",
    "Borders",
    "Preorders",
    "OrderHistory",
)

NODE_TYPES = (
    "controller",
    "service",
    "repository",
    "table",
    "log_table",
    "config",
    "worker",
    "event",
    "command",
    "validator",
)


def domain_name(domain_index: int) -> str:
    family = DOMAIN_FAMILIES[domain_index % len(DOMAIN_FAMILIES)]
    family_ordinal = domain_index // len(DOMAIN_FAMILIES) + 1
    return f"{family}_{family_ordinal:06d}"


def cluster_name(domain_index: int) -> str:
    return DOMAIN_FAMILIES[domain_index % len(DOMAIN_FAMILIES)].lower()


def tenant_id(domain_index: int, tenant_count: int) -> str:
    return f"tenant_{domain_index % tenant_count + 1:04d}"


def version_id(version: int) -> str:
    return f"v{version}"


def source_id(domain: str, node_type: str) -> str:
    return f"{domain}_{type_label(node_type)}"


def chunk_id(domain: str, node_type: str, instance: int, version: int = 1) -> str:
    return f"{source_id(domain, node_type)}_V{version:04d}_{instance:04d}"


def type_label(node_type: str) -> str:
    return "".join(part.capitalize() for part in node_type.split("_"))
