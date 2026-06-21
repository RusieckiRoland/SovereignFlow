# SovereignFlow — Production VPS Deployment

## Target Environment

- VPS: OVH VPS-2 2026, Ubuntu 26.04, 6 vCores, 12 GB RAM, 100 GB disk
- IP: `57.128.245.201`
- Application: `https://app.taricai.com`
- Keycloak: `https://auth.taricai.com`

## Architecture

```
Internet (443/80)
       │
    nginx                ← SSL termination, reverse proxy
       │
       ├── app.taricai.com   → SovereignFlow :8000  (systemd)
       └── auth.taricai.com  → Keycloak :28090      (Docker)

localhost only:
  ├── Postgres  :5432    (Docker)
  ├── Weaviate  :8080    (Docker)
  └── Ollama    :11434   (systemd, nomic-embed-text embeddings)

External:
  └── OpenAI API         ← LLM (gpt-4o-mini)
```

Public ports: **22, 80, 443 only**.

---

## Prerequisites

- [x] DNS: `app.taricai.com` A `57.128.245.201`
- [x] DNS: `auth.taricai.com` A `57.128.245.201`
- [ ] OpenAI API key
- [ ] SSH access to VPS as `ubuntu`

---

## Step 1 — System

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git python3-venv python3-pip docker.io docker-compose-v2 nginx certbot python3-certbot-nginx ufw
sudo usermod -aG docker ubuntu
newgrp docker
```

Firewall:
```bash
sudo ufw allow 22
sudo ufw allow 80
sudo ufw allow 443
sudo ufw enable
```

---

## Step 2 — Ollama (embeddings)

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

After installation completes, pull the embedding model:

```bash
ollama pull nomic-embed-text
```

Verify:
```bash
curl http://localhost:11434/api/tags
```

---

## Step 3 — Repository and Python Environment

Generate a deploy key on the server:
```bash
ssh-keygen -t ed25519 -C "sovereignflow-vps" -f ~/.ssh/github_deploy -N ""
cat ~/.ssh/github_deploy.pub
```

Add the printed public key to the repository:
**GitHub → Repository → Settings → Deploy keys → Add deploy key**
- Title: `sovereignflow-vps`
- Allow write access: No

Configure SSH to use the deploy key and trust GitHub's host key:
```bash
mkdir -p ~/.ssh
cat >> ~/.ssh/config <<'EOF'
Host github.com
  IdentityFile ~/.ssh/github_deploy
EOF
ssh-keyscan github.com >> ~/.ssh/known_hosts
```

Verify authentication works:
```bash
ssh -T git@github.com
```

Expected output: `Hi <user>/<repo>! You've successfully authenticated...`

Clone and set up the environment:
```bash
sudo mkdir /opt/sovereignflow
sudo chown ubuntu:ubuntu /opt/sovereignflow
git clone git@github.com:<org>/<repo>.git /opt/sovereignflow
cd /opt/sovereignflow
python3 -m venv .venv
.venv/bin/pip install -e .
```

System user (no shell, no sudo):
```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin sovereignflow
sudo chown -R sovereignflow:sovereignflow /opt/sovereignflow
```

---

## Step 4 — Secrets

Generate three random keys (postgres password, weaviate key, admin key):
```bash
openssl rand -hex 32
openssl rand -hex 32
openssl rand -hex 32
```

Save the output — you will need all three values in the next command.

```bash
sudo mkdir /etc/sovereignflow
sudo tee /etc/sovereignflow/.env <<EOF
SF_POSTGRES_URL=postgresql://sovereignflow:STRONG_PASSWORD@127.0.0.1:5432/sovereignflow
SF_POSTGRES_PASSWORD=STRONG_PASSWORD
SF_WEAVIATE_API_KEY=RANDOM_KEY_32_CHARS
SF_ADMIN_API_KEY=RANDOM_KEY_32_CHARS
SF_KEYCLOAK_ADMIN_PASSWORD=STRONG_PASSWORD_KEYCLOAK
OPENAI_API_KEY=sk-...
SOVEREIGNFLOW_POSTGRES_PORT=5432
SOVEREIGNFLOW_WEAVIATE_HTTP_PORT=8080
SOVEREIGNFLOW_WEAVIATE_GRPC_PORT=50051
SOVEREIGNFLOW_KEYCLOAK_PORT=28090
EOF
sudo chmod 600 /etc/sovereignflow/.env
sudo chown root:sovereignflow /etc/sovereignflow/.env
```

---

## Step 5 — Docker Compose (data services)

```bash
cd /opt/sovereignflow
docker compose --env-file /etc/sovereignflow/.env --profile identity up -d
```

Verify:
```bash
docker compose --env-file /etc/sovereignflow/.env ps
```

---

## Step 6 — Production Configuration

File `config/sovereignflow.prod.yaml`:

```yaml
server:
  host: 127.0.0.1
  port: 8000
  threads: 4

postgresql:
  connection_url_env: SF_POSTGRES_URL
  timeout_seconds: 10

admin:
  api_key_env: SF_ADMIN_API_KEY

identity_provider:
  issuer: https://auth.taricai.com/realms/sovereignflow
  audience: sovereignflow-api
  jwks_url: https://auth.taricai.com/realms/sovereignflow/protocol/openid-connect/certs
  algorithms: [RS256]
  timeout_seconds: 10
  cache_ttl_seconds: 300
  tenant_claim: tenant_id
  roles_claim: roles
  groups_claim: groups
  acl_claim: acl_labels
  clearance_claim: clearance_label
  classification_labels_claim: classification_labels
  external_model_claim: allow_external_model
  diagnostic_claim: sovereignflow_diagnostics

web_client:
  client_id: sovereignflow-web-client
  authorization_url: https://auth.taricai.com/realms/sovereignflow/protocol/openid-connect/auth
  token_url: https://auth.taricai.com/realms/sovereignflow/protocol/openid-connect/token
  logout_url: https://auth.taricai.com/realms/sovereignflow/protocol/openid-connect/logout

model_servers:
  - id: default-model
    trust_boundary: external
    base_url: https://api.openai.com/v1
    model: gpt-4o-mini
    api_key_env: OPENAI_API_KEY
    timeout_seconds: 120
    input_cost_per_million: 0.15
    output_cost_per_million: 0.60
    security_profile:
      kind: none

embeddings:
  name: ollama-embeddings
  base_url: http://127.0.0.1:11434/v1
  model: nomic-embed-text
  timeout_seconds: 60

weaviate:
  host: 127.0.0.1
  http_port: 8080
  grpc_port: 50051
  secure: false
  api_key_env: SF_WEAVIATE_API_KEY

prompts_root: ../prompts/general
pipelines_root: ../pipelines
domains:
  - domains/general.yaml
  - domains/orders.yaml
```

---

## Step 7 — Data Import

Migrations run automatically on application startup. Import the demo dataset:

```bash
cd /opt/sovereignflow
source /etc/sovereignflow/.env
sudo -u sovereignflow .venv/bin/sovereignflow-import \
  --dataset dataset/orders/ \
  --config config/sovereignflow.prod.yaml
```

---

## Step 8 — systemd Service

```bash
sudo tee /etc/systemd/system/sovereignflow.service <<'EOF'
[Unit]
Description=SovereignFlow API
After=network.target docker.service ollama.service

[Service]
Type=simple
User=sovereignflow
WorkingDirectory=/opt/sovereignflow
EnvironmentFile=/etc/sovereignflow/.env
ExecStart=/opt/sovereignflow/.venv/bin/python -m sovereignflow --config config/sovereignflow.prod.yaml
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now sovereignflow
```

Verify:
```bash
sudo systemctl status sovereignflow
sudo journalctl -u sovereignflow -n 50
```

---

## Step 9 — nginx + SSL

First, create a temporary HTTP-only config so nginx starts and certbot can perform domain validation:

```bash
sudo tee /etc/nginx/sites-available/sovereignflow <<'EOF'
server {
    listen 80;
    server_name app.taricai.com auth.taricai.com;
}
EOF

sudo ln -s /etc/nginx/sites-available/sovereignflow /etc/nginx/sites-enabled/
sudo systemctl start nginx
```

Obtain SSL certificates:

```bash
sudo certbot certonly --nginx \
  -d app.taricai.com \
  -d auth.taricai.com \
  --non-interactive --agree-tos --email YOUR_EMAIL
```

Replace config with the full SSL + proxy config:

```bash
sudo tee /etc/nginx/sites-available/sovereignflow <<'EOF'
server {
    listen 80;
    server_name app.taricai.com auth.taricai.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name app.taricai.com;

    ssl_certificate /etc/letsencrypt/live/app.taricai.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/app.taricai.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
}

server {
    listen 443 ssl;
    server_name auth.taricai.com;

    ssl_certificate /etc/letsencrypt/live/auth.taricai.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/auth.taricai.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:28090;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffer_size 128k;
        proxy_buffers 4 256k;
    }
}
EOF

sudo nginx -t && sudo systemctl reload nginx
```

---

## Step 10 — Verification

```bash
curl https://app.taricai.com/ready
systemctl status sovereignflow
journalctl -u sovereignflow -f
```

Application available at: **https://app.taricai.com/app/**

---

## Troubleshooting

### Keycloak configuration changes are not applied after restart

`docker compose restart` stops and starts the existing container — it does **not** recreate it with updated environment variables. Always use `up -d` to apply configuration changes:

```bash
docker compose --env-file /etc/sovereignflow/.env --profile identity up -d keycloak
```

### Keycloak generates HTTP links behind HTTPS proxy

Keycloak must know it is behind a TLS-terminating proxy. The `docker-compose.yml` sets:
- `KC_PROXY_HEADERS: xforwarded` — trust `X-Forwarded-Proto` from nginx
- `KC_HOSTNAME: https://auth.taricai.com` — force HTTPS in all generated URLs

If these are missing or not applied (e.g. after a `restart` instead of `up -d`), Keycloak will generate `http://` action URLs and the browser will warn about an insecure form.

### nginx fails to start — SSL certificate not found

Do not write the SSL nginx config before obtaining certificates. Certbot cannot run if nginx fails its own config test. Follow the order in Step 9: HTTP-only config → `certbot certonly` → full SSL config.
