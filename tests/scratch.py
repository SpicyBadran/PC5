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

3. **Diagnostic artifacts on failure.**  If a run fails, the images are the only way
   to see what happened.  Deleting them eagerly would destroy the evidence, so the
   root is kept when a test fails and removed when everything passed.  Setting
   ``PC5_KEEP_TEST_SCRATCH=1`` keeps it unconditionally.

Nothing here touches the repository working tree, so a stray directory can never be
committed, and no test can be affected by another checkout's leftovers.

Cleanup reliability
-------------------
``mkdtemp`` is removed by ``unittest``'s ``addClassCleanup``, which runs even when
``setUpClass`` raises part-way through or a test raises.  A process that is
``SIGKILL``ed leaves its directory behind by design (nothing can clean up after a
kill); those directories are named ``pc5-tests-<pid>-*`` under the system temp dir
and are harmless, and ``atexit`` handles the ordinary exit path.

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
_PROCESS_FAILURE = [False]


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


def register_cleanup(case, path: str, *, keep_on_failure: bool = True) -> None:
    """Arrange for ``path`` to be removed when ``case`` (a TestCase) finishes.

    ``unittest`` runs ``doCleanups`` after ``tearDown`` and after a failed
    assertion, so this is reached on every outcome except a hard kill.

    With ``keep_on_failure`` the tree survives when the test did not pass, because
    the images are the diagnostic artifact.  That is decided from
    ``case._outcome``, which is how ``unittest`` itself records results; if the
    attribute is absent the tree is kept (failing safe towards evidence).
    """
    def _cleanup():
        outcome = getattr(case, "_outcome", None)
        failed = True
        if outcome is not None:
            try:
                failed = any(
                    error is not None
                    for _, error in outcome.errors + outcome.failures
                )
            except AttributeError:
                failed = True
        if failed:
            note_failure()
            if keep_on_failure:
                return
        _drop(path)

    case.addCleanup(_cleanup)


def drop_class_tree(path: str, *, case=None) -> None:
    """Remove a class-level tree unless a test in it failed.

    Registered with ``addClassCleanup``.  It cannot see individual results the way
    a per-case cleanup can, so the safe default is to keep the tree whenever the
    process has recorded any failure at all; a passing run still cleans up, and
    ``PC5_KEEP_TEST_SCRATCH=1`` overrides either way.
    """
    if _keep_requested():
        return
    if _PROCESS_FAILURE[0]:
        return
    shutil.rmtree(path, ignore_errors=True)


def note_failure() -> None:
    """Record that at least one test failed, so class trees are preserved.

    Called from the per-case cleanup of every case that uses a scratch tree.
    """
    _PROCESS_FAILURE[0] = True


def describe(path: str) -> str:
    """A short, copy-pasteable description of a scratch path for messages."""
    return path if os.path.isdir(path) else f"{path} (removed)"
