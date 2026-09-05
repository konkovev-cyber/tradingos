#!/bin/bash
# Auto-backup critical trading state files.
# Keeps 24 most recent backups per file (each ~6h = ~6 days).
set -e

BACKUP_DIR="/root/tradingos/backups"
KEEP=24
TS=$(date +%Y%m%d_%H%M%S)

FILES=(
    "/root/tradingos/guardian/reality_state.json"
    "/root/tradingos/guardian/bingx_state.json"
    "/root/tradingos/tradingos_state/system_snapshot.json"
    "/root/tradingos/tradingos_control_state.json"
    "/root/tradingos/control_plane/snapshots/current_state.json"
)

for src in "${FILES[@]}"; do
    if [[ ! -f "$src" ]]; then
        continue
    fi
    name=$(basename "$src" .json)
    dest="${BACKUP_DIR}/${name}_${TS}.json"
    cp -p "$src" "$dest"
    # Prune old backups for this file
    ls -1t "${BACKUP_DIR}/${name}_"*.json 2>/dev/null | tail -n +$((KEEP+1)) | xargs -r rm -f
done

# Sanity-check: verify backup JSON is valid
for f in "${BACKUP_DIR}"/*_${TS}.json; do
    python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$f" || {
        echo "BAD BACKUP: $f" >&2
        rm -f "$f"
    }
done

echo "[$(date -Iseconds)] backup ok: $(ls -1 ${BACKUP_DIR}/*_${TS}.json 2>/dev/null | wc -l) files"
