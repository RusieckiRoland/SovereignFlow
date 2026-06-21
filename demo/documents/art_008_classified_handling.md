# Article 8 — Handling of Classified Data

### 8.1 Physical Controls

Classified data in physical form must be stored in approved security containers meeting the
relevant national standard. Rooms in which classified data is processed must be access-controlled
to prevent entry by persons without current individual clearance. Entry and exit must be logged.

### 8.2 Logical Controls

Classified data in electronic form must be:

- encrypted at rest using an approved algorithm with a minimum key length of 256 bits;
- encrypted in transit using a protocol providing equivalent strength;
- stored on systems that are physically and logically isolated from systems processing data at
  a lower classification level;
- accessible only via authenticated sessions with multi-factor authentication.

Encryption keys for classified data must be stored separately from the data they protect, in
a hardware security module (HSM) approved by the Competent Authority.

### 8.3 Clearance Verification

Before granting access to classified data, the Data Custodian must verify that the individual
holds a current clearance issued or recognised by the Competent Authority. Clearances must be
renewed at intervals not exceeding 36 months. Expired clearances automatically void access.

### 8.4 Handling in Transit

Classified data may only be transmitted across networks approved for classified traffic. Transmission
over public networks is not permitted under any circumstances, including when the data is encrypted.
Physical transport of classified data must use tamper-evident packaging and a two-person rule.

### 8.5 Destruction

Classified data that has reached its retention end date must be destroyed in a manner that ensures
it cannot be recovered. Electronic media must be physically destroyed or degaussed using an
approved degausser. Paper copies must be cross-shredded or incinerated. Destruction must be
witnessed and recorded in the Data Governance Register.

---
*Classification: RESTRICTED*
*acl_labels: restricted, security*
*References: Article 4.4 (classified level), Article 7 (access control), Article 6 (register)*
