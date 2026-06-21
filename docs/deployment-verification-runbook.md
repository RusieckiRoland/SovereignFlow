# Stage 3 verification runbook

## Install development dependencies

```bash
python -m pip install -e ".[dev]"
python -m playwright install chromium
```

## Start real infrastructure

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

Recreate Keycloak after changing the realm:

```bash
docker compose --profile identity up -d --force-recreate keycloak
```

## Export integration settings

```bash
export SOVEREIGNFLOW_TEST_POSTGRES_URL='postgresql://sovereignflow:sovereignflow-test-password@127.0.0.1:25432/sovereignflow'
export SOVEREIGNFLOW_TEST_WEAVIATE_HOST='127.0.0.1'
export SOVEREIGNFLOW_TEST_WEAVIATE_HTTP_PORT=28080
export SOVEREIGNFLOW_TEST_WEAVIATE_GRPC_PORT=25005
export SOVEREIGNFLOW_TEST_WEAVIATE_API_KEY='sovereignflow-test-key'
export SOVEREIGNFLOW_TEST_KEYCLOAK_URL='http://127.0.0.1:28090'
```

## Run verification

```bash
python -m pytest --cov=sovereignflow --cov-branch
python -m pytest -m integration
python -m pytest -m e2e

cd generator
python -m pytest --cov=dataset_generator --cov-branch
```

Expected results:

- 100% statement and branch coverage for both Python projects;
- all real PostgreSQL, Weaviate, HTTP, JWKS, and Keycloak tests pass;
- browser login uses Authorization Code Flow with PKCE;
- `integration-user` receives the permitted capability;
- `restricted-user` receives an empty catalog;
- manual capability manipulation is rejected by the backend;
- policy version changes are visible without restarting SovereignFlow;
- evaluator requests use Bearer authentication and `capability_id`.

## Interactive observation

Run the VS Code configuration `SovereignFlow: OpenAI RAG demo`, then open:

```text
http://127.0.0.1:8000/app/
```

Use:

```text
integration-user / stage2-test-password
restricted-user  / stage2-test-password
```

The first user should see the configured capability. The second should see no
capability unless a policy is explicitly published for the `restricted` claim.
