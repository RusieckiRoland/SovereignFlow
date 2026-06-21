#!/usr/bin/env bash
# Idempotent — safe to run on every deploy.
set -euo pipefail

INSTALL_DIR="$(cd "$(dirname "$0")/../.." && pwd)"

sudo mkdir -p /var/backups/sovereignflow
sudo mkdir -p /var/lib/sovereignflow/applied-changes/sf
sudo mkdir -p /var/lib/sovereignflow/applied-changes/domain

sudo cp "$INSTALL_DIR/scripts/sf/backup.sh" /usr/local/bin/sovereignflow-backup
sudo chmod +x /usr/local/bin/sovereignflow-backup

sudo tee /etc/cron.d/sovereignflow-backup > /dev/null <<'EOF'
0 2 * * * root /usr/local/bin/sovereignflow-backup >> /var/log/sovereignflow-backup.log 2>&1
EOF

sudo tee /etc/systemd/system/sovereignflow-healthcheck.service > /dev/null <<'EOF'
[Unit]
Description=SovereignFlow health check

[Service]
Type=oneshot
ExecStart=/bin/bash -c 'curl -sf http://127.0.0.1:8000/ready || (echo "$(date): /ready failed" >> /var/log/sovereignflow-health.log && systemctl restart sovereignflow)'
EOF

sudo tee /etc/systemd/system/sovereignflow-healthcheck.timer > /dev/null <<'EOF'
[Unit]
Description=SovereignFlow health check every 5 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now sovereignflow-healthcheck.timer

echo "Setup complete."
