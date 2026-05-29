#!/usr/bin/env bash
# Run the Rookery demo suite end to end and print a PASS/FAIL matrix.
#   ./run_all_demos.sh         # free (mock) demos only — fast, $0
#   ./run_all_demos.sh --all   # also the paid real-model demos (claude/codex/deeplake)
# Demos are self-contained (each cleans up its own processes); we run serially.
set -uo pipefail   # NOT -e: keep going past a failing demo
cd "$(dirname "$0")"

FREE=(demo.sh demo_postmaster.sh demo_research.sh demo_multihost.sh demo_security.sh \
      demo_federation.sh demo_dlq.sh demo_terminal.sh a2a_demo.sh demo_a2a_secure.sh demo_a2a_push.sh demo_tls.sh)
PAID=(demo_real.sh demo_real_research.sh demo_real_sdk.sh demo_real_sdk_research.sh demo_real_team.sh)
RUN=("${FREE[@]}")
[ "${1:-}" = "--all" ] && RUN+=("${PAID[@]}")

free_ports() {
  for p in 8765 8766 9099; do
    PID=$(ss -tlnpH 2>/dev/null | grep ":$p " | grep -oP 'pid=\K[0-9]+' | head -1 || true)
    [ -n "${PID:-}" ] && kill -9 "$PID" 2>/dev/null || true
  done
  tmux kill-session -t rooktest 2>/dev/null || true
}

declare -A RESULT
echo "running ${#RUN[@]} demos serially…"; echo
for d in "${RUN[@]}"; do
  if [ ! -x "./$d" ]; then RESULT[$d]="MISSING"; echo "=== $d -> MISSING ==="; continue; fi
  case "$d" in demo_real*) T=360;; *) T=120;; esac
  free_ports
  echo "=== $d (timeout ${T}s) ==="
  if timeout "$T" "./$d" > "/tmp/suite_$d.log" 2>&1; then rc=0; else rc=$?; fi
  if [ $rc -eq 0 ] && grep -qi "complete" "/tmp/suite_$d.log" && ! grep -q "Traceback" "/tmp/suite_$d.log"; then
    RESULT[$d]="PASS"
  else
    RESULT[$d]="FAIL(rc=$rc)"
  fi
  echo "  -> ${RESULT[$d]}  (log: /tmp/suite_$d.log)"
done
free_ports

echo; echo "===================== SUITE RESULTS ====================="
pass=0; total=0
for d in "${RUN[@]}"; do
  printf "  %-28s %s\n" "$d" "${RESULT[$d]:-?}"
  total=$((total+1)); [ "${RESULT[$d]:-}" = "PASS" ] && pass=$((pass+1))
done
echo "---------------------------------------------------------"
echo "  $pass / $total passed"
