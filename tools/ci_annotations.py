#!/usr/bin/env python3
"""Turn a build log into GitHub workflow annotations, and change nothing else.

Why this exists: the runner's log body is served from a blob store that many environments
cannot reach, while a check run's **annotations** are readable through the REST API. A build
that fails with forty `error CS…` lines is therefore invisible to a reviewer who can only
query the API -- they see `completed / failure` and nothing about *why*. This script puts the
first few compiler errors in front of that reader, in the channel they can actually open.

Rules it obeys, because the step that uses it must not become a new way to lose a failure:

* it always exits 0. A reporter that fails would mask the build's own exit code;
* it never rewrites a verdict. It quotes the log's own lines; it does not summarise,
  reorder by severity, or invent a cause;
* it caps what it emits (a 40-error compiler dump should not flood the annotations API);
* it stays silent on a log it cannot read, rather than reporting "no errors found", which
  would read like a pass.
"""

from __future__ import annotations

import argparse
import re
import sys

#: The shape of an MSBuild or Roslyn diagnostic, with the position optional:
#:   `path/log.cs(41,17): error CS0103: message [project]`   a compiler diagnostic
#:   `path/app.csproj : error NU1102: message [project]`     a restore or evaluation failure
#:   `C:\a\b.csproj : error NU1102: …`                        the same, on Windows
#:
#: The file part is matched lazily up to the diagnostic marker rather than with a character
#: class that excludes `:`, because a Windows path *contains* colons: an earlier pattern did
#: exclude them, the first hosted Windows run failed on a `…csproj : error NU1102:` line, and
#: the reporter printed nothing -- a silent exit 1 with no reason attached. Position is used when
#: the log supplies it and never invented when it does not.
DIAGNOSTIC = re.compile(
    r"^(?P<file>.*?)\s*(?:\((?P<line>\d+)(?:,\d+)?\))?\s*:\s*"
    r"(?P<severity>error|warning)\s+(?P<id>[A-Za-z]+\d+)\s*:\s*"
    r"(?P<message>.*?)(?:\s*\[[^\]]*\])?\s*$")

#: A test runner's own failure lines, in the shapes `dotnet test` prints: the per-test line and
#: the summary tally. A red *test* has to be as legible as a red *build*, and the two are
#: different diagnoses -- an assertion failure says something specific about behaviour, which a
#: "Process completed with exit code 1" annotation does not.
TEST_FAILED = re.compile(r"^\s*(?:Failed|X)\s+(?P<name>[\w.]+)(?:\s+\[(?P<detail>[^\]]*)\])?\s*$")
TEST_TALLY = re.compile(r"^(?P<verdict>Failed!|Passed!)\s+-\s+(?P<tally>.*)$")

#: The few non-compiler failures a build step can end with, which are otherwise invisible.
TOOL_FAILURE = re.compile(
    r"^\s*(?:(?:MSB|NU|MSB\d+)\d+\s*:\s*)?"
    r"(?P<message>(?:error|fatal error)[^\r\n]{0,300})", re.I)

SEVERITY = {"error": "error", "warning": "warning"}


#: The two escape levels the workflow-command syntax needs. `data:` is what the *message*
#: gets -- percent, CR and LF only, because a colon inside a message is prose. `param:` is what
#: a `file=`/`line=` value gets, where a comma would start a new parameter and a colon would
#: end the parameter block early. Getting these backwards is how an annotation ends up printing
#: `::error file=%2Fhome…` and being useless to click.
DATA_ESCAPES = (("%", "%25"), ("\r", "%0D"), ("\n", "%0A"))
PARAM_ESCAPES = DATA_ESCAPES + ((",", "%2C"), (":", "%3A"))


def quote(value: str, escapes=DATA_ESCAPES) -> str:
    for needle, replacement in escapes:
        value = value.replace(needle, replacement)
    return value


def annotate(text: str, limit: int = 20, with_warnings: bool = False) -> list:
    """The `::` lines for the diagnostics in `text`, most significant first, capped.

    Errors before warnings, and within a class, file order -- which is the order a compiler
    printed them, so the first line stays the first thing a reader should fix.
    """
    errors, warnings = [], []
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        tally = TEST_TALLY.match(line.strip())
        if tally:
            if tally.group("verdict") == "Passed!":
                # not an error, and not worth an annotation either: this tool reports
                # failures, and a green tally line it "helpfully" echoed would read as
                # something the step asserted beyond what the record says
                continue
            errors.insert(0, ("", "", f"{tally.group('verdict')} {tally.group('tally')}", "error"))
            continue
        test = TEST_FAILED.match(line)
        if test:
            detail = test.group("detail")
            errors.append(("", "", f"failing test {test.group('name')}"
                            + (f" [{detail}]" if detail else ""), "error"))
            continue
        match = DIAGNOSTIC.match(line.strip())
        if match:
            entry = (match.group("file").strip(), match.group("line"),
                     f"{match.group('id')}: {match.group('message')}", match.group("severity"))
            (errors if entry[3] == "error" else warnings).append(entry)
            continue
        if "error" not in line.lower():
            continue
        if DIAGNOSTIC.match(line.strip()):
            continue
        tool = TOOL_FAILURE.search(line)
        if tool:
            errors.append(("", "", tool.group("message").strip()[:300], "error"))
    out = []
    for file, number, message, severity in (errors + (warnings if with_warnings else []))[:limit]:
        level = SEVERITY.get(severity, "error")
        properties = []
        if file:
            properties.append(f"file={quote(file, PARAM_ESCAPES)}")
        if number:
            properties.append(f"line={quote(number, PARAM_ESCAPES)}")
        if properties:
            out.append(f"::{level} {','.join(properties)}::{quote(message)}")
        else:
            out.append(f"::{level}::{quote(message)}")
    return out


def read_log(path: str) -> str:
    """Read a build log whatever encoding its writer chose.

    This exists because the first hosted Windows run produced a build failure and no
    annotation. `Tee-Object -FilePath` on that runner wrote UTF-16, and a UTF-8 reader saw
    `e\x00r\x00r\x00` -- which contains no substring "error", so the reporter fell silent on a
    real failure. A tool whose silence reads as "nothing to report" has to be the thing that is
    fixed, not the log: sniff the byte-order mark, fall back to UTF-16 when NUL bytes alternate,
    and only then treat the file as UTF-8 with errors replaced.
    """
    with open(path, "rb") as handle:
        raw = handle.read()
    for bom, encoding in ((b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be"),
                          (b"\xef\xbb\xbf", "utf-8-sig")):
        if raw.startswith(bom):
            return raw[len(bom):].decode(encoding, errors="replace").replace("\x00", "")
    if len(raw) >= 2 and len(raw) % 2 == 0 and raw.count(b"\x00") >= len(raw) // 20:
        # No mark, but NULs everywhere the odd bytes should be: a UTF-16 write without a BOM,
        # which is exactly what PowerShell's `Tee-Object -FilePath` produces on Windows
        # PowerShell 5.1. Decoding as little-endian is the only reading that yields text here.
        return raw.decode("utf-16-le", errors="replace").replace("\x00", "")
    return raw.decode("utf-8", errors="replace").replace("\x00", "")


#: How many lines to quote when nothing was recognised. Small on purpose: an annotation is a
#: pointer, and a wall of quoted log in the Checks tab buries the pointer.
TAIL_LINES = 5
TAIL_WIDTH = 200


def tail(text: str, limit: int = TAIL_LINES) -> list[str]:
    """The last non-empty lines, each truncated -- quoted only as a last resort."""
    kept = [line.strip() for line in text.splitlines() if line.strip()]
    return [entry if len(entry) <= TAIL_WIDTH else entry[:TAIL_WIDTH - 1] + "\u2026"
            for entry in kept[-limit:]]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--in", dest="source", default="-",
                        help="log file to read, or - for stdin (the default)")
    parser.add_argument("--limit", type=int, default=20,
                        help="maximum annotations to emit (default 20)")
    parser.add_argument("--with-warnings", action="store_true",
                        help="include warnings as well as errors")
    parser.add_argument("--quiet", action="store_true",
                        help="print only the count, for a step that wants a one-line summary")
    parser.add_argument("--status", type=int, default=0,
                        help="the step's exit status; when it is non-zero and nothing matched, "
                             "the last lines of the log are quoted instead of silence")
    args = parser.parse_args(argv)

    try:
        if args.source == "-":
            text = sys.stdin.read()
        else:
            text = read_log(args.source)
    except OSError as failure:
        # Say that nothing was read. Do not claim the log was clean.
        print(f"::warning title=ci annotations::could not read {args.source}: "
              f"{type(failure).__name__}; no annotations derived from it", file=sys.stderr)
        return 0

    lines = annotate(text, limit=max(0, args.limit), with_warnings=args.with_warnings)

    # The silence that reads as "nothing wrong" is the failure mode this guard exists for.
    # Both hosted jobs failed a step and produced no annotation at all -- once because the log
    # was UTF-16, once because the shape was `project : error NU1102:` -- so "no output" was
    # indistinguishable from "no errors". When the caller says the step failed and the reporter
    # found nothing, say so and quote the end of the log, which is where a build writes why it
    # stopped. A passing step still produces exactly nothing.
    if not lines and args.status != 0:
        quoted = tail(text)
        detail = " | ".join(quoted) if quoted else "the log was empty"
        lines = [f"::warning title=ci annotations::no compiler or test diagnostic recognised "
                 f"in a step that exited {args.status}; last {len(quoted)} line(s): "
                 f"{quote(detail)}"]

    if not args.quiet:
        for line in lines:
            print(line)
    else:
        print(f"{len(lines)} annotation(s) emitted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
