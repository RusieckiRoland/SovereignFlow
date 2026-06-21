# Article 4 — Data Classification Levels

This Regulation establishes four classification levels for operational data. Operators must apply
the lowest level consistent with the actual sensitivity of the data. Over-classification is itself
a compliance failure.

### 4.1 PUBLIC

Data whose disclosure to the general public would cause no harm to operations, personnel, or
national security. Examples include published performance statistics, general service descriptions,
and regulatory filings that are a matter of public record.

Access: unrestricted. No ACL required.

### 4.2 INTERNAL

Data whose disclosure outside the operator's organisation would cause limited operational harm or
reputational damage. Examples include internal procedure documents, non-critical configuration
parameters, and general maintenance schedules.

Access: restricted to employees and authorised contractors of the operator. ACL must specify
at minimum the owning business unit (see Article 7).

### 4.3 RESTRICTED

Data whose unauthorised disclosure would cause significant harm to operations, financial position,
or the safety of personnel. Examples include detailed network topology, vulnerability assessments,
incident post-mortems, and supply chain contracts.

Access: restricted to personnel with a demonstrated operational need. ACL must specify named roles
or teams. Access grants must be reviewed at intervals not exceeding 12 months (see Article 7.3).

### 4.4 CLASSIFIED

Data whose disclosure would cause serious harm to national security, public safety, or critical
infrastructure resilience. Examples include threat intelligence reports, red team findings, and
detailed emergency response procedures.

Access: requires individual clearance granted by the Competent Authority. Handling must comply
with the physical and logical controls specified in Article 8. Classified data may not be stored
on systems shared with lower-classification data.

---
*Classification: PUBLIC*
*acl_labels: public*
*References: Article 3 (obligations), Article 7 (access control), Article 8 (classified handling)*
