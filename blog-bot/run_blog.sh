#!/bin/bash
# Blog generation wrapper script.
# Called by macOS launchd on schedule, or manually for on-demand generation.
#
# Handles:
#   - Virtual environment validation and auto-recovery
#   - .env loading
#   - Error logging with timestamps
#   - On-demand generation with --now flag
#
# Usage:
#   Scheduled (via launchd):  ./run_blog.sh
#   On-demand:                ./run_blog.sh --now
#   Custom topic:             ./run_blog.sh --now --topic "Your custom topic"

set -euo pipefail

PROJECT_ROOT="/Users/ethanbrooks/llm-relay"
BLOG_BOT_DIR="${PROJECT_ROOT}/blog-bot"
LOG_DIR="${BLOG_BOT_DIR}/logs"
WRAPPER_LOG="${LOG_DIR}/wrapper.log"
POETRY="/Users/ethanbrooks/.local/bin/poetry"
VENV_PYTHON="${PROJECT_ROOT}/.venv_python_path"

cd "${PROJECT_ROOT}"

# Ensure log directory exists
mkdir -p "${LOG_DIR}"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [wrapper] $1" | tee -a "${WRAPPER_LOG}"
}

log "=== Blog wrapper starting ==="

# Step 1: Find or recover the virtualenv Python
resolve_python() {
    # Try cached path first (fastest)
    if [ -f "${VENV_PYTHON}" ]; then
        CACHED_PATH=$(cat "${VENV_PYTHON}")
        if [ -x "${CACHED_PATH}" ]; then
            echo "${CACHED_PATH}"
            return 0
        fi
        log "WARNING: Cached Python path no longer valid: ${CACHED_PATH}"
    fi

    # Try poetry env info
    if command -v "${POETRY}" &>/dev/null; then
        POETRY_PATH=$("${POETRY}" env info --executable 2>/dev/null || true)
        if [ -n "${POETRY_PATH}" ] && [ -x "${POETRY_PATH}" ]; then
            echo "${POETRY_PATH}" > "${VENV_PYTHON}"
            log "Found Python via poetry: ${POETRY_PATH}"
            echo "${POETRY_PATH}"
            return 0
        fi

        # Virtualenv doesn't exist — recreate it
        log "WARNING: No virtualenv found. Running poetry install..."
        "${POETRY}" install --no-interaction 2>&1 | tail -5 | tee -a "${WRAPPER_LOG}"

        POETRY_PATH=$("${POETRY}" env info --executable 2>/dev/null || true)
        if [ -n "${POETRY_PATH}" ] && [ -x "${POETRY_PATH}" ]; then
            echo "${POETRY_PATH}" > "${VENV_PYTHON}"
            log "Virtualenv restored: ${POETRY_PATH}"
            echo "${POETRY_PATH}"
            return 0
        fi
    fi

    # Last resort: try known poetry cache path
    KNOWN_PATH="/Users/ethanbrooks/Library/Caches/pypoetry/virtualenvs/llm-relay-qr-HRgLT-py3.13/bin/python"
    if [ -x "${KNOWN_PATH}" ]; then
        echo "${KNOWN_PATH}" > "${VENV_PYTHON}"
        echo "${KNOWN_PATH}"
        return 0
    fi

    log "FATAL: Cannot find or create Python virtualenv."
    return 1
}

PYTHON=$(resolve_python)
if [ -z "${PYTHON}" ]; then
    log "FATAL: No Python interpreter available. Exiting."
    exit 1
fi

log "Using Python: ${PYTHON}"

# Step 2: Check if this is a manual/forced run
TODAY=$(date '+%Y-%m-%d')
CURRENT_HOUR=$(date '+%H')
FORCE_RUN=false
for arg in "$@"; do
    case "${arg}" in
        --now|--dry-run|--topic|-t) FORCE_RUN=true ;;
    esac
done

# Step 2a: Time-of-day guard — automatic runs only between 7 AM and 11 PM
if [ "${FORCE_RUN}" = false ] && { [ "${CURRENT_HOUR}" -lt 7 ] || [ "${CURRENT_HOUR}" -ge 23 ]; }; then
    log "Outside generation window (7 AM–11 PM). Current hour: ${CURRENT_HOUR}. Skipping."
    exit 0
fi

# Step 2b: Idempotency check — skip if today's blog already exists
if [ "${FORCE_RUN}" = false ]; then
    EXISTING=$(find "${BLOG_BOT_DIR}/outputs" -name "${TODAY}_*.md" ! -name "*_validation.json" 2>/dev/null | head -1 || true)
    if [ -n "${EXISTING}" ]; then
        log "Blog already generated today: $(basename "${EXISTING}"). Skipping."
        log "=== Blog wrapper finished (already ran today) ==="
        exit 0
    fi
fi

# Step 3: Run the blog generation script
# Pass through any arguments (--topic, --now, etc.)
"${PYTHON}" "${BLOG_BOT_DIR}/generate_blog.py" "$@" 2>&1 | tee -a "${WRAPPER_LOG}"
EXIT_CODE=${PIPESTATUS[0]}

if [ ${EXIT_CODE} -eq 0 ]; then
    log "Blog generation completed successfully."
else
    log "Blog generation failed with exit code ${EXIT_CODE}."
fi

log "=== Blog wrapper finished ==="
exit ${EXIT_CODE}
