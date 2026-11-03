#!/usr/bin/env bash
# deploy_abl1.sh — install this repo's deliverables on abl1, verify, report.
#
#   bash deploy/deploy_abl1.sh            # DRY RUN: preflight + tests + what would change
#   bash deploy/deploy_abl1.sh --apply    # copy files (with backups), run tests in place, fix dry-runs
#
# Never restarts a service. Never touches the 27B unit, ~/.dsh/.credentials.yaml or
# node_modules/@deepseek-ai/. Backups: <file>.bak-cloudcc-20261103 (never overwritten).
# The bug-fix scripts are only DRY-RUN here; apply each one by hand per BUG_FIXES.md.
set -euo pipefail

APPLY=0; [[ "${1:-}" == "--apply" ]] && APPLY=1
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ODY="${ABL_ODYSSEUS_ROOT:-$HOME/abl/odysseus}"
SUFFIX=".bak-cloudcc-20261103"
REPORTS="$HOME/abl/reports"; mkdir -p "$REPORTS"
LOG="$REPORTS/deploy_cloudcc_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG") 2>&1

say()  { printf '\n\033[1;35m== %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32mOK\033[0m   %s\n' "$*"; }
warn() { printf '  \033[33mWARN\033[0m %s\n' "$*"; }
die()  { printf '  \033[31mFAIL\033[0m %s\n' "$*"; exit 1; }

say "mode: $([[ $APPLY == 1 ]] && echo APPLY || echo 'DRY RUN (use --apply)')  repo=$REPO  odysseus=$ODY"
[[ -d "$ODY/src" && -d "$ODY/routes" ]] || die "Odysseus tree not found at $ODY (set ABL_ODYSSEUS_ROOT)"

# Prefer the backend's own interpreter so tests run against its real deps.
PY=""
for c in "$ODY/venv/bin/python" "$ODY/.venv/bin/python" "$HOME/abl/venv/bin/python" "$HOME/abl/.venv/bin/python" python3; do
  if command -v "$c" >/dev/null 2>&1 || [[ -x "$c" ]]; then PY="$c"; break; fi
done
ok "python: $PY ($($PY --version 2>&1))"

say "1. preflight health"
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:7800/api/health || true)
[[ "$code" == "200" ]] && ok "odysseus /api/health 200" || warn "odysseus /api/health -> $code"
curl -sf --max-time 5 http://127.0.0.1:8085/health >/dev/null && ok "27B :8085 health" || warn "27B :8085 health failed"
systemctl --user is-active --quiet abl-dsh-web && ok "abl-dsh-web active" || warn "abl-dsh-web not active"
[[ -f "$ODY/src/model_router.py" ]] && ok "model_router.py present ($(wc -l < "$ODY/src/model_router.py") lines)" || warn "model_router.py missing"
[[ -f "$ODY/core/egress_gateway.py" ]] && ok "egress_gateway present" || warn "egress_gateway missing; DeepSeek fallback will stay fail-closed"

say "2. repo test suites (isolated; nothing installed yet)"
( cd "$REPO" && ABL_ODYSSEUS_ROOT="$ODY" "$PY" -m pytest -q -p no:cacheprovider tests tools/tests fixes/tests ) \
  && ok "repo tests passed" || die "repo tests failed; not deploying"

say "3. install autoresponder into Odysseus"
install_one() {  # src dst
  local src="$1" dst="$2"
  if [[ -f "$dst" ]] && cmp -s "$src" "$dst"; then ok "unchanged $dst"; return; fi
  if [[ $APPLY == 1 ]]; then
    [[ -f "$dst" && ! -f "$dst$SUFFIX" ]] && cp -p "$dst" "$dst$SUFFIX" && ok "backup $dst$SUFFIX"
    install -m 0644 "$src" "$dst" && ok "installed $dst"
  else
    echo "  would install $src -> $dst$([[ -f "$dst" ]] && echo ' (overwrite, with backup)')"
  fi
}
install_one "$REPO/src/autoresponder.py"            "$ODY/src/autoresponder.py"
install_one "$REPO/routes/autoresponder_routes.py"  "$ODY/routes/autoresponder_routes.py"
install_one "$REPO/tests/test_autoresponder.py"     "$ODY/tests/test_autoresponder.py"
# NOTE: routes/__init__.py in this repo exists only for local test discovery; never copied.

if [[ $APPLY == 1 ]]; then
  say "4. tests in place (Odysseus conftest, real egress gateway, real model_router import)"
  ( cd "$ODY" && "$PY" -m pytest -q -p no:cacheprovider tests/test_autoresponder.py ) && ok "in-tree tests passed" \
    || die "in-tree tests failed; restore with: for f in src/autoresponder.py routes/autoresponder_routes.py tests/test_autoresponder.py; do ...; done"
  ( cd "$ODY" && "$PY" -m src.autoresponder --self-test ) && ok "autoresponder self-test" || die "self-test failed"
  ( cd "$ODY" && "$PY" -m src.autoresponder --status ) || warn "status failed"
  if [[ -f "$ODY/src/model_router.py" ]]; then
    ( cd "$ODY" && "$PY" -m src.model_router --self-test 2>/dev/null || "$PY" src/model_router.py --self-test 2>/dev/null ) \
      && ok "model_router self-test" || warn "model_router self-test: run it the way its docstring says"
  fi
fi

say "5. bug-fix scripts (DRY RUN only; apply individually per BUG_FIXES.md)"
for s in fix_fleet_ip fix_errands_path fix_one_project_context; do
  echo "--- $s"; "$PY" "$REPO/fixes/$s.py" || warn "$s exited non-zero (see output)"
done
echo "--- diagnose abl-market-bridge"
"$PY" "$REPO/fixes/diagnose_unit.py" --unit abl-market-bridge --report "$REPORTS/market_bridge_diag.md" | head -40 || true

say "6. Codex manifest audit"
if [[ -f "$HOME/abl-fused/odysseus/data/abl/codex_work_manifest.json" ]]; then
  "$PY" "$REPO/tools/codex_manifest_audit.py" --probe-ports && ok "audit written to $REPORTS"
else
  warn "manifest not found at ~/abl-fused/odysseus/data/abl/codex_work_manifest.json"
fi

if [[ $APPLY == 1 ]]; then
  say "7. docs into ~/abl"
  for d in ABL_UNIFICATION_SPEC.md BUG_FIXES.md CODEX_TASK_AUDIT.md RECEIPT.md; do
    install_one "$REPO/$d" "$HOME/abl/$d"
  done
  install_one "$REPO/RECEIPT.md" "$HOME/abl/CLOUD_SESSION_RECEIPT_20261103.md"
fi

say "8. post-checks"
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:7800/api/health || true)
[[ "$code" == "200" ]] && ok "odysseus still 200" || warn "odysseus /api/health -> $code"
command -v nvidia-smi >/dev/null && nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu --format=csv,noheader || true
free -h | sed -n 1,3p
( cd "$HOME/abl/odysseus" 2>/dev/null && git status --short | head -20 ) || true

say "done. log: $LOG"
cat <<'NEXT'
NEXT (manual, in order):
  1. Wire the routes. In odysseus/app.py, next to the other include_router calls:
       from routes.autoresponder_routes import setup_autoresponder_routes
       app.include_router(setup_autoresponder_routes())
     then restart the backend and check: curl -s http://127.0.0.1:7800/api/abl/autoresponder/status
  2. Mode stays "shadow" until Asif approves "draft" (env ABL_AUTORESPONDER_MODE).
  3. Apply each fix from BUG_FIXES.md with --apply, one at a time, verifying after each.
NEXT
