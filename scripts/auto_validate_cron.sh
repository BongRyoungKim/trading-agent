#!/usr/bin/env bash
# Host crontab entry (runs on the VM itself, NOT inside any container):
#   */15 * * * * /home/ubuntu/trading-agent/scripts/auto_validate_cron.sh >> /home/ubuntu/trading-agent/cron_logs/auto_validate_cron.log 2>&1
#
# The log redirect target MUST be under cron_logs/, not logs/ — logs/ is
# owned by uid 1000 (the container's `trader` user / host `opc`, mode 755),
# so the `ubuntu` host account cron runs as (uid 1001) cannot write there;
# bash fails to open the >> target before the script even starts, so cron
# silently never runs it at all (discovered 2026-09-26 — a full day of
# scheduled ticks produced zero log output and the pending proposal never
# got validated). cron_logs/ is a plain directory `ubuntu` creates and owns
# itself, outside any container-managed path.
#
# Auto-validates a staged live-parameter proposal
# (reports/pending_param_change.json) so nobody needs to SSH in and run
# scripts/validate_params.py by hand. The backtest itself still runs in the
# one-shot `backtest` compose service, kept separate from the live
# trading-agent process on purpose — src/backtest/param_validator.py's
# module docstring: this VM is a 1GB-RAM free-tier instance, and running a
# multi-symbol backtest inside the same process that's placing live orders
# risks starving it of memory. validate_params.py itself skips the actual
# backtest when the cached result still matches what's staged
# (lp.validation_matches_pending()), so repeat cron ticks after the first
# are cheap.
#
# This script only refreshes the cached pass/fail verdict — approving or
# rejecting the proposal still requires a human clicking the dashboard
# button. It never passes --apply.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

LOCK_FILE="/tmp/trading-agent-auto-validate.lock"
exec 200>"$LOCK_FILE"
flock -n 200 || { echo "$(date -Iseconds) skip: previous run still in progress"; exit 0; }

if [ ! -f "reports/pending_param_change.json" ]; then
  exit 0
fi

echo "$(date -Iseconds) pending change found — running validate_params.py"
docker compose run --rm --entrypoint python backtest \
  scripts/validate_params.py --pending reports/pending_param_change.json
