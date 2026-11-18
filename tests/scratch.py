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

3. **Diagnostic artifacts on failure.**  If a case did not do what it claimed,
   the images are the only way to see what happened.  Deleting them eagerly would
   destroy the evidence, so such a case keeps its own directory and a case that
   passed removes it.  "Did not do what it claimed" covers a failure, an error and
   an unexpected success - the three outcomes ``unittest`` counts as a failing
   run.  An ``@expectedFailure`` case that failed *as promised* is a pass and keeps
   nothing.  Setting ``PC5_KEEP_TEST_SCRATCH=1`` keeps everything unconditionally.

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
on the project interpreter — see the addenda in
``FOLLOWUP_EVIDENCE_F1_F5_2026-11-16.md`` (§10 and §11).

One outcome cannot be read from the result at all: ``TestCase.run`` calls
``doCleanups()`` *before* it appends an unexpected success to
``unexpectedSuccesses``, so that list is empty in every cleanup.  It is
reconstructed instead, from the expected-failure marker and the absence of a
recorded expected failure.  Also measured on the project interpreter.

The decision is made at cleanup time, so it cannot see a failure that a *later*
cleanup will cause.  Cleanups run LIFO, so ``register_cleanup`` must be called
before any other cleanup that might raise - see its docstring.  Retention of
evidence is therefore guaranteed for failures of the test body and of earlier
cleanups, not for an exception raised after this cleanup has run.

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
from pathlib import Path

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

    Every part and the ``prefix`` are single path components, and the result is
    guaranteed to be inside :func:`root`.  Without that, ``directory("..",
    "elsewhere")`` built a path outside the root, and because cleanup and ``atexit``
    remove only the root, the escaped directory survived the run: a leak that no test
    would report and that ``PC5_KEEP_TEST_SCRATCH`` could not account for.
    Containment is checked on *resolved* paths, not string prefixes --
    ``/tmp/pc5-tests-1-abc`` is a prefix of ``/tmp/pc5-tests-1-abcdef``, and a
    symlinked component would defeat a prefix test while still escaping.

    ``prefix`` is validated on the same terms as the parts, and *before* anything is
    created.  ``tempfile.mkdtemp`` joins the prefix onto ``dir`` textually, so a
    prefix of ``"../../../"`` or an absolute path names a directory outside the root
    and ``mkdtemp`` creates it.  Checking containment afterwards would then report
    the escape while leaving the escaped directory on disk -- the check would
    describe the damage instead of preventing it.  Validating first is what makes
    the guarantee hold; the post-creation checks below are defence in depth.
    """
    base = root()
    safe = [_component(part, "part") for part in parts]
    # Rejected here, before ``os.makedirs`` and before ``mkdtemp``: an invalid
    # prefix must not be able to create anything, anywhere.
    safe_prefix = _component(prefix, "prefix") if prefix else ""

    parent = os.path.join(base, *safe) if safe else base

    # Before creating anything: a component here may be a pre-existing symlink
    # pointing out of the root, and makedirs must not be allowed to follow it.
    _require_within(base, parent)
    os.makedirs(parent, exist_ok=True)
    # Again after creation, so a component created concurrently cannot slip past.
    _require_within(base, parent)

    path = tempfile.mkdtemp(prefix=safe_prefix or "case-", dir=parent)
    _require_within(base, path)
    return path


def _component(part, kind: str = "part") -> str:
    """Validate one path component of a scratch directory name.

    Rejects everything that could make the name land outside ``root``: an empty
    name, an absolute path, a drive or UNC prefix, an embedded separator, ``.``/
    ``..``, and an embedded NUL.  Comparison is by component, so ``"a..b"`` and
    ``"..a"`` -- ordinary names -- are accepted; only a part that *is* ``..`` is
    traversal.

    ``kind`` only names the argument in the message.  Parts and ``prefix`` are one
    rule, not two: a validator that is stricter in one place than the other is
    exactly how the prefix came to be unguarded while the parts were not, and
    ``os.path.join`` and ``tempfile.mkdtemp`` join both the same way.
    """
    if isinstance(part, os.PathLike):
        part = os.fspath(part)
    if not isinstance(part, str):  # bytes paths are not supported deliberately
        raise TypeError(
            f"scratch directory {kind} must be a path component, not {type(part).__name__}")
    if not part:
        raise ValueError(f"scratch directory {kind} must not be empty")
    if "\x00" in part:
        raise ValueError(f"scratch directory {kind} contains a NUL: {part!r}")
    drive, tail = os.path.splitdrive(part)
    if drive:
        raise ValueError(f"scratch directory {kind} must not name a drive: {part!r}")
    if os.path.isabs(part) or tail.startswith(os.sep):
        raise ValueError(f"scratch directory {kind} must be relative: {part!r}")
    separators = {os.sep} | ({os.altsep} if os.altsep else set())
    if any(separator in part for separator in separators):
        raise ValueError(f"scratch directory {kind} must be a single component: {part!r}")
    if part in (os.pardir, os.curdir):
        raise ValueError(f"scratch directory {kind} must not be {part!r}")
    return part


def _require_within(base: str, path: str) -> None:
    """Raise unless ``path`` resolves inside ``base``.

    ``Path.resolve()`` collapses ``..`` and follows symlinks, so this answers the
    question that matters -- where would a write actually land -- rather than
    whether the string looks right.  ``is_relative_to`` compares whole components,
    which a ``startswith`` check does not.
    """
    resolved_base = Path(base).resolve()
    resolved = Path(path).resolve()
    if resolved != resolved_base and not resolved.is_relative_to(resolved_base):
        raise ValueError(
            f"scratch directory would escape its root: {path} resolves to "
            f"{resolved}, outside {resolved_base}"
        )


def _outcome_owner(test):
    """The ``TestCase`` a result entry is about.

    A failed ``subTest`` is reported as a ``_SubTest`` whose ``test_case`` is the
    case the caller knows; a plain failure is reported as the case itself.
    """
    return getattr(test, "test_case", test)


def _expecting_failure(case) -> bool:
    """Whether ``case`` declares that it is expected to fail.

    Mirrors how ``TestCase.run`` computes it: a marker on the instance or on the
    test method function.  Read from the attributes ``unittest`` itself sets, so
    no inspection of user code is involved.
    """
    if getattr(case, "__unittest_expecting_failure__", False):
        return True
    method = getattr(case, getattr(case, "_testMethodName", ""), None)
    return bool(getattr(method, "__unittest_expecting_failure__", False))


def _unexpected_success(case, outcome) -> bool:
    """Whether ``case`` is an ``@expectedFailure`` case that ran without failing.

    This has to be reconstructed, because ``TestResult.unexpectedSuccesses``
    cannot answer the question at cleanup time.  ``TestCase.run`` calls
    ``doCleanups()`` *before* it calls ``_addUnexpectedSuccess(result)``:

        self.doCleanups()
        if outcome.success:
            if expecting_failure:
                ... self._addUnexpectedSuccess(result)
            else:
                result.addSuccess(self)

    so inside a cleanup ``unexpectedSuccesses`` is still empty and
    ``outcome.expecting_failure`` has already been reset to ``False``.  Checking
    that list here would therefore never fire, and the case directory would be
    deleted for an outcome the suite reports as a failure.

    The visible signals are: the case carries the expected-failure marker, it did
    not fail (``outcome.expectedFailure`` is unset - ``testPartExecutor`` sets it
    only when an ``@expectedFailure`` case actually raises), and ``outcome.success``
    is true.  ``success`` is reliable *here* because ``testPartExecutor`` restores
    it to its pre-test value on exit (``self.success = self.success and old_success``),
    so by cleanup time it holds the outcome of the test body rather than that of a
    cleanup function.
    """
    if not _expecting_failure(case):
        return False
    if getattr(outcome, "expectedFailure", None) is not None:
        return False
    return bool(getattr(outcome, "success", True))


def case_failed(case) -> bool:
    """Whether ``case`` is failed, errored or an unexpected success.

    "Unusable" is the question, not "did an assertion raise": all three outcomes
    mean the case did not do what it claimed, and all three are worth keeping the
    images for.  The suite counts an unexpected success as a failure - it exits
    non-zero - so retaining its artifacts follows the same policy.

    Read from the ``TestResult`` that ``unittest`` attached to the case as
    ``_outcome.result``, plus one judgement the result cannot supply yet:

    * ``_Outcome.success`` is **always** ``True`` inside a cleanup body, because
      ``doCleanups`` wraps each cleanup in ``testPartExecutor``, which sets
      ``success = True`` on entry.  Measured, not assumed.
    * ``_Outcome`` has no ``errors``/``failures`` attributes at all.
    * ``unexpectedSuccesses`` is populated only *after* ``doCleanups`` runs; see
      ``_unexpected_success``.

    The failure and error lists are appended to *before* ``doCleanups`` runs, so
    scanning them is correct at every point a cleanup can observe.  Anything
    unusual - no ``_outcome``, no ``result``, a result that is not the standard
    one - keeps the tree, because losing a diagnostic artifact is worse than
    leaving a directory behind.
    """
    outcome = getattr(case, "_outcome", None)
    if outcome is None:
        return True
    result = getattr(outcome, "result", None)
    if result is None:
        return True
    try:
        for attribute in ("failures", "errors", "unexpectedSuccesses"):
            entries = getattr(result, attribute) or ()
            for entry in entries:
                test = entry[0] if isinstance(entry, tuple) and entry else entry
                if _outcome_owner(test) is case:
                    return True
        return _unexpected_success(case, outcome)
    except Exception:  # a non-standard result object: keep the evidence
        return True


def register_cleanup(case, path: str, *, keep_on_failure: bool = True) -> None:
    """Arrange for ``path`` to be removed when ``case`` (a TestCase) finishes.

    ``unittest`` runs ``doCleanups`` after ``tearDown`` and after a failed
    assertion, so this is reached on every outcome except a hard kill.

    With ``keep_on_failure`` the directory survives when *this* case did not pass,
    because the images are the diagnostic artifact.  Only this case's own result
    counts, so one failing test never retains another test's directory.

    Cleanup ordering (LIFO) and its one hazard
    ------------------------------------------
    ``addCleanup`` functions run in **reverse registration order**, and the
    decision above is taken when *this* cleanup runs.  A cleanup function that
    raises is recorded by ``unittest`` as an error of the same case.  Therefore:

    * if this cleanup runs **after** the raising one, the error is already in the
      result and the directory is correctly kept;
    * if this cleanup runs **before** it, ``case_failed()`` still sees a pass,
      the directory is removed, and the error is recorded a moment later - the
      test failed and its evidence is already gone.

    So call this *before* adding any other cleanup that could raise: registered
    first, it runs last, and it observes everything.  All current call sites do
    that (they are the only cleanup on the case), which is why this is a stated
    constraint rather than an observed defect.  It cannot be fixed from inside
    this module: at the earlier instant the failure genuinely has not happened
    yet, so there is nothing to read.
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
