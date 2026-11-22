#!/bin/bash
# Fresh-clone gates for main + #40 + #41 + #42. Every exit code is captured without a pipe or
# `tail`, so the number in the log is the program's own status. Logs are per-repository, and no
# clone is reused.
set -u
LOG=/tmp/audit42/gates
clone() {                       # $1 = name, $2 = refspec-or-branch
  local d=/tmp/audit42/clones/$1
  rm -rf "$d" 2>/dev/null
  git clone -q --origin upstream https://github.com/SpicyBadran/PC5.git "$d" >>"$LOG/clone.log" 2>&1
  ( cd "$d" && git fetch -q upstream "$2" >>"$LOG/clone.log" 2>&1 && git checkout -q FETCH_HEAD >>"$LOG/clone.log" 2>&1 )
  echo "$1 HEAD=$(cd "$d" && git rev-parse HEAD)"
}
run() {                         # $1 = dir, then the command
  local d=$1; shift
  ( cd "$d" && "$@" >"$LOG/last.out" 2>&1 ); local rc=$?
  echo "\$ $*  ->  exit=$rc"
  sed 's/^/      /' "$LOG/last.out" | tail -6
}
for spec in "main:refs/heads/main" "pr40:refs/pull/40/head" "pr41:refs/pull/41/head" "pr42:refs/pull/42/head"; do
  name=${spec%%:*}; ref=${spec#*:}
  d=/tmp/audit42/clones/$name
  echo "##################### $name ($ref) #####################"
  clone "$name" "$ref"
  echo "  tree=$(cd "$d" && git rev-parse HEAD^{tree})  commit=$(cd "$d" && git rev-parse HEAD)"
  echo "--- gates on a PRISTINE clone (nothing written yet) ---"
  run "$d" make -s dashboard-reconcile
  run "$d" make -s presentation-check
  run "$d" make -s docs-check
  run "$d" make -s citations-check
  run "$d" python3 tools/dashboard_reconcile.py --json
  echo "--- the full suite ---"
  run "$d" python3 -m unittest discover -s tests -p 'test_*.py'
  echo "--- after a generator run, the tree must be clean if the receipt claims currency ---"
  run "$d" make -s presentation
  run "$d" git status --porcelain
done > "$LOG/gates.log" 2>&1
echo "DONE $(date -u +%FT%TZ)" >> "$LOG/gates.log"
