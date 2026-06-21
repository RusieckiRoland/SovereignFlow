# Article 10 — Incident Response

### 10.1 Incident Classification

Incidents are classified by their confirmed or probable impact on operational data:

**Tier 1 — Limited**: Unauthorised access to or loss of INTERNAL data affecting fewer than
500 records. No known onward disclosure. Full containment within 24 hours.

**Tier 2 — Significant**: Unauthorised access to RESTRICTED data at any scale; or INTERNAL data
affecting more than 500 records; or any incident where containment was not achieved within 24 hours.

**Tier 3 — Critical**: Unauthorised access to CLASSIFIED data at any scale; or any incident
believed to have resulted in onward disclosure to a foreign intelligence service or hostile actor;
or any incident causing significant disruption to critical infrastructure operations.

### 10.2 Response Timelines

| Tier | Initial notification to CA | Full report to CA | Post-incident review |
|------|--------------------------|-------------------|----------------------|
| 1    | 5 working days           | 30 days           | 90 days              |
| 2    | 72 hours                 | 14 days           | 60 days              |
| 3    | 4 hours                  | 7 days            | 30 days              |

Notification under Article 3.4 satisfies the initial notification requirement for Tier 2 incidents.

### 10.3 Incident Command

For Tier 2 and Tier 3 incidents, the operator must establish an Incident Command function with
a named Incident Commander who has authority to direct all aspects of the response. The Incident
Commander must have completed specialist incident response training within the preceding 36 months.

### 10.4 Full Incident Report

The Full Incident Report required under Article 10.2 must include:

- a timeline of events from first indicators to full containment;
- the data classification levels and volumes affected;
- the root cause analysis;
- a description of all containment, eradication, and recovery actions taken;
- an assessment of whether the incident arose from a failure of controls required by this
  Regulation, and if so, which controls and why;
- remediation actions planned or completed.

### 10.5 Incident Log

All incidents, regardless of tier, must be entered in the Incident Log (Data Governance Register,
Section D) within 48 hours of detection.

---
*Classification: RESTRICTED*
*acl_labels: restricted, security, compliance*
*References: Article 3.4 (notification), Article 6 Section D (incident log), Article 4 (classification)*
