#!/usr/bin/env bash

set -Eeuo pipefail

readonly TELEGRAM_API_BASE="${TELEGRAM_API_BASE:-https://api.telegram.org}"
readonly TELEGRAM_MAX_MESSAGE_LENGTH="${TELEGRAM_MAX_MESSAGE_LENGTH:-4000}"
readonly TELEGRAM_WATCH_INTERVAL_SECONDS="${TELEGRAM_WATCH_INTERVAL_SECONDS:-5}"

declare -a WATCHER_PIDS=()

log() {
  printf '[telegram-log-sidecar] %s\n' "$*" >&2
}

trim() {
  local value="$1"

  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"

  printf '%s' "$value"
}

strip_ansi() {
  printf '%s' "$1" | tr -d '\r' | sed -E $'s/\x1B\\[[0-9;?]*[ -/]*[@-~]//g'
}

resolve_compose_project() {
  if [[ -n "${COMPOSE_PROJECT_NAME:-}" ]]; then
    printf '%s' "$COMPOSE_PROJECT_NAME"
    return 0
  fi

  docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}' "$HOSTNAME" 2>/dev/null | tr -d '\r'
}

wait_for_compose_project() {
  local project_name=""

  while true; do
    project_name="$(resolve_compose_project || true)"

    if [[ -n "$project_name" && "$project_name" != "<no value>" ]]; then
      printf '%s' "$project_name"
      return 0
    fi

    log "Waiting for Docker Compose project label on container ${HOSTNAME}..."
    sleep "$TELEGRAM_WATCH_INTERVAL_SECONDS"
  done
}

normalize_service_key() {
  printf '%s' "$1" | tr '[:lower:]-' '[:upper:]_'
}

get_service_regex() {
  local kind="$1"
  local service="$2"
  local global_var="TELEGRAM_LOG_${kind}_REGEX"
  local service_var="TELEGRAM_LOG_${kind}_REGEX_$(normalize_service_key "$service")"
  local value="${!service_var:-}"

  if [[ -n "$value" ]]; then
    printf '%s' "$value"
    return 0
  fi

  printf '%s' "${!global_var:-}"
}

matches_regex() {
  local regex="$1"
  local text="$2"

  if [[ -z "$regex" ]]; then
    return 0
  fi

  REGEX="$regex" TEXT="$text" perl -e '
    use strict;
    use warnings;

    my $regex = $ENV{REGEX};
    my $text = $ENV{TEXT};

    my $matched = eval { $text =~ /$regex/ ? 1 : 0 };

    if ($@) {
      print STDERR "Invalid regex: $regex => $@";
      exit 2;
    }

    exit($matched ? 0 : 1);
  '
}

find_service_container() {
  local project_name="$1"
  local service="$2"

  docker ps -q \
    --filter "label=com.docker.compose.project=${project_name}" \
    --filter "label=com.docker.compose.service=${service}" \
    --filter "status=running" | head -n 1
}

send_telegram_request() {
  local text="$1"
  local response_file="$2"

  curl -sS -o "$response_file" -w '%{http_code}' \
    --request POST "${TELEGRAM_API_BASE}/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
    --data-urlencode "text=${text}" \
    --data-urlencode "disable_web_page_preview=true"
}

send_telegram_message() {
  local text="$1"
  local response_file
  local status_code
  local retry_after

  response_file="$(mktemp)"

  status_code="$(send_telegram_request "$text" "$response_file" || true)"

  if [[ ! "$status_code" =~ ^[0-9]{3}$ ]]; then
    log "Failed to reach Telegram API while sending a log line."
    rm -f "$response_file"
    return 1
  fi

  if [[ "$status_code" == "200" ]]; then
    rm -f "$response_file"
    return 0
  fi

  if [[ "$status_code" == "429" ]]; then
    retry_after="$(jq -r '.parameters.retry_after // empty' "$response_file" 2>/dev/null || true)"

    if [[ -n "$retry_after" ]]; then
      log "Telegram rate limit reached. Retrying in ${retry_after}s."
      sleep "$retry_after"
      status_code="$(send_telegram_request "$text" "$response_file" || true)"

      if [[ ! "$status_code" =~ ^[0-9]{3}$ ]]; then
        log "Failed to reach Telegram API while retrying a log line."
        rm -f "$response_file"
        return 1
      fi

      if [[ "$status_code" == "200" ]]; then
        rm -f "$response_file"
        return 0
      fi
    fi
  fi

  log "Failed to send Telegram message (HTTP ${status_code}): $(jq -c '.' "$response_file" 2>/dev/null || cat "$response_file")"
  rm -f "$response_file"
  return 1
}

send_log_line() {
  local service="$1"
  local line="$2"
  local prefix="[$service] "
  local payload_limit=$((TELEGRAM_MAX_MESSAGE_LENGTH - ${#prefix}))
  local remaining="$line"
  local chunk

  if (( payload_limit <= 0 )); then
    log "TELEGRAM_MAX_MESSAGE_LENGTH is too small for the prefix ${prefix}."
    return 1
  fi

  while [[ -n "$remaining" ]]; do
    chunk="${remaining:0:payload_limit}"
    remaining="${remaining:payload_limit}"
    send_telegram_message "${prefix}${chunk}" || true
  done
}

process_line() {
  local service="$1"
  local raw_line="$2"
  local sanitized_line
  local include_regex
  local exclude_regex

  sanitized_line="$(strip_ansi "$raw_line")"

  if [[ -z "${sanitized_line//[[:space:]]/}" ]]; then
    return 0
  fi

  include_regex="$(get_service_regex "INCLUDE" "$service")"
  exclude_regex="$(get_service_regex "EXCLUDE" "$service")"

  if [[ -n "$include_regex" ]] && ! matches_regex "$include_regex" "$sanitized_line"; then
    return 0
  fi

  if [[ -n "$exclude_regex" ]] && matches_regex "$exclude_regex" "$sanitized_line"; then
    return 0
  fi

  send_log_line "$service" "$sanitized_line" || true
}

watch_service_logs() {
  local project_name="$1"
  local service="$2"
  local container_id=""
  local container_name=""

  while true; do
    container_id="$(find_service_container "$project_name" "$service" || true)"

    if [[ -z "$container_id" ]]; then
      log "Waiting for running container of service ${service}..."
      sleep "$TELEGRAM_WATCH_INTERVAL_SECONDS"
      continue
    fi

    container_name="$(docker inspect --format '{{ .Name }}' "$container_id" 2>/dev/null | sed 's#^/##' || true)"
    log "Following logs from service ${service}${container_name:+ (${container_name})}."

    docker logs --tail 0 --follow --timestamps "$container_id" 2>&1 | while IFS= read -r line; do
      process_line "$service" "$line"
    done

    log "Log stream ended for service ${service}. Reconnecting..."
    sleep 1
  done
}

cleanup() {
  if [[ "${#WATCHER_PIDS[@]}" -eq 0 ]]; then
    return 0
  fi

  log "Stopping log watchers..."
  kill "${WATCHER_PIDS[@]}" 2>/dev/null || true
  wait "${WATCHER_PIDS[@]}" 2>/dev/null || true
}

trap cleanup EXIT INT TERM

if [[ "${TELEGRAM_LOGS_ENABLED:-false}" != "true" ]]; then
  log "Telegram log forwarding is disabled because TELEGRAM_LOGS_ENABLED is not set to true."
  exec tail -f /dev/null
fi

if [[ -z "${TELEGRAM_BOT_TOKEN:-}" ]]; then
  log "TELEGRAM_BOT_TOKEN is required when TELEGRAM_LOGS_ENABLED=true."
  exit 1
fi

if [[ -z "${TELEGRAM_CHAT_ID:-}" ]]; then
  log "TELEGRAM_CHAT_ID is required when TELEGRAM_LOGS_ENABLED=true."
  exit 1
fi

IFS=',' read -r -a raw_services <<< "${TELEGRAM_LOG_SERVICES:-backend,db}"
declare -a services=()

for raw_service in "${raw_services[@]}"; do
  raw_service="$(trim "$raw_service")"

  if [[ -n "$raw_service" ]]; then
    services+=("$raw_service")
  fi
done

if [[ "${#services[@]}" -eq 0 ]]; then
  log "No services configured in TELEGRAM_LOG_SERVICES."
  exit 1
fi

compose_project_name="$(wait_for_compose_project)"
log "Resolved Docker Compose project: ${compose_project_name}."

for service in "${services[@]}"; do
  watch_service_logs "$compose_project_name" "$service" &
  WATCHER_PIDS+=("$!")
done

while true; do
  if wait -n "${WATCHER_PIDS[@]}"; then
    status=0
  else
    status=$?
  fi

  log "A log watcher exited unexpectedly with status ${status}."
  exit "$status"
done
