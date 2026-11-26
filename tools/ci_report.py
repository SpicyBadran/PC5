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
import sys

SUMMARY_LIMIT = 4000
ANNOTATION_LIMIT = 5000
MAX_BLOCKS_PER_LOG = 2

#: How a block's lines are joined inside a workflow command.  ``\r`` is the documented
#: line break for annotation bodies and keeps the traceback's shape -- and GitHub renders
#: only its *first* line in the annotation list, which is how a whole failure block got
#: reduced to the one line naming the test.  A space loses the shape and keeps the reason,
#: so the reason wins, and the exact text stays in the job's log either way.
SEPARATOR = " "

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

#: A Python traceback's *exception* line -- the terminating `SomeError: message`.  It is
#: neither indented (so it is not a frame) nor `FAIL:`/`ERROR:`-prefixed (so `BLOCK_RE`
#: misses it) nor in `MARK_PREFIXES` (so the marker scan dropped it too).  That is the
#: single line holding the cause, and it was the one line lost: the ovmf job's traceback
#: reached the annotation as `Traceback (most recent call last): make: ... Error 1`,
#: header and exit status with nothing in between.  Matched by shape -- a dotted
#: identifier, an optional `Exception`/`Error` suffix, an optional `: message` -- not by
#: a closed list of builtins, because a harness may raise its own type.
EXCEPTION_RE = re.compile(
    r"^(?:[A-Za-z_]\w*\.)?[A-Z]\w*"
    r"(?:Error|Exception|Interrupt|Exit|Timeout|NotFound|Found|Warning|Iteration|State)?"
    r"(?:: .*)?$")
#: Lines that open a chained traceback or sit between two; they belong to the report and
#: must not be mistaken for the exception line.
CHAIN_PREFIXES = ("During handling of the above exception",
                  "The above exception was the direct cause",
                  "Traceback (most recent call last)")
#: Lines `unittest` or `make` writes that *look* like `Word: text` but name no exception.
#: `ERROR: test_x (mod.Cls.test_x)` is a block heading, and quoting it as the exception of
#: the traceback under it would be a mislabelling of the log rather than a reading of it.
NON_EXCEPTION_PREFIXES = ("FAIL:", "ERROR:", "Ran ", "OK", "FAILED", "make:", "Traceback")

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


def tracebacks(text):
    """Every `Traceback (most recent call last):` block, as (exception, raising frame).

    Frames are indented, the exception line is not, so the block ends at the first
    non-indented line after the header -- skipping the chain separators CPython prints
    between two linked tracebacks.  The returned pair is what a reviewer needs and what
    a raw log hides behind a wall: the *exception type and message*, plus the `File`,
    line and function the raise came from.  Nothing here re-raises or reinterprets: the
    block is quoted as written, and a malformed or header-only traceback yields an empty
    exception so the caller falls back to the marker scan rather than inventing one.
    """
    lines = text.splitlines()
    out = []
    i, n = 0, len(lines)
    while i < n:
        if not lines[i].lstrip().startswith(CHAIN_PREFIXES[-1]):
            i += 1
            continue
        # (File line, the statement printed under it), outermost first.  The site quoted
        # is the *deepest frame in the project's own files*: the outermost is usually just
        # `sys.exit(main())`, and the innermost is usually CPython raising on a library's
        # behalf -- neither is where the bug is.
        frames, j = [], i + 1
        exception = ""
        while j < n:
            line = lines[j]
            if not line.strip():
                j += 1
                continue
            if line[:1] in (" ", "\t"):
                stripped = line.strip()
                if stripped.startswith('File "'):
                    frames.append([stripped, ""])
                elif frames and not frames[-1][1]:
                    frames[-1][1] = stripped      # the raising statement itself
                j += 1
                continue
            if line.startswith(CHAIN_PREFIXES):
                # a chained traceback: keep scanning -- the *last* exception is the live one
                j += 1
                continue
            candidate = line.strip()
            # Only call it an exception if it *looks* like one.  A log truncated by a
            # signal ends the traceback at the first `make:` line, and reporting that
            # `make: *** [...] Error 1` as the exception would be an invention.
            looks_like_one = (EXCEPTION_RE.match(candidate)
                              and not candidate.startswith(NON_EXCEPTION_PREFIXES))
            exception = candidate if looks_like_one else ""
            if not exception:
                exception = "(no exception line: log ends after the traceback)"
            j += 1
            break
        site = ""
        if frames:
            here = os.getcwd() + os.sep
            mine = [f for f in frames if f[0].startswith('File "%s' % here)]
            chosen = mine[-1] if mine else frames[0]
            skipped = len(frames) - 1 - frames.index(chosen)
            site = chosen[0] + (": " + chosen[1] if chosen[1] else "")
            if skipped:
                site += " [+%d frame(s) below]" % skipped
        if exception or site:
            out.append((exception, site))
        i = j if j > i else i + 1
    return out


def unquoted_tracebacks(text, blocks=()):
    """The tracebacks in `text` whose exception line no failure block already carries.

    A `FAIL:` block quotes the traceback it printed, exception line included, so repeating
    it would be duplication dressed up as evidence.  What earns an annotation of its own is
    a crash *outside* unittest -- a harness that died with a traceback and no test block --
    which is exactly the shape the firmware job's log had: a `Traceback` header at one end,
    a `make:` exit status at the other, and the cause in neither.
    """
    joined = "\n".join(blocks)
    return [(exc, frame) for exc, frame in tracebacks(text) if exc and exc not in joined]


def traceback_annotation(path, text, found=None):
    """One `::error::` line naming the exception, its site, and the exit status.

    Ordered so the *reason* leads: `SomeError: message` is more load-bearing than the
    `File ... line N` beneath it, which is more load-bearing than the `make: ... Error 1`
    that only says the gate exited non-zero.
    """
    if found is None:
        found = tracebacks(text)
    if not found:
        return ""
    exception, frame = found[-1]
    tail = [l for l in text.splitlines() if l.startswith("make:")]
    bits = [exception or "(traceback with no exception line)",
            "raised at " + frame if frame else "(no frame)",
            tail[-1] if tail else "(no `make:` status line)"]
    return ("::error title=%s::%s" % (os.path.basename(path), " | ".join(bits)[:ANNOTATION_LIMIT]))



def annotations_for(path, text):
    """`::error::` workflow commands quoting this log's failure blocks.

    A workflow command is one line, so a block's newlines are folded with `SEPARATOR`.
    The measured reason is `SEPARATOR = " "` rather than the ``\\r`` the docs suggest for
    annotation bodies: GitHub lists only the first line of a message containing real line
    breaks, so a folded-with-``\\r`` block arrived here as nothing but `FAIL: test_name`,
    and the assertion underneath it was unreachable from any endpoint.
    """
    blocks, tally, marks = extract(text)
    # A `FAILED (...)` tally *is* a verdict: a gate whose own output ended there, with
    # no block and no marker line, still earns an annotation.  A bare `OK` does not.
    failed = any(l.startswith("FAILED") for l in tally)
    tb = traceback_annotation(path, text, unquoted_tracebacks(text, blocks))
    # A traceback means the harness itself crashed rather than returning a verdict.  Its
    # exception line is the cause, so it leads -- and it is emitted *in addition to* the
    # block and marker output below, never instead of it, so nothing already reported is
    # concealed by the new line.
    if not blocks and not marks and not failed:
        # A green gate's tally is not a failure.  This step runs after a *failed job*, and
        # the failing thing is usually a step that writes no log at all, so silence here is
        # the signal; the fallback in `main` says so in words.  Reporting `Ran N tests / OK`
        # as an `::error::` would point a reviewer at whichever gate happened to be loudest.
        # A crash annotation is not that silence: it is the only verdict such a log
        # holds, and `report_for` still returns "" for a gate that passed.
        return [tb] if tb else []
    if not blocks:
        blocks = [l for l in tally if l.startswith("FAILED")]
    out = [tb] if tb else []
    for chunk in (blocks[:MAX_BLOCKS_PER_LOG] or ["\n".join(marks)[-1500:]]):
        body = chunk.strip("\n")
        if not body:
            continue
        flat = SEPARATOR.join(l.strip() for l in body.splitlines() if l.strip())
        out.append("::error title=%s::%s"
                   % (os.path.basename(path), flat[:ANNOTATION_LIMIT]))
    return out


def report_for(path, text):
    """The markdown block this log contributes to the job summary."""
    blocks, tally, marks = extract(text)
    # The same rule as `annotations_for`: only a verdict that something went wrong is a
    # "failed gate".  A green gate's tally appearing in a failure report is noise that
    # reads like evidence, which is the one thing this step must not produce.
    failed = any(l.startswith("FAILED") for l in tally)
    found = unquoted_tracebacks(text, blocks)
    if not (blocks or marks or failed or found):
        return ""
    body = "\n".join(blocks)[:SUMMARY_LIMIT] or "\n".join(marks)[-2000:] or "(no failure block)"
    if found:
        # Spell the crash out in the summary too: the annotation is one folded line, and
        # this is where the raising frame can keep its own line.
        head = "\n".join("  %s%s" % (exc or "(no exception line)",
                                     " [raised at %s]" % frame if frame else "")
                         for exc, frame in found)
        body = "Traceback(s):\n%s\n\n%s" % (head, body)
    return "### failed gate: %s\n```\n%s\n```\n%s" % (path, body, "\n".join(tally[-6:]))


def triage_text():
    """The per-file triage's JSON, if the job ran one *before* this step.

    On today's wiring it has not: `tools/ci_triage.py` is the step *after* this one, so
    `build/logs/triage.json` does not exist yet and this returns "".  That is not a gap in
    the evidence -- the triage prints its own `::notice`/`::error` workflow commands, which
    GitHub turns into annotations directly, which is how a restricted caller reads its
    verdict.  This glob is the alternative path: if the two steps are ever swapped so the
    triage runs first, its structured JSON is quoted into the summary here rather than
    living only in an artifact nobody can download.  Kept because the ordering is the kind
    of thing a later edit changes, and the docstring says which way is which.
    """
    out = []
    for path in sorted(glob.glob("build/logs/*.json")):
        try:
            with open(path, "rb") as handle:
                raw = handle.read().decode("utf-8", "replace")
        except OSError:
            continue
        if "results" not in raw and "nonzero" not in raw:
            continue
        picks = [l.strip() for l in raw.splitlines()
                 if re.search(r'"(?:outcome|nonzero|note|stopped_at|file|returncode)"', l)]
        out.append("### " + path + "\n```\n" + "\n".join(picks[:40])[:3000] + "\n```")
    return "\n".join(out)


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
        # Say what was looked at and how big it was: an absent log and a log truncated
        # mid-write by a signal look identical from the annotation list, and "exit 139"
        # only makes sense next to "host.log exists and is 41 bytes".  The interpreter
        # travels with it for the same reason -- a failure that exists only on the runner
        # is nearly always a version difference, and the summary should not have to wait
        # for someone to think to ask.
        seen = []
        for path in gate_logs():
            try:
                seen.append("%s=%dB" % (os.path.basename(path), os.path.getsize(path)))
            except OSError:
                seen.append("%s=unreadable" % os.path.basename(path))
        note = ("No gate log held a verdict (%s). The failure came from a step that writes "
                "no log, died before flushing one, or ran before the first recorded gate. "
                "Interpreter: %s %s."
                % (", ".join(seen) if seen else "none found",
                   sys.implementation.name, " ".join(sys.version.split()[:1])))
        parts.append("### failed job\n\n" + note)
        print("::error title=no gate log::" + note[:ANNOTATION_LIMIT])
    triage = triage_text()
    if triage:
        parts.append(triage)
        for line in triage.splitlines():
            if "outcome" in line or "SIGSEGV" in line.upper():
                print("::notice title=per-file triage::" + line.strip()[:1500])
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
