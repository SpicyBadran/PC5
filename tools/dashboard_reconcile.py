#!/usr/bin/env python3
"""Reconcile PC5's README dashboard with what has actually landed on the primary branch.

Two separate questions, and conflating them is what made the first version unreliable:

  * is the *content* current?  `tools/presentation.py check` answers that — the committed
    figures and generated README blocks must equal a fresh render of the tree.
  * has every change on the primary branch been *inspected*?  This tool answers that, from
    an explicit, durable reconciliation receipt committed in the repository.

A receipt is a positive record: the boundary commit it was written against, the commits it
looked at, the files those commits touched, whether the generated output changed as a result,
and the verification commands with their real exit codes.  Inferring a watermark from "the
last commit that touched README.md or an SVG" cannot work, because a reconciliation that
changes nothing visible produces no such commit, and a merge commit that *delivers* one is
hidden by Git's history simplification.  So nothing here is inferred.

    python3 tools/dashboard_reconcile.py                 # read: is main reconciled?
    python3 tools/dashboard_reconcile.py --json          # the same answer, machine-readable
    python3 tools/dashboard_reconcile.py --record        # write the receipt (post-merge)

Exit codes, deliberately: 0 current, 1 outstanding, 2 error.  Anything that cannot be
established is an error or an outstanding state, never a green result — a tool that answers
"nothing to do" when it could not read the history is worse than no tool.

Nothing runs this command for you.  There is no CI workflow in this repository, no Git hook is
installed by `make`, and no scheduled job reads `main`: reconciling the dashboard after a merge
into `main` is a duty of whoever merged, discharged with `make post-merge` and read back with
`make dashboard-reconcile`.  Those two targets report exit 2 whenever this tool says anything
other than 0, because that is how `make` reports a failed recipe; the distinction between "an
inspection is due" (1) and "the question cannot be answered" (2) lives in the tool's own status
and in `--json`.


Merge strategy does not matter.  The tool walks `git rev-list --first-parent <boundary>..<target>`
and classifies each commit by the paths its diff to its first parent touched, so a merge
commit, a squash, a rebase-merge, a fast-forward and a direct commit to `main` are all just
commits in the range.

Exemption rule (this is what stops a reconciliation from re-triggering itself forever): a
commit in the range needs no inspection if everything it changed is a reconciliation output —
the receipt itself, or a file under `docs/assets/` — and, for `README.md`, if every line it
changed falls inside a generated `<!-- pc5:*:begin --> … <!-- pc5:*:end -->` span.  Those
files are exactly what `make presentation` writes, they cannot carry an unreviewed change
(a hand edit fails `presentation-check`), and the rule is a statement about *content*, not
about a commit SHA that has not been created yet.  Anything else in the range — code, docs
prose, a milestone state, an evidence record — is an event that must be inspected and named
in the next receipt.

The follow-up that records the delivery of a receipt is therefore a receipt-only change: it
is exempt, and the loop closes after one step.  It is also auditable: `boundary_commits` in
the next receipt names it.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

SCHEMA = "pc5.dashboard-reconciliation/1"

#: where the receipt lives; tracked, dated only by the commits it names
RECEIPT = "docs/evidence/dashboard-reconciliation.json"

#: the primary branch this tool reconciles against unless told otherwise.  The *remote* ref
#: only, deliberately: a local `main` may be stale or be the branch you are standing on, and
#: answering "what has landed?" from it would be a silent fallback.  Offline, pass `--here`.
DEFAULT_TARGET_REFS = ("origin/main", "origin/HEAD")

#: files whose content is entirely produced by the generator: changing them cannot be an
#: unreviewed change, because `presentation-check` compares them against a fresh render
GENERATED_OUTPUTS = ("docs/assets/",)
GENERATED_README = "README.md"

#: the inputs the dashboard is computed from; a range touching one of these can move a figure
DRIVERS = (
    "docs/architecture/roadmap.json",
    "docs/architecture/PS5_PLATFORM_CONTRACT.md",
    "psm/",
    "docs/evidence/",
    "PS5UPDATE.PUP",
    "tools/presentation.py",
)

#: commands a recording run must pass, in order, before a receipt may be written
PRESENTATION_GATE = "presentation figures and README blocks are current"
VERIFICATIONS = (
    (PRESENTATION_GATE,
     ["{python}", "tools/presentation.py", "check"]),
    ("documentation links, anchors and commands resolve",
     ["{python}", "tools/docs_check.py"]),
)

FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.M)
MARKER = re.compile(r"<!-- pc5:([a-z-]+):(begin|end) -->")


class ReconcileError(Exception):
    """Anything that cannot be established. Reported as exit 2, never as a green result."""


class VerifierMissing(ReconcileError):
    """A gate that cannot run at all, so nothing about the tree was measured.

    A different fact from "a gate ran and said no". Conflating them makes the read path demand an
    inspection that no amount of inspecting can satisfy — and lets the report assert a mismatch
    nobody established — while making the write path sound as though it had checked something.
    """


class GateFailure(ReconcileError):
    """A gate that ran and refused. It carries its label, so each caller can say *which* gate."""

    def __init__(self, label: str, exit_code: int, detail: str):
        super().__init__(f"{label} failed (exit {exit_code}): {detail}")
        self.label = label
        self.detail = detail


def git(repo: Path, *args: str) -> str:
    """Run git in `repo` and return stdout. A failure raises; it is never an empty answer."""
    if not repo.is_dir():
        raise ReconcileError(f"not a directory: {repo}")
    try:
        result = subprocess.run(("git", *args), cwd=repo, capture_output=True, text=True)
    except OSError as exc:                       # git not installed, EACCES, …
        raise ReconcileError(f"cannot run git {args[0] if args else ''}: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        raise ReconcileError(f"git {' '.join(args)} failed ({result.returncode}): "
                             f"{detail[0] if detail else 'no diagnostics'}")
    return result.stdout.strip()


def try_git(repo: Path, *args: str) -> str:
    """The one place a failure means something: an empty string, with the caller deciding."""
    try:
        return git(repo, *args)
    except ReconcileError:
        return ""


def probe(repo: Path, *args: str) -> int:
    """Run a git command that answers with its exit code: 0 is yes, 1 is no, anything else is an
    error worth reporting.

    `git cat-file -e <object>` returns 1 for "no such object" and 128 for "that is not even a
    rev"; conflating the two is how a missing receipt became an error and a broken repository
    became a clean bill of health, in opposite directions, in the previous version of this tool.

    `git merge-base --is-ancestor` and `git cat-file -e` print *nothing* either way, so reading
    their stdout turns "yes" into `""` and then into "no".  That is the difference between a
    receipt being honoured and a receipt being called a foreign line of history, and it is the
    exact class of bug this tool exists to remove — an empty answer that reads like a negative.
    """
    try:
        done = subprocess.run(("git", *args), cwd=repo, capture_output=True, text=True)
    except OSError as exc:
        raise ReconcileError(f"cannot run git {args[0] if args else ''}: {exc}") from exc
    if done.returncode > 1:
        detail = (done.stderr or done.stdout or "").strip().splitlines()
        raise ReconcileError(f"git {' '.join(args)} could not be answered "
                             f"({done.returncode}): {detail[0] if detail else 'no diagnostics'}")
    return done.returncode


def is_ancestor(repo: Path, first: str, second: str) -> bool:
    """Is `first` an ancestor of `second`, or the same commit?  Absent objects are an error."""
    for sha, what in ((first, "boundary"), (second, "target")):
        if not FULL_SHA.match(sha or ""):
            raise ReconcileError(f"the {what} {sha!r} is not a full 40-hex SHA, so ancestry "
                                 "cannot be checked")
        if probe(repo, "cat-file", "-e", sha) != 0:
            raise ReconcileError(
                f"the {what} {sha[:12]} is not an object in this repository: a receipt whose "
                "commits are absent cannot be honoured (a shallow or incomplete clone, or a "
                "history that was rewritten after the receipt was written)")
    return probe(repo, "merge-base", "--is-ancestor", first, second) == 0


def open_repo(repo: Path) -> None:
    if not (repo / ".git").exists() and try_git(repo, "rev-parse", "--is-inside-work-tree") != "true":
        raise ReconcileError(f"{repo} is not a git repository")
    if try_git(repo, "rev-parse", "--verify", "HEAD") == "":
        raise ReconcileError(f"{repo} has no commits: nothing can be reconciled in an empty repository")
    if git(repo, "rev-parse", "--is-shallow-repository") == "true":
        raise ReconcileError(
            "this is a shallow clone, so the commits between the receipt's boundary and the "
            "target cannot be enumerated: a range that cannot be read is not a clean range. "
            "Fetch the history (`git fetch --unshallow`) or re-clone without --depth")


# ---------------------------------------------------------------- topology primitives
def resolve_target(repo: Path, requested: str | None, here: bool) -> tuple:
    """Return (ref, sha) for the branch being reconciled. Refuses to guess."""
    if here:
        sha = try_git(repo, "rev-parse", "--verify", "HEAD")
        if not sha:
            raise ReconcileError("--here: HEAD does not resolve")
        branch = try_git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        if branch == "HEAD":
            print("warning: --here on a detached HEAD; the receipt will name the SHA only",
                  file=sys.stderr)
            branch = "detached HEAD"
        return f"{branch} (--here)", sha
    candidates = (requested,) if requested else DEFAULT_TARGET_REFS
    tried = []
    for ref in candidates:
        sha = try_git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")
        if sha:
            return ref, sha
        tried.append(ref)
    raise ReconcileError(
        "cannot resolve the primary branch from " + ", ".join(tried)
        + ": fetch it first (`git fetch origin main`), or name it with --target <ref>. "
          "A local branch is deliberately not a fallback — the checked-out branch says "
          "nothing about what has landed on the primary branch, and a stale local copy of "
          "`main` says less than nothing. Pass --here to reconcile the checked-out branch "
          "on purpose, and read the limits that prints.")


def range_commits(repo: Path, boundary: str, target: str) -> list:
    """First-parent commits in (boundary, target], oldest first, each with its own diff.

    The diff is against the commit's *first parent*, which is what "what did this commit
    bring to the branch" means for a merge as well as for a single-parent commit — so a
    squash, a rebase-merge, a fast-forward and a direct commit are all handled the same way.
    """
    out = git(repo, "rev-list", "--reverse", "--first-parent", f"{boundary}..{target}")
    commits = []
    for sha in [line for line in out.splitlines() if line.strip()]:
        parents = git(repo, "show", "-s", "--format=%P", sha).split()
        base = parents[0] if parents else None
        paths = (sorted(set(git(repo, "diff", "--name-only", "--no-commit-id", base,
                                sha).splitlines()))
                 if base else sorted(git(repo, "show", "--pretty=", "--name-only",
                                         sha).splitlines()))
        commits.append({
            "sha": sha,
            "subject": git(repo, "show", "-s", "--format=%s", sha),
            "date": git(repo, "show", "-s", "--format=%cI", sha),
            "parents": len(parents),
            "changed_paths": [line for line in paths if line.strip()],
        })
    return commits


def generated_spans(repo: Path, ref: str) -> list:
    """Line ranges (1-based, inclusive) of generated blocks in README.md at `ref`."""
    body = try_git(repo, "show", f"{ref}:{GENERATED_README}")
    if not body:
        return []
    spans, open_at = [], {}
    for number, line in enumerate(body.splitlines(), start=1):
        match = MARKER.search(line)
        if not match:
            continue
        name, edge = match.group(1), match.group(2)
        if edge == "begin":
            open_at[name] = number
        elif name in open_at:
            spans.append((open_at.pop(name), number))
    return spans


def outside_generated_blocks(repo: Path, sha: str, parents: list) -> list:
    """README line numbers a commit changed that are NOT inside a generated block."""
    base = parents[-1] if len(parents) > 1 else (parents[0] if parents else None)
    if base is None:
        return []
    diff = git(repo, "diff", "--unified=0", "--no-commit-id", base, sha, "--",
               GENERATED_README)
    if not diff:
        return []
    new_spans = generated_spans(repo, sha)
    old_spans = generated_spans(repo, base)
    offenders = []
    for match in HUNK.finditer(diff):
        old_start, old_count = int(match.group(1)), int(match.group(2) or 1)
        new_start, new_count = int(match.group(3)), int(match.group(4) or 1)
        for start, count, spans in ((new_start, new_count, new_spans),
                                    (old_start, old_count, old_spans)):
            if count == 0:
                continue
            for line in range(start, start + count):
                if not any(low <= line <= high for low, high in spans):
                    offenders.append(line)
    return sorted(set(offenders))


def commit_facts(repo: Path, sha: str, cache: dict) -> dict:
    """Parents and first-parent diff of one commit, fetched at most once per run.

    Off the first-parent line — the commits a merge introduced — SHAs are looked up as often
    as a rule needs them, and this is a git subprocess per call otherwise.
    """
    if sha not in cache:
        parents = git(repo, "show", "-s", "--format=%P", sha).split()
        base = parents[0] if parents else None
        paths = (sorted(set(git(repo, "diff", "--name-only", "--no-commit-id", base,
                                sha).splitlines()))
                 if base else sorted(git(repo, "show", "--pretty=", "--name-only",
                                         sha).splitlines()))
        cache[sha] = {"sha": sha, "parents": len(parents),
                      "changed_paths": [line for line in paths if line.strip()]}
    return cache[sha]


def is_generated_output_only(repo: Path, sha: str, cache: dict) -> bool:
    """Does everything this commit changed consist of reconciliation output?

    The content rule that closes the loop: `docs/assets/**` and the receipt are written by the
    generator and by `--record`, and a `README.md` line is reconciliation output only when it
    falls inside a generated span. Hand-writing prose in a generated block fails
    `presentation-check`, so exempting these paths cannot hide an unreviewed change — while
    exempting by *commit SHA* or by *message* would.
    """
    facts = commit_facts(repo, sha, cache)
    paths = facts["changed_paths"]
    if not paths:
        return False                                   # an empty commit is nothing to exempt
    parent = git(repo, "show", "-s", "--format=%P", sha).split()[:1]
    outside = outside_generated_blocks(repo, sha, parent)
    return all(p.startswith(GENERATED_OUTPUTS) or p == RECEIPT
               or (p == GENERATED_README and not outside)
               for p in paths)


def reviewed_ancestry(repo: Path, merge: str, named: set, cache: dict) -> tuple:
    """For a merge: are all the commits it brought in already reviewed?

    This is what lets a pull-request merge that delivers a reconciliation clear at the moment
    it lands. The merge commit itself is a new SHA that no earlier receipt can name, and
    forbidding it from ever being satisfied would mean a reconciliation that must be
    re-recorded forever; accepting it on the strength of "the merge happened" would mean any
    merge at all is self-approving. So the rule is about its content: every commit the merge
    introduces must be named in a receipt or consist of reconciliation output. The commits a
    squash mints are new SHAs with no such ancestry, and still need recording — which is the
    honest difference between the two strategies, not an accident of history walking.
    """
    introduced = [sha for sha in git(repo, "rev-list", f"{merge}^1..{merge}").splitlines()
                  if sha and sha != merge]
    if not introduced:
        # A merge that introduces nothing — `--no-ff` over an already-contained branch, a sync
        # merge with no new content — is still a merge that landed. AGENTS.md §1 step 2 says so in
        # as many words ("a no-op merge is still a merge, and it is recorded as reviewed in step 6
        # rather than skipped"), so this must not be cleared here: clearing it would be the tool
        # deciding quietly that a successful merge needs no look.
        return False, []
    unreviewed = [sha for sha in introduced
                  if sha not in named and not is_generated_output_only(repo, sha, cache)]
    return not unreviewed, unreviewed


def classify(repo: Path, commit: dict, named: set = frozenset(), cache: dict = None) -> dict:
    """Label one commit in the range: does it need inspecting, and what does it drive?"""
    cache = {} if cache is None else cache
    sha = commit["sha"]
    paths = commit["changed_paths"]
    drivers = [p for p in paths if any(p == d or p.startswith(d) for d in DRIVERS)]
    parent = git(repo, "show", "-s", "--format=%P", sha).split()[:1]
    outside = outside_generated_blocks(repo, sha, parent)
    generated_only = is_generated_output_only(repo, sha, cache)
    if generated_only:
        reason, needs = "generated output only", False
    elif sha in named:
        reason, needs = "named in the receipt", False
    elif commit["parents"] > 1:
        covered, unreviewed = reviewed_ancestry(repo, sha, named, cache)
        if covered:
            reason, needs = ("every commit it introduced is already reviewed", False)
        elif not unreviewed:
            reason = ("it introduces no commit of its own: a no-op merge is recorded as reviewed "
                      "rather than skipped (AGENTS.md §1 step 2)")
            needs = True
        else:
            reason = (f"it introduces {len(unreviewed)} unreviewed commit(s): "
                      + ", ".join(sha[:12] for sha in unreviewed[:6])
                      + ("" if len(unreviewed) <= 6 else f" (+{len(unreviewed) - 6} more)"))
            needs = True
    else:
        reason, needs = "not recorded by any receipt", True
    return {"kind": "merge" if commit["parents"] > 1 else "single-parent",
            "requires_inspection": needs,
            "reason": reason,
            "generated_output_only": generated_only,
            "readme_lines_outside_generated_blocks": outside,
            "drivers": drivers}


def generated_output_agrees(repo: Path) -> tuple:
    """Return (why the exemption is not earned, which gate said so), or ("", "") when it is earned.

    This is the premise under the generated-output exemption, tested rather than assumed. It runs
    the same two verifiers a recording run would, against the tree the operator is standing in —
    which is the tree `make post-merge` regenerates, and the one a receipt would be written from.

    Three outcomes, and they are not interchangeable:

      * both gates pass — the exemption is earned, exit 0 is available;
      * a gate ran and refused — an inspection is due, which is exit 1's meaning, and the sentence
        has to name the gate that refused. Only the presentation gate speaks about the figures:
        a documentation gate saying "this output is not what its data produces" sends the operator
        to `make presentation`, a command that would change nothing;
      * a gate cannot run — nothing was measured, so the answer is `cannot answer` (exit 2),
        which is exactly how a recording run treats the same tree. `VerifierMissing` therefore
        propagates rather than being folded into a finding about content.
    """
    try:
        verify(repo, lambda line: None)
    except GateFailure as exc:
        if exc.label == PRESENTATION_GATE:
            return (f"its generated output no longer matches the data it renders ({exc.detail})",
                    exc.label)
        return (f"the check behind the exemption did not pass ({exc})", exc.label)
    return "", ""


# ---------------------------------------------------------------- the receipt
def read_receipt(repo: Path, target: str) -> dict:
    """The receipt as committed on the target branch — not the working tree's copy."""
    raw = try_git(repo, "show", f"{target}:{RECEIPT}")
    if not raw:
        # `cat-file -e <ref>:<path>` exits 128 for a path that is simply not there, which is an
        # absence and not a failure, so presence is asked of `ls-tree` instead: it exits 0 and
        # prints the path when the file is in the tree, and prints nothing when it is not.
        if try_git(repo, "ls-tree", "--name-only", target, "--", RECEIPT):
            raise ReconcileError(f"{RECEIPT} exists at {target} but `git show` could not read "
                                 "it")
        return {}                          # genuinely absent: the Outstanding case
    try:
        receipt = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReconcileError(f"{RECEIPT} at {target} is not valid JSON: {exc}") from exc
    if not isinstance(receipt, dict):
        raise ReconcileError(f"{RECEIPT} at {target} is not an object")
    return receipt


def validate_receipt(repo: Path, receipt: dict, target: str) -> dict:
    """Reject a receipt that cannot be honoured. Every rejection is an error, not a green."""
    if not receipt:
        raise Outstanding(
            f"{RECEIPT} does not exist on {target} yet: nothing has been recorded as "
            "inspected. Bootstrap it with `python3 tools/dashboard_reconcile.py --record "
            "--bootstrap --boundary <full SHA> --inspect <full SHA>`")
    if receipt.get("schema") != SCHEMA:
        raise ReconcileError(f"{RECEIPT} declares schema {receipt.get('schema')!r}, "
                             f"this tool reads {SCHEMA!r}")
    boundary = receipt.get("reconciled_at", "")
    if not isinstance(boundary, str) or not FULL_SHA.match(boundary):
        raise ReconcileError(f"{RECEIPT} has no full 40-hex `reconciled_at` boundary "
                             f"(got {boundary!r}); short SHAs are not acceptable because "
                             "they cannot be proved to be the intended commit")
    if try_git(repo, "cat-file", "-t", boundary) != "commit":
        raise ReconcileError(f"the receipt's boundary {boundary} is not a commit in this "
                             "repository (a rewritten or incomplete clone cannot be audited)")
    if not is_ancestor(repo, boundary, target):
        raise ReconcileError(
            f"the receipt's boundary {boundary[:12]} is not an ancestor of {target}: the "
            "receipt describes a different line of history, so it says nothing about this "
            "one (a rebased or force-pushed branch, or a receipt written with --here on a "
            "feature branch that has not landed)")
    if receipt.get("complete") is not True:
        raise Outstanding(f"the receipt on {target} says the reconciliation was incomplete: "
                          f"{receipt.get('incomplete_reason') or 'no reason recorded'}")
    claimed = receipt.get("inspected_commits") or []
    if not isinstance(claimed, list):
        raise ReconcileError(f"{RECEIPT}'s inspected_commits is not a list")
    for sha in claimed:
        if not isinstance(sha, str) or not FULL_SHA.match(sha):
            raise ReconcileError(f"{RECEIPT} claims a short or malformed SHA: {sha!r}")
        if not is_ancestor(repo, sha, target):
            raise ReconcileError(
                f"{RECEIPT} claims to have inspected {sha[:12]}, which is not an ancestor of "
                f"{target}: a receipt may only name commits that have actually landed. This "
                "is what stops a receipt from pre-approving an unknown future merge.")
    for step in receipt.get("verification", []):
        if step.get("exit_code") != 0:
            raise ReconcileError(f"{RECEIPT} records a failing verification "
                                 f"({step.get('label')} exit {step.get('exit_code')}): the "
                                 "receipt was written over a red gate")
    return receipt


class Outstanding(Exception):
    """A reconciliation that is due, as opposed to one that cannot be established."""


def inspect(repo: Path, target_ref: str, target: str) -> dict:
    open_repo(repo)
    receipt = validate_receipt(repo, read_receipt(repo, target), target)
    boundary = receipt["reconciled_at"]
    commits = range_commits(repo, boundary, target)
    outstanding, reviewed, exempt, cleared = [], [], [], []
    named = set(receipt.get("inspected_commits") or [])
    cache: dict = {}
    for commit in commits:
        info = commit.setdefault("classification", classify(repo, commit, named, cache))
        entry = {**commit, **info}
        # The order matters, and the previous version had it backwards: `named in the receipt` and
        # `every commit it introduced is already reviewed` both clear a commit, so testing
        # `requires_inspection` first swept them into `exempt_generated_only`. The effect was that
        # `recorded_in_receipt` and `merges_cleared_by_reviewed_content` were always empty, and the
        # report's "N already named in the receipt" line always read 0 — a receipt whose whole
        # purpose is to say *which* commits were read, reporting that none were.
        reason = info["reason"]
        if reason == "named in the receipt":
            reviewed.append(entry)
        elif reason == "every commit it introduced is already reviewed":
            cleared.append(entry)
        elif not info["requires_inspection"]:
            exempt.append(entry)
        else:
            outstanding.append(entry)
    exemption, gate = "", ""
    if commits and not outstanding:
        # Whether a clear answer comes from generated output or from a name in the receipt, it
        # comes from a premise, and the premise is that the figures match their data: that what is on disk is what the
        # generator produces from the data. `presentation-check` is the only thing that proves it,
        # and this tool used to exempt by path and then never ask — which let a hand-written
        # figure, a corrupted one, or a deleted one read as CURRENT, and let a commit that edited
        # the receipt name its own unreviewed work and walk away green. So the premise is tested
        # whenever it is load-bearing.
        #
        # A failed premise is *not* moved onto the commits: the receipt-only commit in a delivery is
        # genuinely exempt, and what went stale is the output, which some other commit (or no
        # commit at all) made stale. Blaming the exempt commits would send the operator to inspect
        # a commit that changed nothing but generated output. It is reported as its own finding, in
        # `generated_output_agreement`, and `report` refuses to call the state CURRENT because of it.
        exemption, gate = generated_output_agrees(repo)
    figures = [c for c in commits if any(p.startswith(GENERATED_OUTPUTS)
                                         for p in c["changed_paths"])]
    notes = []
    if target_ref.startswith("origin/"):
        # `origin/main` is a *local* record of what the remote holds; if the branch you are
        # standing on has moved past it, this answer is about the older state on purpose, and
        # saying so is better than silently reconciling the wrong commits.
        local = try_git(repo, "rev-parse", "--verify", "main^{commit}")
        if local and local != target and is_ancestor(repo, target, local):
            ahead = len(git(repo, "rev-list", "--count", f"{target}..{main_branch(repo)}").split())
            notes.append(f"local `main` is {ahead} commit(s) ahead of {target_ref}: this answer "
                         "is about what has landed. Run `git fetch` to catch up, or `--here` to "
                         "reconcile the checked-out branch (that is what `make post-merge` does "
                         "before the push).")
        if not try_git(repo, "config", "--get", "remote.origin.url"):
            notes.append("this clone has no `origin`, so the primary branch cannot be fetched; "
                         "the ref read above is whatever was last recorded locally")
    return {
        "repository": str(repo),
        "repository_id": repository_id(repo),
        "receipt_repository": receipt.get("repository"),
        "repository_matches": (receipt.get("repository") or repository_id(repo))
        == repository_id(repo),
        "target_ref": target_ref,
        "target": target,
        "receipt_path": RECEIPT,
        "boundary": boundary,
        "boundary_date": try_git(repo, "show", "-s", "--format=%cI", boundary),
        "inspected_commits": receipt.get("inspected_commits") or [],
        "commits_since_boundary": [
            {"sha": c["sha"], "subject": c["subject"], "date": c["date"],
             "parents": c["parents"], "changed_paths": c["changed_paths"],
             "requires_inspection": c["classification"]["requires_inspection"],
             "reason": c["classification"]["reason"],
             "drivers": c["classification"]["drivers"]} for c in commits],
        "outstanding": outstanding,
        "generated_output_agreement": exemption,
        "agreement_gate": gate,
        "exemption_checked": bool(commits) and not outstanding,
        "recorded_in_receipt": reviewed,
        "merges_cleared_by_reviewed_content": [c["sha"] for c in cleared],
        "exempt_generated_only": exempt,
        "output_touched_by_range": [c["sha"] for c in figures],
        "verification": receipt.get("verification", []),
        "figures_changed": receipt.get("figures_changed"),
        "readme_changed": receipt.get("readme_changed"),
        "summary": receipt.get("summary", ""),
        "notes": notes,
    }


def main_branch(repo: Path) -> str:
    """`main`, if it exists, else the branch `origin/HEAD` points at."""
    if try_git(repo, "rev-parse", "--verify", "main^{commit}") != "":
        return "main"
    head = try_git(repo, "symbolic-ref", "--short", "origin/HEAD")
    return head or "HEAD"


def repository_id(repo: Path) -> str:
    """The remote this receipt vouches for, so a fork's receipt cannot be mistaken for one."""
    url = try_git(repo, "config", "--get", "remote.origin.url")
    match = re.search(r"[:/]([^/]+/[^/.]+?)(?:\.git)?$", url or "")
    return match.group(1) if match else (url or "unknown-remote")


# ---------------------------------------------------------------- reporting and writing
def report(state: dict, json_mode: bool) -> int:
    # One verdict, two output modes. `outstanding` alone would let `--json` answer 0 about a
    # range whose generated output disagrees with its data, which is the false green the
    # exemption check exists to close — and a script reading the exit code would never know.
    verdict = bool(state["outstanding"]) or bool(state.get("generated_output_agreement"))
    if json_mode:
        print(json.dumps(state, indent=2, sort_keys=True))
        return 1 if verdict else 0
    print(f"repository           {state['repository_id']}")
    if not state["repository_matches"]:
        print(f"  NOTE the receipt vouches for {state['receipt_repository']!r}, and this "
              "checkout's origin is "
              f"{state['repository_id']!r}: the SHAs below were resolved here, so a commit "
              "that exists in a fork is honoured even though the receipt was written "
              "elsewhere. That is auditable, not trusted.")
    print(f"target               {state['target_ref']} @ {state['target'][:12]}")
    for line in state.get("notes", []):
        print(f"  {line}")
    print(f"receipt              {state['receipt_path']}")
    if state.get("exemption_checked"):
        agreement = state.get("generated_output_agreement") or ""
        figures = state.get("agreement_gate") == PRESENTATION_GATE
        wording = (f"does not match its data: {agreement}" if agreement and figures else
                   f"could not be confirmed by the gates: {agreement}" if agreement else
                   "checked against its data in this checkout: the exemption is earned")
        print(f"generated output       {wording}")
    print(f"reconciled boundary  {state['boundary'][:12]}  ({state['boundary_date'][:10]})")
    print(f"summary              {state['summary'] or '(none recorded)'}")
    print(f"figures changed      {state['figures_changed']}   README changed: "
          f"{state['readme_changed']}")
    print(f"verifications        " + (", ".join(f"{v['label']}=exit {v['exit_code']}"
                                                for v in state["verification"]) or "(none)"))
    total = len(state["commits_since_boundary"])
    via_merge = len(state.get("merges_cleared_by_reviewed_content", []))
    print(f"commits since then   {total} on the first-parent line "
          f"({len(state['outstanding'])} needing inspection, "
          f"{len(state['recorded_in_receipt'])} already named in the receipt, "
          f"{len(state['exempt_generated_only'])} generated-output only"
          + (f", {via_merge} merge(s) cleared because every commit they introduce is reviewed"
             if via_merge else "") + ")")
    for entry in state["outstanding"]:
        print(f"  OUTSTANDING {entry['sha'][:12]}  {entry['date'][:10]}  "
              f"[{entry['kind']}]  {entry['subject'][:64]}")
        if entry["drivers"]:
            print(f"              drives: {', '.join(entry['drivers'][:4])}")
        else:
            print("              drives: nothing the dashboard counts")
        if entry["reason"] not in ("not recorded by any receipt", "generated output only",
                                   "named in the receipt"):
            print(f"              {entry['reason']}")
    if verdict:
        if not state["outstanding"]:
            if state.get("agreement_gate") == PRESENTATION_GATE:
                print("OUTSTANDING — the range is confined to generated output, and that output "
                      f"is not what its data produces: {state['generated_output_agreement']}")
            else:
                print("OUTSTANDING — the range is confined to generated output, and the check "
                      "that earns that exemption says no: "
                      f"{state['generated_output_agreement']}")
        else:
            print("OUTSTANDING — a successful change on the primary branch has not been "
                  "reconciled with the dashboard")
        print("  run: make post-merge        # regenerate, verify, and record the receipt")
        print("  then commit the regenerated figures and the receipt together (AGENTS.md)")
        return 1
    print("CURRENT — every first-parent change since the receipt's boundary is either "
          "inspected and recorded or confined to generated output")
    return 0


def record(repo: Path, target_ref: str, target: str, summary: str, dry_run: bool) -> tuple:
    """Write a receipt claiming only what can be shown. Returns (exit code, lines)."""
    open_repo(repo)
    log = []
    existing = read_receipt(repo, target)
    boundary = existing.get("reconciled_at", "") if FULL_SHA.match(str(existing.get(
        "reconciled_at", ""))) else ""
    if boundary and not is_ancestor(repo, boundary, target):
        raise ReconcileError(
            f"the receipt at {target} has boundary {boundary[:12]}, which is not an ancestor "
            "of the target: refusing to record over a history that does not contain it")
    previous = {"reconciled_at": boundary, "inspected_commits": (existing.get(
        "inspected_commits") or [])} if boundary else {}
    commits = range_commits(repo, boundary, target) if boundary else []
    if not boundary:
        # The first receipt on a line of history cannot be inferred: there is no earlier
        # record saying what was already inspected, so the caller has to name a boundary and
        # the commits it looked at. Refusing here is what keeps `make post-merge` on a fresh
        # fork from inventing a clean bill of health.
        raise ReconcileError(
            f"no usable receipt at {target}: `--record` only extends an existing boundary. "
            "Bootstrap it explicitly with --record --boundary <full SHA> --inspect <sha>…"
            " so the receipt states what it looked at.")
    cache: dict = {}
    named_all = set((existing.get("inspected_commits") or []))
    need = [c for c in commits
            if c.setdefault("classification",
                            classify(repo, c, named_all, cache))["requires_inspection"]]
    named = set(previous["inspected_commits"])
    unrecorded = [c for c in need if c["sha"] not in named]
    pending = uncommitted_changes(repo)
    # A reconciliation writes generated output and the receipt together. Generated output is
    # exempt from inspection by definition (the gate just proved it matches the data), so a dirty
    # figure is not a reason to refuse the receipt — a dirty source file is.
    figures_pending = sorted({p for p in pending if p.startswith(GENERATED_OUTPUTS)})
    results = verify_for_record(repo, log.append)
    if not unrecorded:
        # Verification runs first, and it runs even when there is nothing to write. A range of
        # generated-output-only commits used to return here untouched, which meant a stale or
        # hand-written figure could be "reconciled" by the absence of a receipt update: the gates
        # the receipt exists to attest were never run. Nothing is written on this path, so
        # nothing is claimed — but the answer is only honest if the gates were checked.
        return (0, log + ["nothing to record: every change since the boundary is either named in "
                          "the receipt or confined to generated output, and the gates above pass"],
                "nothing-to-record")
    paths = sorted({p for c in unrecorded for p in c["changed_paths"]})
    for commit in commits:                      # a second pass would re-diff every commit
        commit.setdefault("classification", classify(repo, commit))
    receipt = {
        "schema": SCHEMA,
        "repository": repository_id(repo),
        "target_ref": target_ref,
        "reconciled_at": target,
        "previous_boundary": boundary,
        "boundary_date": git(repo, "show", "-s", "--format=%cI", target),
        "summary": summary or (f"reconciled {len(unrecorded)} commit(s) since {boundary[:12]}"),
        "inspected_commits": sorted(set(named) | {c["sha"] for c in unrecorded}),
        "commits_inspected_now": [
            {"sha": c["sha"], "subject": c["subject"], "date": c["date"],
             "parents": c["parents"],
             "kind": c["classification"]["kind"],
             "changed_paths": sorted(set(c["changed_paths"]))[:24],
             "dashboard_drivers": c["classification"]["drivers"]} for c in unrecorded],
        "inspected_paths": paths[:64],
        "paths_truncated": max(0, len(paths) - 64),
        "figures_changed": bool(figures_pending) or any(
            p.startswith(GENERATED_OUTPUTS) for c in unrecorded for p in c["changed_paths"]),
        "readme_changed": GENERATED_README in pending or any(
            p == GENERATED_README for c in unrecorded for p in c["changed_paths"]),
        "generated_output_pending_commit": figures_pending
        + ([GENERATED_README] if GENERATED_README in pending else []),
        "verification": results,
        "complete": True,
        "incomplete_reason": None,
    }
    payload = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if dry_run:
        log.append("  dry run: receipt not written")
        return 0, log + [payload], "dry-run"
    path = repo / RECEIPT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    log.append(f"  wrote {RECEIPT}: boundary {target[:12]}, "
               f"{len(receipt['commits_inspected_now'])} commit(s) inspected, "
               f"figures changed: {receipt['figures_changed']}")
    log.append("  commit the receipt (with any regenerated figures) and open a pull request")
    return 0, log, "written"


# ---------------------------------------------------------------- writing a receipt
def uncommitted_changes(repo: Path) -> list:
    """The receipt and the generator's own output may be dirty; nothing else may.

    A receipt written while a source file is modified would vouch for a tree that does not
    exist in any commit, so the source change has to land (or be stashed) first.
    """
    pending = []
    for line in git(repo, "status", "--porcelain").splitlines():
        fields = line.split(None, 1)          # "XY path", "?? path", "R  old -> new"
        if len(fields) != 2:
            raise ReconcileError(f"cannot read `git status --porcelain` output: {line!r}")
        path = fields[1].split(" -> ")[-1].strip().strip('"')
        if path == RECEIPT or path.startswith(GENERATED_OUTPUTS) or path == GENERATED_README:
            pending.append(path)
            continue
        raise ReconcileError(
            f"the working tree changes {path}, which is neither the receipt nor generated "
            "output: commit the source change first, then reconcile against it")
    return pending


def verify(repo: Path, note=lambda line: None) -> list:
    """Run the gates a receipt is allowed to claim, and record their real exit codes.

    A missing verifier is an error rather than a skipped step: `presentation-check` passing
    because nothing was run is exactly the kind of green this whole tool exists to prevent.
    """
    results = []
    for label, argv in VERIFICATIONS:
        cmd = [sys.executable if a == "{python}" else a for a in argv]
        for part in cmd:
            if part.endswith(".py") and not (repo / part).exists():
                raise VerifierMissing(f"verifier {part} is missing from this checkout")
        done = subprocess.run(cmd, cwd=repo, capture_output=True, text=True)
        results.append({"label": label, "argv": argv, "exit_code": done.returncode})
        note(f"  verify {label}: exit {done.returncode}")
        if done.returncode != 0:
            tail = (done.stdout + done.stderr).strip().splitlines()
            raise GateFailure(label, done.returncode, tail[-1] if tail else "no output")
    return results


def verify_for_record(repo: Path, note=lambda line: None) -> list:
    """`verify()`, phrased for the run that is about to claim something in a receipt.

    The verdict is identical; only the sentence differs. A read run must not be told "refusing to
    record" when nothing was being recorded, and a recording run has to say what it declined to do
    and why the tree is unverifiable.
    """
    try:
        return verify(repo, note)
    except VerifierMissing as exc:
        raise ReconcileError(f"{exc}: refusing to record a receipt over an unverifiable tree")
    except GateFailure as exc:
        raise ReconcileError(f"refusing to record: {exc}")


def must_be_on_target(repo: Path, sha: str, target: str, what: str) -> None:
    """A receipt may only name commits that have landed on the branch it is read from."""
    if not FULL_SHA.match(sha):
        raise ReconcileError(f"{what} {sha!r} is not a full 40-hex SHA")
    if try_git(repo, "cat-file", "-t", sha) != "commit":
        raise ReconcileError(f"{what} {sha[:12]} is not a commit in this repository")
    if sha != target and not is_ancestor(repo, sha, target):
        raise ReconcileError(
            f"{what} {sha[:12]} is not an ancestor of the target: a receipt may only name "
            "commits that have already landed, so it cannot pre-approve a merge that has not "
            "happened yet")


def bootstrap(repo: Path, target_ref: str, target: str, boundary: str, inspected: list,
              note: str, dry_run: bool) -> tuple:
    """Seed the first receipt on a line of history, with the same guarantees as any other.

    It has to pass the gates and name commits that exist on the target, because a bootstrap
    that could be written freely would be a way to declare a branch reconciled without reading
    it — which is the defect this rewrite replaced.
    """
    open_repo(repo)
    must_be_on_target(repo, boundary, target, "the boundary")
    if not inspected:
        raise ReconcileError("--bootstrap needs --inspect <full SHA> at least once: the first "
                             "receipt has to say which commits were read")
    for sha in inspected:
        must_be_on_target(repo, sha, target, f"an inspected commit")
    uncommitted_changes(repo)
    results = verify_for_record(repo, lambda line: None)
    receipt = {
        "schema": SCHEMA,
        "repository": repository_id(repo),
        "target_ref": target_ref,
        "reconciled_at": boundary,
        "previous_boundary": None,
        "boundary_date": git(repo, "show", "-s", "--format=%cI", boundary),
        "summary": note or "bootstrap: the first receipt on this line of history",
        "inspected_commits": sorted(set(inspected)),
        "commits_inspected_now": [],
        "inspected_paths": [],
        "paths_truncated": 0,
        "figures_changed": False,
        "readme_changed": False,
        "generated_output_pending_commit": [],
        "verification": results,
        "bootstrap": True,
        "complete": True,
        "incomplete_reason": None,
    }
    payload = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if dry_run:
        return 0, ["  dry run: receipt not written", payload], "dry-run"
    path = repo / RECEIPT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    return 0, [f"  bootstrapped {RECEIPT} at boundary {boundary[:12]} naming "
               f"{len(inspected)} commit(s); commit it and open a pull request"], "written"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parent.parent),
                        help="repository to inspect (default: this checkout)")
    parser.add_argument("--target", default=None,
                        help="ref to reconcile against (default: the first of "
                             + ", ".join(DEFAULT_TARGET_REFS) + " that resolves)")
    parser.add_argument("--here", action="store_true",
                        help="reconcile the checked-out branch instead of the primary branch; "
                             "the receipt records that it was written this way")
    parser.add_argument("--record", action="store_true",
                        help="write a receipt for the changes since the recorded boundary")
    parser.add_argument("--boundary", default="",
                        help="with --record --bootstrap: the full SHA this receipt starts from")
    parser.add_argument("--inspect", action="append", default=[],
                        help="with --record --bootstrap: a full SHA that was inspected "
                             "(repeatable, at least one required)")
    parser.add_argument("--bootstrap", action="store_true",
                        help="write the first receipt on a line of history: needs --boundary "
                             "and --inspect, and runs the same verification gates")
    parser.add_argument("--note", default="", help="one-line summary stored in the receipt")
    parser.add_argument("--dry-run", action="store_true",
                        help="with --record: print the receipt, do not write it")
    parser.add_argument("--json", action="store_true", dest="json_mode",
                        help="answer with one JSON object, on the read path and on --record")
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    try:
        if (args.boundary or args.inspect) and not args.bootstrap:
            raise ReconcileError(
                "--boundary and --inspect belong to --bootstrap: they say where the *first* "
                "receipt on a line of history starts and what it read. Without --bootstrap this "
                "run would have ignored them and extended the receipt already on the target, "
                "which is not what naming a boundary means. Add --bootstrap, or drop the flags.")
        if args.note and not args.record:
            raise ReconcileError("--note writes into a receipt, and this run writes none: pass "
                                 "--record (or --record --bootstrap) with --note")
        if (args.bootstrap or args.dry_run) and not args.record:
            which = ("--bootstrap and --dry-run" if (args.bootstrap and args.dry_run)
                     else "--bootstrap" if args.bootstrap else "--dry-run")
            raise ReconcileError(
                f"{which} only mean something to a run that would write a receipt: --bootstrap "
                "seeds the first one on a line of history, --dry-run prints the one that would be "
                "written. Without --record this run writes none, so the flag would have been "
                "ignored and the answer above would have been the ordinary read. Pass --record, "
                f"or drop {which}.")
        if args.here and args.target:
            raise ReconcileError(
                f"--here and --target name different things to reconcile: the branch you are "
                f"standing on, and {args.target}. One of them would have been ignored silently, "
                "and a receipt written against a ref nobody meant to name is worse than no "
                "receipt: pick one (`make post-merge` uses --here, before the push).")
        target_ref, target = resolve_target(repo, args.target, args.here)
        if args.record:
            # `--json` is the machine interface, so it is honoured here too: a caller that asked
            # for JSON must never be handed prose to guess at. The status word comes from the
            # writer, which knows whether it wrote, previewed, or had nothing to do.
            if args.bootstrap:
                code, log, status = bootstrap(repo, target_ref, target, args.boundary or "",
                                              args.inspect, args.note, args.dry_run)
            else:
                code, log, status = record(repo, target_ref, target, args.note, args.dry_run)
            if args.json_mode:
                print(json.dumps({"status": status, "exit": code, "target_ref": target_ref,
                                  "target": target, "lines": log}, indent=2, sort_keys=True))
            else:
                for line in log:
                    print(line)
            return code
        return report(inspect(repo, target_ref, target), args.json_mode)
    except Outstanding as due:
        if args.json_mode:
            # `--json` is the machine interface: prose on stdout where a caller expects an object
            # is a parse error, and a parse error reads like "the tool is broken" instead of
            # "a reconciliation is due". The verdict and the exit code were already right.
            print(json.dumps({"status": "outstanding", "reason": str(due),
                              "next": "make post-merge"}, indent=2, sort_keys=True))
        else:
            print(f"OUTSTANDING — {due}")
            print("  run: make post-merge")
        return 1
    except ReconcileError as error:
        if args.json_mode:
            print(json.dumps({"status": "error", "reason": str(error)},
                             indent=2, sort_keys=True), file=sys.stderr)
        print(f"ERROR — {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
