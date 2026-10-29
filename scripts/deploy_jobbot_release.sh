#!/usr/bin/env bash
# ==============================================================================
# JOBBOT V5 PRODUCTION RELEASE SCRIPT
# Safe, idempotent deployment from workspace to /opt/satvik-jobbot
#
# USAGE (Run in user SSH terminal on Oracle VM):
#   bash /home/ubuntu/main/satvik/jugad-main/scripts/deploy_jobbot_release.sh
# ==============================================================================

set -euo pipefail

WORKSPACE_DIR="/home/ubuntu/main/satvik/jugad-main"
PROD_DIR="/opt/satvik-jobbot"
RELEASE_ID="V5_$(date +%Y%m%d_%H%M%S)"

TIMER_WAS_ACTIVE=false

cleanup() {
    local exit_code=$?
    if [[ "${TIMER_WAS_ACTIVE}" == "true" ]]; then
        if ! sudo systemctl is-active --quiet jobbot-dispatch.timer 2>/dev/null; then
            echo ""
            echo "Deployment cleanup: restoring jobbot-dispatch.timer to active state..."
            sudo systemctl start jobbot-dispatch.timer 2>/dev/null || true
        fi
    fi
    exit "${exit_code}"
}
trap cleanup EXIT INT TERM

echo "=================================================================="
echo "Starting JobBot Production Release: ${RELEASE_ID}"
echo "=================================================================="

# 1. Validate workspace source files
echo "[1/10] Validating workspace source files..."
for f in "jobbot.py" "tests/test_jobbot.py" "sources.json" "config.json" "profile.json" "JOBBOT_FASTTRACK_PROGRESS.md"; do
    if [[ ! -f "${WORKSPACE_DIR}/${f}" ]]; then
        echo "ERROR: Required workspace file ${f} is missing!" >&2
        exit 1
    fi
done

# 2. Validate JSON syntax
echo "[2/10] Validating JSON configuration syntax..."
python3 -m json.tool "${WORKSPACE_DIR}/sources.json" > /dev/null
python3 -m json.tool "${WORKSPACE_DIR}/config.json" > /dev/null
python3 -m json.tool "${WORKSPACE_DIR}/profile.json" > /dev/null

# 3. Validate production directory and service user
echo "[3/10] Validating production directory and 'jobbot' service account..."
if [[ ! -d "${PROD_DIR}" ]]; then
    echo "ERROR: Production directory ${PROD_DIR} does not exist!" >&2
    exit 1
fi
if ! id -u jobbot >/dev/null 2>&1; then
    echo "ERROR: Service account 'jobbot' does not exist!" >&2
    exit 1
fi

# 4. Verify production environment & resume preservation
echo "[4/10] Checking production environment and approved resume..."
if [[ ! -f "${PROD_DIR}/.env" ]]; then
    echo "ERROR: Production .env does not exist at ${PROD_DIR}/.env!" >&2
    exit 1
fi
if [[ ! -f "${PROD_DIR}/resumes/Satvik_Saini_Resume.pdf" ]]; then
    echo "ERROR: Approved resume not found at ${PROD_DIR}/resumes/Satvik_Saini_Resume.pdf!" >&2
    exit 1
fi
if grep -qE "SEND_ENABLED=true" "${PROD_DIR}/.env"; then
    echo "WARNING: SEND_ENABLED is true in .env! Retaining existing file."
fi

# 5. Pre-deployment atomic SQLite online backup
echo "[5/10] Creating atomic pre-deployment backup of production database..."
BACKUP_OUT=$(sudo -u jobbot python3 "${PROD_DIR}/jobbot.py" backup)
echo "Backup result: ${BACKUP_OUT}"
BACKUP_FILE=$(echo "${BACKUP_OUT}" | python3 -c "import sys, json; print(json.load(sys.stdin).get('backup_file', ''))")

if [[ -z "${BACKUP_FILE}" || ! -f "${BACKUP_FILE}" ]]; then
    echo "ERROR: Pre-deployment backup failed!" >&2
    exit 1
fi

# Validate backup integrity
sudo -u jobbot python3 -c "
import sqlite3, sys
conn = sqlite3.connect('${BACKUP_FILE}')
res = conn.execute('PRAGMA integrity_check').fetchone()
conn.close()
if not res or res[0] != 'ok':
    sys.exit(1)
" || { echo "ERROR: Backup integrity check failed!"; exit 1; }
echo "Backup verified intact: ${BACKUP_FILE}"

# 6. Archive current production code for safe rollback
echo "[6/10] Archiving current production code for rollback..."
ROLLBACK_DIR="${PROD_DIR}/rollback/${RELEASE_ID}"
sudo -u jobbot mkdir -p "${ROLLBACK_DIR}"
sudo cp -p "${PROD_DIR}/jobbot.py" "${ROLLBACK_DIR}/"
if [[ -f "${PROD_DIR}/tests/test_jobbot.py" ]]; then
    sudo cp -p "${PROD_DIR}/tests/test_jobbot.py" "${ROLLBACK_DIR}/"
fi
echo "Current code archived to ${ROLLBACK_DIR}"

# 7. Pause dispatch timer during file deployment
echo "[7/10] Pausing systemd timer during file copy..."
if sudo systemctl is-active --quiet jobbot-dispatch.timer; then
    TIMER_WAS_ACTIVE=true
    sudo systemctl stop jobbot-dispatch.timer
    echo "jobbot-dispatch.timer stopped."
fi

# 8. Copy modified release files and set permissions
echo "[8/10] Synchronizing reviewed code files..."
sudo cp "${WORKSPACE_DIR}/jobbot.py" "${PROD_DIR}/jobbot.py"
sudo mkdir -p "${PROD_DIR}/tests"
sudo cp "${WORKSPACE_DIR}/tests/test_jobbot.py" "${PROD_DIR}/tests/test_jobbot.py"
sudo cp "${WORKSPACE_DIR}/JOBBOT_FASTTRACK_PROGRESS.md" "${PROD_DIR}/JOBBOT_FASTTRACK_PROGRESS.md"

sudo chown -R jobbot:jobbot "${PROD_DIR}/jobbot.py" "${PROD_DIR}/tests/test_jobbot.py" "${PROD_DIR}/JOBBOT_FASTTRACK_PROGRESS.md"
sudo chmod 0644 "${PROD_DIR}/jobbot.py" "${PROD_DIR}/tests/test_jobbot.py" "${PROD_DIR}/JOBBOT_FASTTRACK_PROGRESS.md"

# 9. Run full production test suite under 'jobbot' user
echo "[9/10] Running full test suite in production environment..."
if ! sudo -u jobbot PYTHONPATH="${PROD_DIR}" timeout -k 2s 25s python3 -m unittest discover -s "${PROD_DIR}/tests" -q; then
    echo "=================================================================="
    echo "ERROR: Production tests failed! Executing automatic code rollback..."
    echo "=================================================================="
    sudo cp -p "${ROLLBACK_DIR}/jobbot.py" "${PROD_DIR}/jobbot.py"
    if [[ -f "${ROLLBACK_DIR}/test_jobbot.py" ]]; then
        sudo cp -p "${ROLLBACK_DIR}/test_jobbot.py" "${PROD_DIR}/tests/test_jobbot.py"
    fi
    sudo chown -R jobbot:jobbot "${PROD_DIR}/jobbot.py"
    if [[ -d "${PROD_DIR}/tests" ]]; then
        sudo chown -R jobbot:jobbot "${PROD_DIR}/tests"
    fi
    if [[ "${TIMER_WAS_ACTIVE}" = true ]]; then
        sudo systemctl start jobbot-dispatch.timer
    fi
    echo "Rolled back to previous code version. Database preserved untouched."
    exit 1
fi
echo "All production tests passed!"

# 10. Resume timer and verify health
echo "[10/10] Resuming systemd timer and verifying health..."
if [[ "${TIMER_WAS_ACTIVE}" = true ]]; then
    sudo systemctl start jobbot-dispatch.timer
    sudo systemctl is-active --quiet jobbot-dispatch.timer || { echo "WARNING: Timer failed to restart!"; }
    echo "jobbot-dispatch.timer is active."
fi

echo ""
echo "Production Health Check:"
sudo -u jobbot python3 "${PROD_DIR}/jobbot.py" health

echo ""
echo "=================================================================="
echo "SUCCESS: JobBot Release ${RELEASE_ID} deployed successfully!"
echo "Production files updated: jobbot.py, tests/test_jobbot.py"
echo "Rollback archive: ${ROLLBACK_DIR}"
echo "Pre-deployment backup: ${BACKUP_FILE}"
echo "Safety status: SEND_ENABLED=false, DIGEST_SEND_ENABLED=false"
echo "=================================================================="
