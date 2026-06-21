# Article 7 — Access Control

### 7.1 Principle of Least Privilege

Access to operational data must be granted on the basis of operational necessity only. Personnel
must not be granted access to data at a higher classification level than required for their
current role. Access grants must be specific to datasets or categories of data; blanket grants
to all data of a given classification level are not permitted.

### 7.2 Access Control Lists

Each dataset classified as INTERNAL or above must have an Access Control List (ACL) that
enumerates:

- the organisational roles permitted to read the dataset;
- the organisational roles permitted to modify the dataset (if applicable);
- the organisational roles permitted to grant further access (i.e. delegate ACL authority);
- any external entities permitted access, with the contractual basis and expiry date.

ACLs must be reviewed at intervals not exceeding 12 months for INTERNAL data, 6 months for
RESTRICTED data, and 3 months for CLASSIFIED data.

### 7.3 Access Review Process

During each scheduled review, the Data Custodian must:

1. Verify that each listed principal still has an operational need for access.
2. Revoke access for any principal whose need has ceased, and record the revocation in the
   Data Governance Register (Article 6, Section B).
3. Confirm that the ACL continues to reflect the correct classification level.
4. Certify the review by signing the Register entry.

### 7.4 Emergency Access

Where operational continuity requires access to be granted outside the normal review cycle,
an Emergency Access Grant may be issued by a Data Custodian. Emergency grants:

- must not exceed 72 hours in duration;
- must be notified to the Data Custodian's line manager within 4 hours;
- must be logged in the Data Governance Register within 24 hours;
- must be reviewed and either formalised or revoked within 5 working days.

### 7.5 Technical Enforcement

ACLs must be enforced by technical controls. Reliance on procedural controls alone is not
acceptable for RESTRICTED or CLASSIFIED data. Technical controls must be audited at intervals
not exceeding 6 months to verify that they accurately reflect the current ACL.

---
*Classification: INTERNAL*
*acl_labels: internal, compliance, security*
*References: Article 2 (definitions), Article 4 (classification), Article 6 (register), Article 8 (classified)*
