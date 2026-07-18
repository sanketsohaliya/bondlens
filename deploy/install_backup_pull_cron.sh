#!/usr/bin/env bash
# Install a local WSL cron entry that pulls the VPS weekly backup to the
# OneDrive-synced Desktop (offsite copy). Idempotent.
set -euo pipefail

DEST='/mnt/c/Users/sanket.patel/OneDrive - FE fundinfo/Desktop/bondlens.dump'
LOG=/home/sanket/code/BondLens/logs/backup_pull.log
LINE="0 9 * * 6 scp -q bondlens-vps:backups/bondlens.dump \"$DEST\" >> $LOG 2>&1"

mkdir -p "$(dirname "$LOG")"
if crontab -l 2>/dev/null | grep -qF 'bondlens-vps:backups/bondlens.dump'; then
    echo "cron entry already installed"
else
    (
        crontab -l 2>/dev/null
        echo '# Pull the VPS weekly backup (made Sat 08:00 IST) for an offsite OneDrive copy'
        echo "$LINE"
    ) | crontab -
    echo "cron entry installed"
fi
crontab -l | tail -2
