#!/usr/bin/env bash
set -euo pipefail
APP_ROOT=/opt/douyin-sparkflow
SUB_URL='https://liangxin.xyz/api/v1/liangxin?OwO=6981c5a8452d44e8521c78f9f7bf1eea'
curl -fsSL -A 'clash-verge/1.7.7' "$SUB_URL" -o "$APP_ROOT/proxy/config.yaml"
if grep -q '^allow-lan:' "$APP_ROOT/proxy/config.yaml"; then sed -i 's/^allow-lan:.*/allow-lan: true/' "$APP_ROOT/proxy/config.yaml"; else echo 'allow-lan: true' >> "$APP_ROOT/proxy/config.yaml"; fi
if grep -q '^bind-address:' "$APP_ROOT/proxy/config.yaml"; then sed -i "s#^bind-address:.*#bind-address: '*'#" "$APP_ROOT/proxy/config.yaml"; else echo "bind-address: '*'" >> "$APP_ROOT/proxy/config.yaml"; fi
if grep -q '^external-controller:' "$APP_ROOT/proxy/config.yaml"; then sed -i "s#^external-controller:.*#external-controller: '0.0.0.0:9090'#" "$APP_ROOT/proxy/config.yaml"; else echo "external-controller: '0.0.0.0:9090'" >> "$APP_ROOT/proxy/config.yaml"; fi
docker compose -f "$APP_ROOT/docker-compose.yml" restart proxy
