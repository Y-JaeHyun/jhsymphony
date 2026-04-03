#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# JHSymphony Service Manager
# Usage: ./jhsymphony.sh {start|stop|status|restart|logs}
# ============================================================

APP_NAME="jhsymphony"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Paths
DATA_DIR="${HOME}/.jhsymphony"
PID_FILE="${DATA_DIR}/${APP_NAME}.pid"
LOG_DIR="${DATA_DIR}/logs"
LOG_FILE="${LOG_DIR}/${APP_NAME}.log"

# Config
CONFIG_FILE="${JHSYMPHONY_CONFIG:-${SCRIPT_DIR}/jhsymphony.yaml}"

# Graceful shutdown timeout (seconds)
STOP_TIMEOUT=10

# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

_init_dirs() {
    mkdir -p "${DATA_DIR}" "${LOG_DIR}"
}

_load_env() {
    local env_file="${SCRIPT_DIR}/.env"
    if [[ -f "${env_file}" ]]; then
        set -a
        # shellcheck disable=SC1090
        source "${env_file}"
        set +a
    fi
}

_ensure_github_token() {
    if [[ -z "${GITHUB_TOKEN:-}" ]]; then
        if command -v gh &>/dev/null; then
            GITHUB_TOKEN="$(gh auth token 2>/dev/null || true)"
            if [[ -n "${GITHUB_TOKEN}" ]]; then
                export GITHUB_TOKEN
            else
                echo "[WARN] GITHUB_TOKEN not set and 'gh auth token' failed"
            fi
        else
            echo "[WARN] GITHUB_TOKEN not set and gh CLI not found"
        fi
    fi
}

_find_python() {
    # Priority: project venv > system
    local venv_python="${SCRIPT_DIR}/.venv/bin/python"
    if [[ -x "${venv_python}" ]]; then
        echo "${venv_python}"
    else
        echo "python3"
    fi
}

_is_running() {
    if [[ -f "${PID_FILE}" ]]; then
        local pid
        pid="$(cat "${PID_FILE}")"
        if kill -0 "${pid}" 2>/dev/null; then
            return 0
        fi
        # Stale PID file
        rm -f "${PID_FILE}"
    fi
    return 1
}

_get_pid() {
    cat "${PID_FILE}" 2>/dev/null || echo ""
}

# ------------------------------------------------------------
# Commands
# ------------------------------------------------------------

cmd_start() {
    _init_dirs
    _load_env
    _ensure_github_token

    if _is_running; then
        echo "${APP_NAME} is already running (PID: $(_get_pid))"
        exit 1
    fi

    if [[ ! -f "${CONFIG_FILE}" ]]; then
        echo "[ERROR] Config not found: ${CONFIG_FILE}"
        exit 1
    fi

    local python
    python="$(_find_python)"

    echo "Starting ${APP_NAME}..."
    echo "  Python : ${python}"
    echo "  Config : ${CONFIG_FILE}"
    echo "  Log    : ${LOG_FILE}"

    cd "${SCRIPT_DIR}"
    nohup "${python}" run_service.py >/dev/null 2>&1 &
    local pid=$!

    echo "${pid}" > "${PID_FILE}"

    # Brief wait to check immediate crash
    sleep 1
    if kill -0 "${pid}" 2>/dev/null; then
        echo "${APP_NAME} started (PID: ${pid})"
    else
        rm -f "${PID_FILE}"
        echo "[ERROR] ${APP_NAME} failed to start. Check log: ${LOG_FILE}"
        tail -20 "${LOG_FILE}"
        exit 1
    fi
}

cmd_stop() {
    if ! _is_running; then
        echo "${APP_NAME} is not running"
        return 0
    fi

    local pid
    pid="$(_get_pid)"
    echo "Stopping ${APP_NAME} (PID: ${pid})..."

    kill "${pid}"

    local waited=0
    while kill -0 "${pid}" 2>/dev/null; do
        if (( waited >= STOP_TIMEOUT )); then
            echo "[WARN] Graceful shutdown timed out. Sending SIGKILL..."
            kill -9 "${pid}" 2>/dev/null || true
            break
        fi
        sleep 1
        (( waited++ ))
    done

    rm -f "${PID_FILE}"
    echo "${APP_NAME} stopped"
}

cmd_status() {
    if _is_running; then
        local pid
        pid="$(_get_pid)"
        echo "${APP_NAME} is running (PID: ${pid})"

        # Check dashboard port
        if command -v ss &>/dev/null; then
            if ss -tlnp 2>/dev/null | grep -q ":8080"; then
                echo "  Dashboard: http://localhost:8080 (listening)"
            else
                echo "  Dashboard: not yet listening"
            fi
        fi
    else
        echo "${APP_NAME} is not running"
        exit 1
    fi
}

cmd_restart() {
    cmd_stop
    sleep 1
    cmd_start
}

cmd_logs() {
    if [[ ! -f "${LOG_FILE}" ]]; then
        echo "No log file found: ${LOG_FILE}"
        exit 1
    fi
    tail -f "${LOG_FILE}"
}

# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

case "${1:-}" in
    start)   cmd_start   ;;
    stop)    cmd_stop    ;;
    status)  cmd_status  ;;
    restart) cmd_restart ;;
    logs)    cmd_logs    ;;
    *)
        echo "Usage: $0 {start|stop|status|restart|logs}"
        echo ""
        echo "Commands:"
        echo "  start    Start the service in background"
        echo "  stop     Stop the running service"
        echo "  status   Check if the service is running"
        echo "  restart  Restart the service"
        echo "  logs     Tail the service log"
        echo ""
        echo "Environment:"
        echo "  JHSYMPHONY_CONFIG  Config file path (default: jhsymphony.yaml)"
        echo "  GITHUB_TOKEN       GitHub API token (fallback: gh auth token)"
        exit 1
        ;;
esac
