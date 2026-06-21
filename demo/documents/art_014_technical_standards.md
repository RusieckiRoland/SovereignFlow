# Article 14 — Technical Security Standards

### 14.1 Approved Standards

Technical controls implemented to meet the requirements of this Regulation must conform to
standards approved by the Competent Authority. The following standards are approved at the
time of publication; operators must monitor the approved list for updates:

- **Encryption at rest**: AES-256 or ChaCha20-Poly1305
- **Encryption in transit**: TLS 1.3; TLS 1.2 permissible only where a device constraint
  is documented and approved
- **Key management**: HSMs meeting FIPS 140-3 Level 3 or equivalent
- **Authentication**: FIDO2/WebAuthn for interactive sessions; X.509 certificates for
  service-to-service authentication
- **Network segmentation**: Zero Trust Architecture principles as defined in NIST SP 800-207
- **Vulnerability management**: patching of critical and high vulnerabilities within 30 days
  of publication of a fix by the vendor

### 14.2 Deviations

Where a technical constraint prevents the implementation of an approved standard, the operator
must document the constraint, the compensating control applied, and obtain written approval from
the Competent Authority before placing the non-compliant system in service.

### 14.3 Security Testing

Systems that store or process RESTRICTED or CLASSIFIED data must be subject to independent
security testing at intervals not exceeding 12 months. Testing must include:

- a vulnerability assessment of all network-exposed interfaces;
- a review of access control configurations against the ACL in the Data Governance Register;
- a test of encryption key rotation procedures.

Test reports are classified RESTRICTED by default and must be stored accordingly.

### 14.4 Software Supply Chain

Operators must maintain an inventory of all software components (including open source libraries)
used in systems that process RESTRICTED or CLASSIFIED data. The inventory must include version
numbers and known vulnerability status. Software components with unmitigated critical or high
vulnerabilities must not be deployed in such systems.

---
*Classification: RESTRICTED*
*acl_labels: restricted, security, technical*
*References: Article 4 (classification), Article 7.5 (technical enforcement), Article 8 (classified handling), Article 5 (risk assessment)*
