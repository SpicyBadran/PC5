"""Per-file triage of the host test suite, for use from CI.

`python3 -m unittest discover` reports nothing when the interpreter *dies*: a
segmentation fault ends the run at the shell (`Process completed with exit code 139`),
the log stops mid-line, and there is no `FAIL:` block to quote because no test failed.
The pipeline hit exactly that on its third run, and the only way to name the offending
module is to run the files one at a time and report the first death.

This is a diagnostic, never a gate:

* it runs the same modules `discover` runs, in its own subprocess per file, and writes
  only the file named by `--json-out`;
* each non-zero outcome is announced as one workflow command per line, so a restricted
  caller can read the result without the run page (see `ci/README.md`, "Reading a failed
  run": logs and artifacts live on hosts a sandbox may not reach, annotations do not);
* it always exits 0, including when a file segfaults. The pipeline's verdict belongs to
  the real suite; a step that could fail the job would let the observer change what is
  observed, which is the trap this whole file exists to avoid.

Usage:

    python3 tools/ci_triage.py [--root tests] [--python python3] [--json-out FILE]
                          [--budget SECONDS]
"""

import json
import os
import re
import subprocess
import sys
import time

#: A module gets its own interpreter, so a crash costs one file and not the run.  The
#: slowest legitimate file in this suite (the emulator-backed ones) is a few minutes.
TIMEOUT_SECONDS = 900
#: Start-to-finish budget for the triage itself, so it cannot outrun the job it serves.
TIME_BUDGET_SECONDS = 780
#: Annotate at most this many broken files: enough to see a pattern, few enough that the
#: annotation list stays readable.
MAX_ANNOUNCE = 40

EXIT_NAMES = {
    -4: "SIGILL",
    -6: "SIGABRT",
    -9: "SIGKILL (out of memory, or killed by the runner)",
    -11: "SIGSEGV",
    132: "SIGILL (128+4)",
    134: "SIGABRT (128+6)",
    137: "SIGKILL (128+9)",
    139: "SIGSEGV (128+11)",
    124: "timeout: the file ran past its deadline",
    127: "the interpreter could not be started at all",
}


def test_files(root):
    """The modules `unittest discover -p 'test_*.py'` would load, in the same order."""
    try:
        names = os.listdir(root)
    except OSError:
        return []
    return sorted(n for n in names if re.fullmatch(r"test_.*\.py", n))


def outcome_name(rc):
    if rc == 0:
        return "passed"
    if rc in EXIT_NAMES:
        return EXIT_NAMES[rc]
    if rc < 0:
        return "killed by signal %d" % -rc
    return "exit %d" % rc


def last_lines(text, count=3):
    return [l for l in text.splitlines() if l.strip()][-count:]


def run_file(python, cwd, module, timeout):
    """(returncode, output tail) for one test module, in a fresh interpreter."""
    try:
        done = subprocess.run([python, "-m", "unittest", "-v", module], cwd=cwd,
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout or ""
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", "replace")
        return 124, partial
    except OSError as exc:
        return 127, "%s: %s" % (type(exc).__name__, exc)
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def parse(argv):
    opts = {"root": "tests", "json_out": "", "python": sys.executable or "python3",
            "budget": TIME_BUDGET_SECONDS}
    i = 0
    while i < len(argv):
        key = argv[i]
        if key in ("-h", "--help"):
            print(__doc__)
            return None, opts
        if key in ("--root", "--json-out", "--python", "--budget") and i + 1 < len(argv):
            name = key[2:].replace("-", "_")
            value = argv[i + 1]
            if name == "budget":
                try:
                    value = float(value)
                except ValueError:
                    print("ci_triage: --budget wants a number, got %r; keeping the default"
                          % argv[i + 1], file=sys.stderr)
                    value = TIME_BUDGET_SECONDS
            opts[name] = value
            i += 2
            continue
        print("ci_triage: ignoring unknown argument %r" % key, file=sys.stderr)
        i += 1
    return True, opts


def main(argv=None):
    ok, opts = parse(list(sys.argv[1:] if argv is None else argv))
    if ok is None:
        return 0
    root = opts["root"]
    files = test_files(root)
    if not files:
        print("::warning title=triage::no test files under %s; nothing to say" % root)
        return 0
    started = time.time()
    results, announced, stopped_at = [], 0, ""
    for name in files:
        if time.time() - started > float(opts["budget"]):
            stopped_at = name
            break
        rc, text = run_file(opts["python"], os.path.dirname(os.path.abspath(root)) or ".",
                            "tests." + name[:-3], TIMEOUT_SECONDS)
        results.append({"file": name, "returncode": rc, "outcome": outcome_name(rc),
                        "tail": last_lines(text),
                        "seconds": round(time.time() - started, 1)})
        if rc != 0 and announced < MAX_ANNOUNCE:
            print("::error title=%s::%s | %s"
                  % (name, outcome_name(rc), " / ".join(results[-1]["tail"])[-1200:]))
            announced += 1
    bad = [r for r in results if r["returncode"] != 0]
    note = ("ran %d of %d files in %.0fs; %d did not return 0; interpreter %s"
            % (len(results), len(files), time.time() - started, len(bad), opts["python"]))
    if stopped_at:
        note += "; budget exhausted, not reached: " + stopped_at
    print("::notice title=triage::" + note)
    if opts["json_out"]:
        try:
            with open(opts["json_out"], "w", encoding="utf-8") as handle:
                json.dump({"note": note, "files_total": len(files), "files_run": len(results),
                           "nonzero": len(bad), "stopped_at": stopped_at,
                           "interpreter": opts["python"],
                           "results": bad}, handle, indent=1)
        except OSError as exc:
            print("::warning title=triage::could not write %s: %s"
                  % (opts["json_out"], exc))
    print(note)
    return 0


if __name__ == "__main__":
    # Belt and braces on the exit-code promise: a diagnostic that can fail the job it is
    # describing is not a diagnostic.  Any crash here is announced and swallowed; the
    # suite's own status is the pipeline's business, not this script's.
    try:
        _code = main()
    except BaseException:
        import traceback
        print("::warning title=triage::crashed while diagnosing: "
              + traceback.format_exc(limit=2).replace(chr(10), " | ")[:1200])
        _code = 0
    raise SystemExit(_code)
