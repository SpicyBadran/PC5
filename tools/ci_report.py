#!/usr/bin/env python3
"""Quote a failed CI gate's own text, where a restricted caller can read it.

GitHub Actions keeps raw job logs and uploaded artifacts on hosts outside this
repository's control (``results-receiver.actions.githubusercontent.com`` and a
blob store), and a sandboxed environment may be able to reach neither.  What *is*
reachable, with only a repository token, is the Checks API:

    GET /repos/{owner}/{repo}/check-runs/{job_id}/annotations

so a job that ends in failure runs this script, and this script re-reads the logs
the gates already wrote and repeats them as annotations and in the job summary.
It writes no log of its own and reads nothing a gate did not already produce.

Two properties are load-bearing, and ``tests/test_ci_report.py`` pins both:

* **It always exits 0.**  A step whose job is to describe a failure must not become
  a second failure; the first version of this step did exactly that, with a ``grep``
  that matched nothing under ``pipefail``, and the run then reported two red steps
  while the real verdict stayed unread.
* **It quotes the failure block, not the tally line.**  ``FAILED (failures=1)`` says
  a test broke; the block says which one and why.  A one-line annotation carried a
  test name across the wall and none of the reason.
"""

import glob
import os
import re

SUMMARY_LIMIT = 4000
ANNOTATION_LIMIT = 5000
MAX_BLOCKS_PER_LOG = 2

#: The unittest failure block: from its ``FAIL:``/``ERROR:`` heading to the next
#: separator or tally line.  ``re.M`` makes ``^`` a line start; ``re.S`` lets the
#: body cross lines.  Both are needed: without the first, no block is ever found,
#: and without the second, only its heading is.
BLOCK_RE = re.compile(
    r"^(?:FAIL|ERROR): .*?(?=^(?:Ran [0-9]+ tests|OK(?: |$)|FAILED |=====))",
    re.M | re.S)
#: The tally lines a suite ends with.  Reported alongside a block, and on its own
#: when a gate died before unittest could print anything (exit 2, a `KeyboardInterrupt`).
TALLY_RE = re.compile(r"^(?:Ran [0-9]+ tests in .*|FAILED .*|OK(?: \(.*\))?)$", re.M)
#: Lines a `make` failure leaves behind, which carry no unittest structure at all.
MARK_PREFIXES = ("make:", "ERROR:", "Traceback", "[FAIL]", "NOT TESTED", "::error::")

#: What the machine has.  A failure that cannot be reproduced locally is nearly
#: always an environment difference, and the log should answer that question without
#: a shell on the runner.
PROBES = (
    "python3 -VV",
    "make --version | head -n 1",
    "gcc --version | head -n 1",
    "ld --version | head -n 1",
    "git --version",
    "git rev-parse --is-shallow-repository",
    "ls /usr/share/OVMF | head -n 6",
    "uname -srm",
    "node --version",
    "python3 -c 'import unicorn; print(unicorn.__version__)'",
)


def gate_logs(root="."):
    """The log files a run may have produced, in a stable order."""
    names = sorted(glob.glob(os.path.join(root, "build", "logs", "*.log")))
    for extra in ("build/verify.log", "build-iso.log"):
        path = os.path.join(root, extra)
        if os.path.exists(path) and path not in names:
            names.append(path)
    return names


def extract(text):
    """(failure blocks, tally lines, marker lines) from one log's text."""
    blocks = BLOCK_RE.findall(text)
    tally = TALLY_RE.findall(text)
    marks = [l for l in text.splitlines() if l.startswith(MARK_PREFIXES)]
    return blocks, tally, marks


def annotations_for(path, text):
    """`::error::` workflow commands quoting this log's failure blocks.

    Newlines become ``\\r`` because a workflow command must be one line; GitHub
    renders the carriage returns back as line breaks in the annotation body.
    """
    blocks, tally, marks = extract(text)
    # A `FAILED (...)` tally *is* a verdict: a gate whose own output ended there, with
    # no block and no marker line, still earns an annotation.  A bare `OK` does not.
    failed = any(l.startswith("FAILED") for l in tally)
    if not blocks and not marks and not failed:
        # A green gate's tally is not a failure.  This step runs after a *failed job*, and
        # the failing thing is usually a step that writes no log at all, so silence here is
        # the signal; the fallback in `main` says so in words.  Reporting `Ran N tests / OK`
        # as an `::error::` would point a reviewer at whichever gate happened to be loudest.
        return []
    if not blocks:
        blocks = [l for l in tally if l.startswith("FAILED")]
    out = []
    for chunk in (blocks[:MAX_BLOCKS_PER_LOG] or ["\n".join(marks)[-1500:]]):
        body = chunk.strip("\n")
        if not body:
            continue
        out.append("::error title=%s::%s"
                   % (os.path.basename(path),
                      "\r".join(body.splitlines())[:ANNOTATION_LIMIT]))
    return out


def report_for(path, text):
    """The markdown block this log contributes to the job summary."""
    blocks, tally, marks = extract(text)
    # The same rule as `annotations_for`: only a verdict that something went wrong is a
    # "failed gate".  A green gate's tally appearing in a failure report is noise that
    # reads like evidence, which is the one thing this step must not produce.
    failed = any(l.startswith("FAILED") for l in tally)
    if not (blocks or marks or failed):
        return ""
    body = "\n".join(blocks)[:SUMMARY_LIMIT] or "\n".join(marks)[-2000:] or "(no failure block)"
    return "### failed gate: %s\n```\n%s\n```\n%s" % (path, body, "\n".join(tally[-6:]))


def environment_lines():
    lines = []
    for cmd in PROBES:
        stream = os.popen("{ " + cmd + " ; } 2>&1 | head -n 3")
        try:
            text = stream.read().strip()
        except OSError:
            text = ""
        finally:
            stream.close()
        flat = text.replace("\n", " | ")[:200] if text else ""
        lines.append("- `%s`: %s" % (cmd, flat or "MISSING"))
    return lines


def main(argv=None):
    """Print annotations, append the summary, and always return 0."""
    logs = gate_logs()
    parts = []
    for path in logs:
        try:
            with open(path, "rb") as handle:
                text = handle.read().decode("utf-8", "replace")
        except OSError:
            continue
        for line in annotations_for(path, text):
            print(line)
        block = report_for(path, text)
        if block:
            parts.append(block)
    if not parts:
        note = ("No gate log was written: the failure was reported by a step that writes no "
                "log, or happened before the first recorded gate ran.")
        parts.append("### failed job\n\n" + note)
        print("::error title=no gate log::" + note[:ANNOTATION_LIMIT])
    parts.append("### runner environment\n" + "\n".join(environment_lines()))
    out = "\n".join(parts)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        try:
            with open(summary, "a", encoding="utf-8") as handle:
                handle.write("\n" + out + "\n")
        except OSError:
            pass
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
