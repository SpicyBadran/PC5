#!/bin/sh
# Provision the Git objects the citations gate needs, from a clone's own remote.
#
# `tools/commit_citations.py` resolves every backticked object id in the dated
# evidence documents. Some of those ids are pull-request heads, and GitHub publishes
# them only under `refs/pull/N/head`, which no default clone (and not
# `actions/checkout`, even with `fetch-depth: 0`) ever fetches. Without them the gate
# cannot tell a stale citation from a citation it was never able to resolve.
#
#   make citation-refs            # or: tools/citation_refs.sh [remote]
#   make citations-check          # now the verdict is real
#
# Behaviour
#   * fetches `+refs/pull/*/head:refs/remotes/origin/pr/*` from the given remote, or
#     from the first remote the clone has, so the refs land under refs/remotes/
#     origin/pr/N and are visible to `git cat-file` like any other object;
#   * it is provisioning, not a gate: it never reports on the documents themselves, it
#     only makes the check able to look;
#   * it does not weaken anything it fetches into, and it writes no file in the tree;
#   * a clone with no remote (an export, a bundle, a tarball) cannot provision, and
#     says so as exit 2 -- "not checked", never "checked and passed" and never
#     "the citations are dead".
#
# Exit status
#   0  the pull-request refs are present in this clone (count printed)
#   1  the fetch was attempted and failed (offline, auth, unreachable host)
#   2  this clone has no remote to fetch from, so the gate cannot be made complete
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
remote=${1:-}

if [ -z "$remote" ]; then
    remote=$(git -C "$root" remote | head -n 1 || true)
fi

if [ -z "$remote" ]; then
    echo "CITATION REFS NOT FETCHED: $root has no git remote, so refs/pull/*/head" \
         "cannot be provisioned here."
    echo "    In a tree exported from an archive the evidence citations are not" \
         "    verifiable; run \`make citations-check\` from a clone of the repository" \
         "    to get a real verdict. This is not a pass."
    exit 2
fi

before=$(git -C "$root" for-each-ref --format='%(refname)' 'refs/remotes/origin/pr/' \
         | wc -l | tr -d ' ')

if ! git -C "$root" fetch --quiet "$remote" '+refs/pull/*/head:refs/remotes/origin/pr/*'; then
    echo "CITATION REFS NOT FETCHED: \`git fetch $remote +refs/pull/*/head:...' failed" \
         "(offline, credentials, or the host is unreachable)."
    echo "    \`make citations-check\` will report unresolved citations that may simply be" \
         "    unfetched. This is not a pass and it is not proof the citations are dead."
    exit 1
fi

after=$(git -C "$root" for-each-ref --format='%(refname)' 'refs/remotes/origin/pr/' \
        | wc -l | tr -d ' ')
echo "CITATION REFS OK: $after pull-request head ref(s) fetched from '$remote'" \
     "(was $before); \`make citations-check\` can now resolve PR-head citations."
