#!/bin/bash
# RANST deploy script — stuurt code naar de server en herstart de backend
# Gebruik: ./deploy.sh

SERVER="ranst@178.104.97.78"
KEY="$HOME/.ssh/ranst_hetzner"
REMOTE="/home/ranst/ranst-poc"

echo "→ Code uploaden..."
rsync -az \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.git' \
  --exclude='ranst-desktop/node_modules' \
  --exclude='ranst-desktop/dist' \
  --exclude='temp/' \
  --exclude='output/' \
  /Users/Shared/ranst-poc/ \
  $SERVER:$REMOTE/ \
  -e "ssh -i $KEY"

echo "→ Meetings DB uploaden..."
python3 -c "
import sqlite3
src = sqlite3.connect('output/meetings.db')
dst = sqlite3.connect('/tmp/meetings_deploy.db')
src.backup(dst)
dst.close(); src.close()
"
rsync -az /tmp/meetings_deploy.db $SERVER:$REMOTE/output/meetings.db -e "ssh -i $KEY"

echo "→ Demo-artikelen uploaden op server..."
ssh -i $KEY $SERVER "cd $REMOTE && python3 demo_seed.py 2>&1 | tail -5"

echo "→ Backend herstarten..."
ssh -i $KEY $SERVER "sudo systemctl restart ranst"

echo "✓ Deploy klaar — https://api.ranst.nl/app"
