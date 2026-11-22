#!/bin/bash
# §10, cleanly: one fresh clone per ref, the full suite with its own log file, and the shell's own
# exit status appended without any pipe between the program and `echo $?`.
set -u
R=https://github.com/SpicyBadran/PC5.git
for spec in main:refs/heads/main pr40:refs/pull/40/head pr41:refs/pull/41/head pr42:refs/pull/42/head; do
  n=${spec%%:*}; ref=${spec#*:}
  d=/tmp/audit42/clones3/$n
  rm -rf "$d"; mkdir -p /tmp/audit42/clones3
  git clone -q "$R" "$d"
  ( cd "$d" && git fetch -q origin "$ref" && git checkout -q FETCH_HEAD )
  log=/tmp/audit42/gates/suite-$n.log
  { echo "# \$ git clone $R && git fetch origin $ref && git checkout FETCH_HEAD"
    echo "# head=$(cd "$d" && git rev-parse HEAD)"
    echo "# tree=$(cd "$d" && git rev-parse 'HEAD^{tree}')"
    echo "# origin/main=$(cd "$d" && git rev-parse origin/main)"
    echo "# \$ TMPDIR=/tmp python3 tools/validate_storage_topology.py 2>/dev/null; python3 -m unittest discover -s tests -p 'test_*.py'"
  } > "$log"
  ( cd "$d" && TMPDIR=/tmp timeout 2400 python3 -m unittest discover -s tests -p 'test_*.py' >> "$log" 2>&1 )
  echo "unittest exit=$?" >> "$log"
  ( cd "$d" && TMPDIR=/timeout= true; TMPDIR=/tmp timeout 2400 make -s check >> "$log" 2>&1 )
  echo "make check exit=$?" >> "$log"
  ( cd "$d" && git status --porcelain >> "$log" 2>&1 ); echo "git status exit=$?" >> "$log"
  echo "done $n $(date -u +%FT%TZ)"
done
