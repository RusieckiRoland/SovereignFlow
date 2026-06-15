# Keycloak integration

SovereignFlow remains provider-neutral. Keycloak is an optional development and
integration-test Identity Provider implementing the standard OIDC/JWT contract.
The domain and application layers do not import Keycloak libraries.

## Start the development realm

```bash
export WEAVIATE_API_KEY='sovereignflow-test-key'
export KEYCLOAK_ADMIN_PASSWORD='sovereignflow-admin-test'
export SOVEREIGNFLOW_KEYCLOAK_PORT=28090
docker compose --profile identity up -d keycloak
```

The imported realm is available at:

```text
http://127.0.0.1:28090/realms/sovereignflow
```

The realm definition is stored in:

```text
infra/keycloak/sovereignflow-realm.json
```

It contains development-only users and passwords. They must never be reused in
production.

## SovereignFlow OIDC configuration

```yaml
identity_provider:
  issuer: http://127.0.0.1:28090/realms/sovereignflow
  audience: sovereignflow-api
  jwks_url: http://127.0.0.1:28090/realms/sovereignflow/protocol/openid-connect/certs
  algorithms: [RS256]
  timeout_seconds: 5
  cache_ttl_seconds: 300
  tenant_claim: tenant_id
  roles_claim: roles
  groups_claim: groups
  acl_claim: acl_labels
  classification_claim: max_classification_level
  external_model_claim: allow_external_model
  diagnostic_claim: sovereignflow_diagnostics
```

## Obtain a development token

```bash
curl --fail-with-body \
  -X POST \
  'http://127.0.0.1:28090/realms/sovereignflow/protocol/openid-connect/token' \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode 'grant_type=password' \
  --data-urlencode 'client_id=sovereignflow-integration-client' \
  --data-urlencode 'username=integration-user' \
  --data-urlencode 'password=stage2-test-password'
```

The integration client enables the password grant only to support automated local
tests. A web or mobile application should use Authorization Code Flow with PKCE.

## Use the interactive test console

The realm also contains the public client:

```text
sovereignflow-web-client
```

This client:

- enables Authorization Code Flow;
- requires PKCE with SHA-256;
- disables the password grant;
- accepts the local redirect URI `http://127.0.0.1:8000/app/`;
- issues access tokens with the `sovereignflow-api` audience and SovereignFlow
  authorization claims.

After changing the imported realm file, recreate the development Keycloak
container so that the updated realm is imported:

```bash
export WEAVIATE_API_KEY='sovereignflow-test-key'
export KEYCLOAK_ADMIN_PASSWORD='sovereignflow-admin-test'
export SOVEREIGNFLOW_KEYCLOAK_PORT=28090
docker compose --profile identity up -d --force-recreate keycloak
```

Start SovereignFlow with a configuration containing the `web_client` section,
then open:

```text
http://127.0.0.1:8000/app/
```

Select **Sign in** and use either development account:

```text
integration-user / stage2-test-password
restricted-user  / stage2-test-password
```

The first account can use the configured external model and request diagnostics.
The restricted account maps to a different Identity Provider group and receives
an empty capability catalog until an explicit SovereignFlow policy grants access.

## Test users

`integration-user` has:

- tenant `tenant_0001`;
- ACL labels `public`, `internal`, and `restricted`;
- classification ceiling `3`;
- external-model permission;
- diagnostic permission.
- Identity Provider group `integration`.

`restricted-user` has:

- tenant `tenant_0001`;
- ACL label `public`;
- classification ceiling `1`;
- no external-model permission;
- no diagnostic permission.
- Identity Provider group `restricted`.

## Run the real Keycloak integration test

Start PostgreSQL, Weaviate, and Keycloak:

```bash
export POSTGRES_PASSWORD='sovereignflow-test-password'
export WEAVIATE_API_KEY='sovereignflow-test-key'
export KEYCLOAK_ADMIN_PASSWORD='sovereignflow-admin-test'
export SOVEREIGNFLOW_POSTGRES_PORT=25432
export SOVEREIGNFLOW_WEAVIATE_HTTP_PORT=28080
export SOVEREIGNFLOW_WEAVIATE_GRPC_PORT=25005
export SOVEREIGNFLOW_KEYCLOAK_PORT=28090
docker compose --profile identity up -d postgres weaviate keycloak
```

Run the test:

```bash
export SOVEREIGNFLOW_TEST_POSTGRES_URL='postgresql://sovereignflow:sovereignflow-test-password@127.0.0.1:25432/sovereignflow'
export SOVEREIGNFLOW_TEST_WEAVIATE_HOST='127.0.0.1'
export SOVEREIGNFLOW_TEST_WEAVIATE_HTTP_PORT=28080
export SOVEREIGNFLOW_TEST_WEAVIATE_GRPC_PORT=25005
export SOVEREIGNFLOW_TEST_WEAVIATE_API_KEY='sovereignflow-test-key'
export SOVEREIGNFLOW_TEST_KEYCLOAK_URL='http://127.0.0.1:28090'
python -m pytest tests/test_keycloak_integration.py
```

The test obtains real Keycloak tokens, validates them through JWKS, maps claims to
internal SovereignFlow groups, verifies different capability catalogs, executes a
real HTTP SovereignFlow request, queries real Weaviate and PostgreSQL
infrastructure, and confirms fail-closed denial for the restricted user.

The browser E2E test additionally executes Authorization Code Flow with PKCE
through the real Keycloak login page:

```bash
python -m playwright install chromium
python -m pytest tests/test_web_keycloak_e2e.py
```
