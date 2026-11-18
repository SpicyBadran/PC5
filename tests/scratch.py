"""Per-process scratch storage for tests that shell out to a compiled driver.

Why this module exists
----------------------
Several FAT32 tests compile a host driver once per class and then, for every case,
write an input image, run the driver, and read back an output image.  Those files
are *mutable*, and the tests originally placed them at fixed paths under
``build/test/m1i`` with fixed names (``in.img``, ``out.img``, ``lba.txt``).  Two
test-suite processes running in the same checkout therefore overwrote each other's
images mid-run, which produced large, confusing failures in tests that had nothing
to do with each other.  The defect was real, reproduced, and independent of the
code under test.

The isolation model, and why the scopes differ
----------------------------------------------
Three kinds of artifact live in a FAT32 test class, and they need different scopes:

1. **The compiled driver** (``fat32-write-check``).  Immutable once linked and safe
   for concurrent *read/execute*, but **not** safe to rebuild while another process
   is executing it: on Linux that is ``ETXTBSY`` ("Text file busy").  So the driver
   must be unique per process *and* must not be replaced while in use.  It lives in
   the per-process root.

2. **Per-run mutable outputs** (``in.img``, ``out.img``, ``lba.txt``).  Written and
   read by every ``run_op()`` call.  Unique per process is not enough if a future
   change makes tests run in parallel *within* one process (``unittest`` has no
   built-in parallel runner today, but pytest-xdist, ``concurrent.futures`` or a
   future refactor would).  So each class gets its own directory inside the
   per-process root, and each *case* gets its own directory inside that.

3. **Diagnostic artifacts on failure.**  If a case fails, the images are the only
   way to see what happened.  Deleting them eagerly would destroy the evidence, so
   a failing case keeps its own directory and a passing case removes it.  Setting
   ``PC5_KEEP_TEST_SCRATCH=1`` keeps everything unconditionally.

Nothing here touches the repository working tree, so a stray directory can never be
committed, and no test can be affected by another checkout's leftovers.

Cleanup reliability
-------------------
``addClassCleanup`` runs even when ``setUpClass`` raises part-way through, and it
runs after every case in the class has finished, so it is the right boundary for
the class tree.  Per-case cleanups run inside ``TestCase.run`` after the test body
and after a failed assertion.

Whether a case *failed* has to be read from the ``TestResult``, not from
``_Outcome.success``: ``doCleanups`` re-enters ``testPartExecutor`` for every
cleanup function, and that context manager resets ``success`` to ``True`` on
entry, so ``success`` is always ``True`` while a cleanup body executes.  Measured
on the project interpreter — see the addendum in
``FOLLOWUP_EVIDENCE_F1_F5_2026-11-16.md``.

A process that is ``SIGKILL``ed leaves its directory behind by design (nothing can
clean up after a kill); those directories are named ``pc5-tests-<pid>-*`` under
the system temp dir and are harmless, and ``atexit`` handles the ordinary exit
path.

The layout is worth stating exactly, because the directory names mislead.  The
per-process root has no fixed shape; each class adds a tree under its own name,
and that tree is *itself* a ``case-*`` directory holding the compiled driver -
that is the path ``drop_class_tree`` receives.  Per-case images live deeper
still, under ``<module>/<Class>/<test_name>/case-*``.  So the class-level
``case-*`` and the case-level ``case-*`` are different things: the first is
removed at the class boundary, the second survives when its case failed.

This module deliberately uses only the standard library, because the defect it
fixes is about file paths and does not justify a new dependency.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile

#: Set to any non-empty value to keep scratch directories even when tests pass.
KEEP_ENV = "PC5_KEEP_TEST_SCRATCH"

_ROOTS: dict[int, str] = {}


def _keep_requested() -> bool:
    return bool(os.environ.get(KEEP_ENV))


def _drop(path: str) -> None:
    """Remove a scratch tree unless the caller asked to keep it."""
    if _keep_requested():
        return
    shutil.rmtree(path, ignore_errors=True)


def root() -> str:
    """The scratch root for this process, created on first use.

    Unique per process, so two suite runs in one checkout cannot collide.  It is
    registered with ``atexit`` so an ordinary interpreter exit cleans it up even if
    a test never reaches its teardown.
    """
    key = os.getpid()
    existing = _ROOTS.get(key)
    if existing is not None and os.path.isdir(existing):
        return existing
    path = tempfile.mkdtemp(prefix=f"pc5-tests-{key}-")
    _ROOTS[key] = path
    atexit.register(_drop, path)
    return path


def directory(*parts: str, prefix: str = "") -> str:
    """A fresh directory inside this process's scratch root.

    ``parts`` name the owner (typically the test module and class) so a leftover
    directory says what produced it.  The returned directory is unique, so callers
    may use fixed filenames inside it without colliding with anything else.
    """
    parent = os.path.join(root(), *parts) if parts else root()
    os.makedirs(parent, exist_ok=True)
    return tempfile.mkdtemp(prefix=prefix or "case-", dir=parent)


def _outcome_owner(test):
    """The ``TestCase`` a result entry is about.

    A failed ``subTest`` is reported as a ``_SubTest`` whose ``test_case`` is the
    case the caller knows; a plain failure is reported as the case itself.
    """
    return getattr(test, "test_case", test)


def case_failed(case) -> bool:
    """Whether ``case`` is recorded as failed, errored or unexpectedly successful.

    Read from the ``TestResult`` that ``unittest`` attached to the case as
    ``_outcome.result``.  This is the only signal that is both correct at cleanup
    time and visible there:

    * ``_Outcome.success`` is **always** ``True`` inside a cleanup body, because
      ``doCleanups`` wraps each cleanup in ``testPartExecutor``, which sets
      ``success = True`` on entry.  Measured, not assumed.
    * ``_Outcome`` has no ``errors``/``failures`` attributes at all.

    The result lists are appended to *before* ``doCleanups`` runs, so scanning
    them is correct at every point a cleanup can observe.  Anything unusual — no
    ``_outcome``, no ``result``, a result that is not the standard one — keeps the
    tree, because losing a diagnostic artifact is worse than leaving a directory.
    """
    outcome = getattr(case, "_outcome", None)
    result = getattr(outcome, "result", None) if outcome is not None else None
    if result is None:
        return True
    for attribute in ("failures", "errors", "unexpectedSuccesses"):
        try:
            entries = getattr(result, attribute) or ()
        except Exception:  # a non-standard result object: keep the evidence
            return True
        for entry in entries:
            test = entry[0] if isinstance(entry, tuple) and entry else entry
            if _outcome_owner(test) is case:
                return True
    return False


def register_cleanup(case, path: str, *, keep_on_failure: bool = True) -> None:
    """Arrange for ``path`` to be removed when ``case`` (a TestCase) finishes.

    ``unittest`` runs ``doCleanups`` after ``tearDown`` and after a failed
    assertion, so this is reached on every outcome except a hard kill.

    With ``keep_on_failure`` the directory survives when *this* case did not pass,
    because the images are the diagnostic artifact.  Only this case's own result
    counts, so one failing test never retains another test's directory.
    """
    def _cleanup():
        if keep_on_failure and case_failed(case):
            return
        _drop(path)
        if not _keep_requested():
            _prune_empty_parents(path, root())

    case.addCleanup(_cleanup)


def _prune_empty_parents(path: str, stop: str) -> None:
    """Remove ``path``'s now-empty parent directories, up to but not including ``stop``.

    Containment is compared on whole path components: a plain ``startswith``
    would treat ``/tmp/pc5-tests-1-abcdef`` as inside ``/tmp/pc5-tests-1-abc``.
    """
    parent = os.path.dirname(os.path.abspath(path))
    stop = os.path.abspath(stop)
    while parent != stop and parent.startswith(stop + os.sep):
        try:
            os.rmdir(parent)  # only succeeds when empty
        except OSError:
            return
        parent = os.path.dirname(parent)


def drop_class_tree(path: str, *, case=None) -> None:
    """Remove a class-level tree at the class boundary.

    Registered with ``addClassCleanup``, which ``unittest`` runs after the last
    case in the class and also when ``setUpClass`` raises part-way through.

    ``path`` is the class's own ``case-*`` directory and holds the compiled
    driver, not the per-case images: those live deeper, under
    ``<module>/<Class>/<test_name>/case-*``, and are removed or retained by their
    own case cleanups.  Removing this tree therefore cannot destroy a failed
    case's artifacts.  ``PC5_KEEP_TEST_SCRATCH=1`` keeps it.
    """
    if _keep_requested():
        return
    shutil.rmtree(path, ignore_errors=True)
    _prune_empty_parents(path, root())


def describe(path: str) -> str:
    """A short, copy-pasteable description of a scratch path for messages."""
    return path if os.path.isdir(path) else f"{path} (removed)"
