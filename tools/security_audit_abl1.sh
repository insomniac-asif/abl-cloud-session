#!/usr/bin/env bash
# security_audit_abl1.sh — read-only runtime security check for abl1 (or the desktop WSL).
# Complements reports/SECURITY_SCAN.md, which could only read code. Changes nothing.
#   bash tools/security_audit_abl1.sh            # human-readable
#   bash tools/security_audit_abl1.sh > ~/abl/reports/security_audit_$(date +%F).txt
set -uo pipefail
hdr() { printf '\n== %s\n' "$*"; }
flag() { printf '  !! %s\n' "$*"; }
okay() { printf '  ok %s\n' "$*"; }

hdr "Listeners NOT on loopback (each one is reachable from the LAN and/or tailnet)"
ss -ltnpH 2>/dev/null | awk '{print $4, $6}' | grep -vE '^(127\.[0-9.]+|\[::1\]):' | while read -r addr proc; do
  port="${addr##*:}"
  case "$port" in
    22) okay "$addr sshd" ;;
    *) flag "$addr  $proc" ;;
  esac
done
for p in 7800 7900 8081 8085 8090 8091 8092 8093 8094 8095 8096 8097 8098 8899 8900 8902 3081 11434 50051; do
  ss -ltnH "sport = :$p" 2>/dev/null | grep -qE '0\.0\.0\.0:|\*:|\[::\]:' && flag "port $p bound to ALL interfaces"
done

hdr "Firewall"
if command -v ufw >/dev/null; then sudo -n ufw status 2>/dev/null | head -20 || echo "  (run with sudo to read ufw status)"; else flag "ufw not installed"; fi

hdr "Unauthenticated ABL core dashboard (:8081) — SECURITY_SCAN H1"
if curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:8081/proposals 2>/dev/null | grep -q 200; then
  flag "GET /proposals answers 200 without auth (approve endpoint is likely open too)"
else okay "no open /proposals on :8081"; fi

hdr "gpuctl token-in-page (:7900) — SECURITY_SCAN H2"
if curl -s -m 3 http://127.0.0.1:7900/ 2>/dev/null | grep -qiE 'X-ABL-Control|ctrl.?token' ; then
  page=$(curl -s -m 3 http://127.0.0.1:7900/)
  echo "$page" | grep -q '__CTRL_TOKEN__' && okay "placeholder not substituted (no helper)" || flag "control token is embedded in the unauthenticated page"
else okay "gpuctl not answering on :7900"; fi

hdr "Secret files: owner-only permissions?"
for f in ~/.dsh/.credentials.yaml ~/abl/odysseus/data/abl/dev.token ~/abl/odysseus/data/abl/mistral.key ~/.config/abl/token \
         ~/.config/abl/abl1.env ~/.config/abl/imap.pass ~/abl/odysseus/.env ~/abl/.env ~/.abl/dashboard_token; do
  [[ -e "$f" ]] || continue
  m=$(stat -c '%a' "$f")
  if [[ "$m" =~ ^[0-7]00$ ]]; then okay "$f ($m)"; else flag "$f is mode $m → chmod 600 $f"; fi
done
find ~/abl ~/abl-fused -maxdepth 4 \( -name '*.token' -o -name '*.key' -o -name '.env' -o -name '*.pem' \) -perm /o+r 2>/dev/null \
  | grep -v node_modules | sed 's/^/  !! world-readable: /'

hdr "Secrets tracked by git (should be empty)"
for r in ~/abl ~/abl-fused ~/abl/odysseus; do
  [[ -d "$r/.git" ]] || continue
  git -C "$r" ls-files | grep -E '(^|/)(\.env|.*\.token|.*\.key|.*\.pem|credentials.*)$' | grep -vE '\.example$' | sed "s|^|  !! $r: |"
done

hdr "Tailscale exposure"
command -v tailscale >/dev/null && { tailscale serve status 2>/dev/null | head -10; tailscale funnel status 2>/dev/null | grep -qi 'funnel on' && flag "FUNNEL is ON (public internet)" || okay "no funnel"; }

hdr "Mesh peer server plaintext fallback — SECURITY_SCAN M1"
journalctl --user -n 2000 --no-pager 2>/dev/null | grep -q 'running without TLS\|No TLS certs configured' && flag "a peer server started WITHOUT TLS" || okay "no plaintext peer start in recent logs"

echo; echo "done. Fix order: H1 → H2 → firewall → M1 (see reports/SECURITY_SCAN.md)."
