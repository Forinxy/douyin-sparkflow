#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/halfwaystudent/douyin-sparkflow.git}"
BRANCH="${BRANCH:-main}"
APP_ROOT="${APP_ROOT:-/opt/douyin-sparkflow}"

if [ "$(id -u)" -ne 0 ]; then
  SUDO="sudo"
else
  SUDO=""
fi

run_root() {
  if [ -n "$SUDO" ]; then
    sudo "$@"
  else
    "$@"
  fi
}

install_base_tools() {
  if command -v curl >/dev/null 2>&1 && command -v git >/dev/null 2>&1 && command -v gpg >/dev/null 2>&1; then
    return
  fi
  if command -v apt-get >/dev/null 2>&1; then
    run_root apt-get update
    run_root apt-get install -y ca-certificates curl git gnupg
  elif command -v yum >/dev/null 2>&1; then
    run_root yum install -y ca-certificates curl git
  fi
}

install_docker_debian() {
  . /etc/os-release
  local docker_id="${ID}"
  if [ "$docker_id" = "debian" ] || [ "$docker_id" = "ubuntu" ]; then
    run_root install -m 0755 -d /etc/apt/keyrings
    curl -fsSL "https://download.docker.com/linux/${docker_id}/gpg" | run_root tee /etc/apt/keyrings/docker.asc >/dev/null
    run_root chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/${docker_id} ${VERSION_CODENAME} stable" | run_root tee /etc/apt/sources.list.d/docker.list >/dev/null
    run_root apt-get update
    run_root apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  else
    run_root apt-get install -y docker.io docker-compose-plugin
  fi
}

ensure_docker() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    return
  fi
  if command -v apt-get >/dev/null 2>&1; then
    install_docker_debian
  elif command -v yum >/dev/null 2>&1; then
    run_root yum install -y docker docker-compose-plugin
  else
    echo "Docker is not installed. Please install Docker with the Compose plugin first." >&2
    exit 1
  fi
  run_root systemctl enable --now docker || true
}

prepare_repo() {
  run_root mkdir -p "$(dirname "$APP_ROOT")"
  if [ -d "$APP_ROOT/.git" ]; then
    run_root git -C "$APP_ROOT" reset --hard
    run_root git -C "$APP_ROOT" fetch origin "$BRANCH"
    run_root git -C "$APP_ROOT" checkout -B "$BRANCH" "origin/$BRANCH"
    run_root git -C "$APP_ROOT" reset --hard "origin/$BRANCH"
  else
    run_root git clone --branch "$BRANCH" "$REPO_URL" "$APP_ROOT"
  fi
}

set_env_value() {
  local file="$1"
  local key="$2"
  local value="$3"
  if grep -q "^${key}=" "$file"; then
    local tmp_file
    tmp_file="$(mktemp)"
    awk -v key="$key" -v value="$value" '
      BEGIN { replaced = 0 }
      $0 ~ "^" key "=" { print key "=" value; replaced = 1; next }
      { print }
      END { if (!replaced) print key "=" value }
    ' "$file" > "$tmp_file"
    run_root cp "$tmp_file" "$file"
    rm -f "$tmp_file"
  else
    printf '%s=%s\n' "$key" "$value" | run_root tee -a "$file" >/dev/null
  fi
}

prepare_runtime_files() {
  if [ ! -f "$APP_ROOT/.env" ]; then
    run_root cp "$APP_ROOT/.env.example" "$APP_ROOT/.env"
  fi
  set_env_value "$APP_ROOT/.env" "APP_ROOT" "$APP_ROOT"
  for key in TZ WEB_PORT LOGIN_DESKTOP_WEB_PORT PROXY_HTTP_PORT PROXY_CONTROLLER_PORT PROXY_SUB_URL PLAYWRIGHT_BASE_IMAGE HTTP_PROXY_BUILD HTTPS_PROXY_BUILD ALL_PROXY_BUILD; do
    if [ -n "${!key:-}" ]; then
      set_env_value "$APP_ROOT/.env" "$key" "${!key}"
    fi
  done

  local current_sub
  current_sub="$(grep '^PROXY_SUB_URL=' "$APP_ROOT/.env" | sed 's/^PROXY_SUB_URL=//' || true)"
  if [ -z "$current_sub" ] && [ -t 0 ]; then
    printf 'Proxy subscription URL, optional and hidden: '
    read -r -s input_sub || true
    printf '\n'
    if [ -n "${input_sub:-}" ]; then
      set_env_value "$APP_ROOT/.env" "PROXY_SUB_URL" "$input_sub"
    fi
  fi

  run_root mkdir -p "$APP_ROOT/proxy" "$APP_ROOT/state/cron" "$APP_ROOT/state/login-profile" "$APP_ROOT/DouYinSparkFlow/logs"
  if [ ! -f "$APP_ROOT/proxy/config.yaml" ]; then
    run_root cp "$APP_ROOT/proxy/config.example.yaml" "$APP_ROOT/proxy/config.yaml"
  fi
}

main() {
  install_base_tools
  ensure_docker
  prepare_repo
  prepare_runtime_files
  cd "$APP_ROOT"
  run_root bash "$APP_ROOT/refresh_proxy.sh"
  run_root env DOCKER_BUILDKIT=1 COMPOSE_DOCKER_CLI_BUILD=1 docker compose up -d --build

  local web_port login_port host_ip
  web_port="$(grep '^WEB_PORT=' "$APP_ROOT/.env" | sed 's/^WEB_PORT=//')"
  login_port="$(grep '^LOGIN_DESKTOP_WEB_PORT=' "$APP_ROOT/.env" | sed 's/^LOGIN_DESKTOP_WEB_PORT=//')"
  host_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  host_ip="${host_ip:-127.0.0.1}"
  echo
  echo "Douyin SparkFlow is running."
  echo "Web UI: http://${host_ip}:${web_port:-8787}"
  echo "Login desktop: http://${host_ip}:${login_port:-8788}/vnc.html?autoconnect=1&resize=scale&view_only=0"
  echo "Next: create the admin password, open the login desktop, scan the QR code, select target friends, and set the send window."
}

main "$@"
