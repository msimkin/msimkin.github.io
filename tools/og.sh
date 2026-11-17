#!/bin/sh
# Render tools/og.html to static/og.png (needs Google Chrome). Run from anywhere.
cd "$(dirname "$0")/.." || exit 1
/usr/bin/python3 -m http.server 8799 --bind 127.0.0.1 >/dev/null 2>&1 & S=$!
sleep 1; P=$(mktemp -d)
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --hide-scrollbars \
  --force-device-scale-factor=1 --virtual-time-budget=4000 --user-data-dir="$P" --window-size=1200,630 \
  --screenshot=static/og.png http://127.0.0.1:8799/tools/og.html >/dev/null 2>&1
rm -rf "$P"; kill $S
echo "wrote static/og.png"
