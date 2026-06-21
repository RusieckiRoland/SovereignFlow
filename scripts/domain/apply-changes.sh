#!/usr/bin/env bash
# Runs each script in scripts/domain/changes/ exactly once.
# Applied scripts are tracked in /var/lib/sovereignflow/applied-changes/domain/.
# If an applied script is modified, the run fails — create a new change script instead.
set -euo pipefail

CHANGES_DIR="$(cd "$(dirname "$0")/changes" && pwd)"
APPLIED_DIR="/var/lib/sovereignflow/applied-changes/domain"

sudo mkdir -p "$APPLIED_DIR"

for script in "$CHANGES_DIR"/[0-9]*.sh; do
    [ -f "$script" ] || continue
    name=$(basename "$script")
    marker="$APPLIED_DIR/$name.done"
    hash_file="$APPLIED_DIR/$name.sha256"
    current_hash=$(sha256sum "$script" | cut -d' ' -f1)

    if [ -f "$marker" ]; then
        stored_hash=$(cat "$hash_file" 2>/dev/null || echo "")
        if [ "$stored_hash" != "$current_hash" ]; then
            echo "ERROR: $name was modified after being applied (hash mismatch)."
            echo "  stored:  $stored_hash"
            echo "  current: $current_hash"
            echo "Create a new change script instead of modifying an applied one."
            exit 1
        fi
        echo "skip: $name"
        continue
    fi

    echo "apply: $name"
    bash "$script"
    echo "$current_hash" | sudo tee "$hash_file" > /dev/null
    sudo touch "$marker"
    echo "done: $name"
done

echo "All domain changes applied."
