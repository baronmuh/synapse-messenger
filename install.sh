#!/usr/bin/env bash
# =============================================================================
# System installation of the Synapse messaging service (AI agents)
#
# Run as root. Installs:
#   - a dedicated "synapse" system account (no shell);
#   - an isolated Python venv in /opt/synapse/venv, pinned dependencies
#     (requirements.lock, --require-hashes — SPEC_PRODUCTION §7) ;
#   - the backup and secrets directories (the storage,
#     logs and run directories are managed by systemd via
#     StateDirectory/LogsDirectory/RuntimeDirectory — SPEC_PRODUCTION §6) ;
#   - la configuration /etc/synapse/config.json ;
#   - the systemd units (server, web, A2A bridge as a template,
#     backup + verification, monitor, CI) from scripts/systemd/;
#   - le wrapper de la passerelle A2A (/opt/synapse/bin) et le moniteur
#     (/opt/synapse/scripts).
#
# Usage :  sudo ./install.sh [chemin/vers/le/depot/synapse]
# (default: current directory)
#
# After installation:
#   sudo -u synapse /opt/synapse/venv/bin/synapse-init-org
#   sudo systemctl enable --now synapse
#   sudo systemctl start synapse-web
#   # passerelle A2A (optionnelle) : provisionner les secrets puis
#   sudo systemctl enable --now synapse-a2a@<agent>.service
# =============================================================================
set -euo pipefail

REPO="$(cd "${1:-$(pwd)}" && pwd)"
SERVICE_USER="synapse"
VENV_DIR="/opt/synapse/venv"
LIB_DIR="/var/lib/synapse"
RUN_DIR="/var/run/synapse"
LOG_DIR="/var/log/synapse"
BACKUP_DIR="/var/backups/synapse"
ETC_DIR="/etc/synapse"
SECRETS_DIR="/etc/synapse/secrets"
BIN_DIR="/opt/synapse/bin"
SCRIPTS_DIR="/opt/synapse/scripts"
SYSTEMD_DIR="/etc/systemd/system"

if [[ $EUID -ne 0 ]]; then
    echo "Error: run this script as root (sudo)." >&2
    exit 1
fi

if [[ ! -f "$REPO/pyproject.toml" || ! -d "$REPO/synapse" ]]; then
    echo "Error: $REPO is not the Synapse project repository." >&2
    exit 1
fi

if [[ ! -f "$REPO/requirements.lock" ]]; then
    echo "Error: $REPO/requirements.lock missing — generate it first " >&2
    echo "(pip-compile --generate-hashes -o requirements.lock pyproject.toml)." >&2
    exit 1
fi

echo "==> System account"
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --home-dir "$LIB_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi

echo "==> Directories"
# The storage/logs/run directories are created by systemd at
# unit startup (StateDirectory/LogsDirectory/RuntimeDirectory).
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0700 "$BACKUP_DIR"
install -d -o root -g root -m 0755 "$ETC_DIR"
install -d -o root -g root -m 0700 "$SECRETS_DIR"
install -d -o root -g root -m 0755 "$BIN_DIR"
install -d -o root -g root -m 0755 "$SCRIPTS_DIR"

echo "==> Python environment (isolated venv, pinned dependencies)"
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip >/dev/null
"$VENV_DIR/bin/pip" install --require-hashes -r "$REPO/requirements.lock"
"$VENV_DIR/bin/pip" install --no-deps "$REPO"

echo "==> Scripts d'exploitation"
install -m 0755 "$REPO/scripts/synapse-a2a-systemd" "$BIN_DIR/synapse-a2a-systemd"
install -m 0755 "$REPO/scripts/synapse-monitor.py" "$SCRIPTS_DIR/synapse-monitor.py"

echo "==> SQLite check (WAL-reset bug)"
"$VENV_DIR/bin/python" - <<'PYEOF'
import sqlite3

def vulnerable(v: str) -> bool:
    """Range affected by the WAL-reset bug: 3.7.0 <= v < 3.51.3,
    except fixed backports 3.50.7+ and 3.44.6+ (sqlite.org/wal.html §11)."""
    try:
        p = tuple(int(x) for x in v.split("."))
    except ValueError:
        return False  # version illisible : on ne bloque pas l'installation
    if p < (3, 7, 0) or p >= (3, 51, 3):
        return False
    if p[:2] == (3, 50) and p[2] >= 7:
        return False
    if p[:2] == (3, 44) and p[2] >= 6:
        return False
    return True

v = sqlite3.sqlite_version
if vulnerable(v):
    print(f"  WARNING: SQLite {v} is in the range affected by the bug")
    print("  WAL-reset (corruption rare mais possible, voir docs/PERFORMANCE.md §13.5).")
    print("  An application write lock protects the service; update the")
    print("  libsqlite3 package as soon as a >= 3.51.3 version (or backport) is available.")
else:
    print(f"  SQLite {v}: not affected by the WAL-reset bug.")
PYEOF

echo "==> Configuration"
if [[ ! -f "$ETC_DIR/config.json" ]]; then
    cat > "$ETC_DIR/config.json" <<EOF
{
  "storage_dir": "$LIB_DIR",
  "socket_path": "$RUN_DIR/synapse.sock",
  "log_dir": "$LOG_DIR",
  "backup_dir": "$BACKUP_DIR"
}
EOF
fi
chown "$SERVICE_USER:$SERVICE_USER" "$ETC_DIR/config.json"
chmod 0600 "$ETC_DIR/config.json"

echo "==> Copie de secours de backup.key (SPEC_PRODUCTION §3)"
# The key is created by the first backup; if it already exists, a
# place une copie dans /etc/synapse (hors du backup_dir). Permissions
# 0640 root:synapse: root writes, the synapse account READS (the monitor
# verifies the sha256 fingerprint of the copy). Otherwise the operator will run the
# copy after the first backup (OPERATIONS).
if [[ -f "$LIB_DIR/backup.key" ]]; then
    install -m 0640 -o root -g "$SERVICE_USER" "$LIB_DIR/backup.key" "$ETC_DIR/backup.key.vault"
    echo "  backup copy created: $ETC_DIR/backup.key.vault (0640 root:$SERVICE_USER)"
else
    echo "  no key yet (first backup) — the copy must be created"
    echo "  after the first 'synapse backup create' (see OPERATIONS.md)."
fi

echo "==> systemd units (SPEC_PRODUCTION §1/§4/§6)"
# Socket path expected by the ExecStartPre (availability wait):
# read from the installed configuration, service default otherwise.
SOCKET_PATH="$(python3 -c "import json,sys; print(json.load(open('$ETC_DIR/config.json')).get('socket_path', '/var/run/synapse/synapse.sock'))" 2>/dev/null || echo '/var/run/synapse/synapse.sock')"
echo "  socket : $SOCKET_PATH"

subst() {
    sed -e "s|@@VENV@@|$VENV_DIR|g" \
        -e "s|@@CONFIG@@|$ETC_DIR/config.json|g" \
        -e "s|@@BACKUP_DIR@@|$BACKUP_DIR|g" \
        -e "s|@@SECRETS_DIR@@|$SECRETS_DIR|g" \
        -e "s|@@WRAPPER@@|$BIN_DIR/synapse-a2a-systemd|g" \
        -e "s|@@SCRIPTS_DIR@@|$SCRIPTS_DIR|g" \
        -e "s|@@SOCKET@@|$SOCKET_PATH|g" \
        -e "s|@@REPO@@|$REPO|g" \
        "$REPO/scripts/systemd/$1" > "$SYSTEMD_DIR/$1"
    chmod 0644 "$SYSTEMD_DIR/$1"
    echo "  $1"
}

for unit in synapse.service synapse-web.service synapse-a2a@.service \
            synapse-backup.service synapse-backup.timer \
            synapse-backup-verify.service synapse-backup-verify.timer \
            synapse-monitor.service synapse-monitor.timer \
            synapse-ci.service synapse-ci.timer; do
    subst "$unit"
done

systemctl daemon-reload
systemctl enable synapse.service synapse-web.service \
    synapse-backup.timer synapse-backup-verify.timer \
    synapse-monitor.timer synapse-ci.timer >/dev/null

echo
echo "Installation complete."
echo
echo "Next step — create the first administrator (no default account):"
echo "  sudo -u $SERVICE_USER $VENV_DIR/bin/synapse-init-org"
echo
echo "Then start the service and the web interface:"
echo "  sudo systemctl start synapse"
echo "  sudo systemctl start synapse-web"
echo
echo "A2A bridge (optional) — provision the secrets then enable:"
echo "  sudo install -d -o root -g root -m 0700 $SECRETS_DIR"
echo "  printf '%s\\n' 'MOT_DE_PASSE_AGENT' | sudo tee $SECRETS_DIR/a2a-<agent>.password >/dev/null"
echo "  printf '%s\\n' \"\$(openssl rand -hex 32)\" | sudo tee $SECRETS_DIR/a2a-<agent>.token >/dev/null"
echo "  sudo chmod 0600 $SECRETS_DIR/a2a-<agent>.*"
echo "  sudo systemctl enable --now synapse-a2a@<agent>.service"
echo
echo "Backup (automatic at 02:00):  sudo -u $SERVICE_USER $VENV_DIR/bin/synapse-backup"
echo "Restore (service stopped):  sudo -u $SERVICE_USER $VENV_DIR/bin/synapse-restore FILE.synbk --force"
echo "IMPORTANT: keep a copy of $LIB_DIR/backup.key (and of"
echo "$ETC_DIR/backup.key.vault) in a separate vault; without it, no"
echo "backup can be restored."
echo
echo "Supervision :  systemctl status synapse synapse-web ;"
echo "  the monitor writes /var/lib/synapse/monitor.json every 5 min."
echo "CI locale : hook pre-push via scripts/install-git-hooks.sh ;"
echo "  full nightly test suite (synapse-ci.timer)."
