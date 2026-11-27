"""PC5 stands on its own: no third-party archive, no borrowed identity.

This repository began life with a single third-party source archive committed at
its root. That archive was removed, together with the vendor note and the fetch
script that mentioned it, so that PC5's tree describes PC5 and nothing else. This
test is the guard that keeps it that way: it fails if a third-party archive
returns, if the project's name reappears anywhere in the tracked tree, or if the
removed archive comes back as a Git LFS pointer under another name.

Why a test rather than a convention: the archive arrived by a drag-and-drop
upload, which is the easiest thing in the world to do twice. A rule nobody checks
would not have caught it the first time either.

The forbidden token is assembled from parts on purpose — writing it out here
would reintroduce exactly the string this file exists to forbid.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import licensing  # noqa: E402 -- the one reader the gates and the pages share

#: The upstream project's name, assembled so this file does not contain it.
FORBIDDEN_TOKEN = "any" + "ps5"

#: The Git LFS object id of the archive that was removed. The payload was never
#: fetched; if this oid reappears anywhere, the archive came back.
REMOVED_LFS_OID = "eb4fd557e1611e95c0b73f93043b0708d5e4c7e796e29016e54fbccc5c874bb2"

#: Files that are allowed to be large or binary enough to skip content scanning
#: are handled by detection, not by a list: a file with NUL bytes in its head is
#: treated as binary and its *path* is still checked.
TEXT_SNIFF = 8192

#: This file has to contain the removed archive's object id in order to forbid it,
#: so it is the one file the content scan skips -- by name, not by pattern, and the
#: skip is asserted below rather than assumed.
SELF = Path("tests/test_project_independence.py")


def tracked_files() -> list[Path]:
    """Every file git tracks, as repository-relative paths."""
    out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO,
                         capture_output=True, check=True).stdout
    return [Path(name.decode()) for name in out.split(b"\0") if name]


def looks_binary(path: Path) -> bool:
    try:
        head = path.read_bytes()[:TEXT_SNIFF]
    except OSError:
        return True
    return b"\0" in head


class NoThirdPartyProjectReferenceTest(unittest.TestCase):
    def test_no_tracked_path_names_the_third_party_project(self):
        offenders = [str(p) for p in tracked_files()
                     if FORBIDDEN_TOKEN in str(p).lower()]
        self.assertEqual(offenders, [],
                         "these tracked paths name the removed third-party project: "
                         f"{offenders}")

    def test_no_tracked_text_mentions_the_third_party_project(self):
        offenders = []
        for path in tracked_files():
            full = REPO / path
            if path == SELF or looks_binary(full):
                continue
            text = full.read_text(errors="ignore").lower()
            if FORBIDDEN_TOKEN in text:
                line = next((n for n, row in enumerate(text.splitlines(), 1)
                             if FORBIDDEN_TOKEN in row), 0)
                offenders.append(f"{path}:{line}")
        self.assertEqual(offenders, [],
                         "these tracked files mention the removed third-party project: "
                         f"{offenders}")

    def test_no_archive_at_the_repository_root(self):
        archives = [str(p) for p in tracked_files()
                    if p.parent == Path(".") and p.suffix.lower() == ".zip"]
        self.assertEqual(archives, [],
                         f"a source archive is tracked at the repository root: {archives}")

    def test_the_removed_archive_cannot_return_as_an_lfs_pointer(self):
        offenders = []
        for path in tracked_files():
            full = REPO / path
            if path == SELF or looks_binary(full):
                continue
            if REMOVED_LFS_OID in full.read_text(errors="ignore"):
                offenders.append(str(path))
        self.assertEqual(offenders, [],
                         "the removed archive's LFS object id is referenced by: "
                         f"{offenders}")

    def test_only_the_guard_itself_carries_the_removed_object_id(self):
        holders = [str(p) for p in tracked_files()
                   if p != SELF and not looks_binary(REPO / p)
                   and REMOVED_LFS_OID in (REPO / p).read_text(errors="ignore")]
        self.assertEqual(holders, [], f"the removed object id is referenced by: {holders}")

    def test_the_vendor_directory_is_gone(self):
        vendored = [str(p) for p in tracked_files() if str(p).startswith("vendor/")]
        self.assertEqual(vendored, [], f"the vendor directory is back: {vendored}")


class Pc5OwnsItsIdentityTest(unittest.TestCase):
    """The repository must say what PC5 is, in PC5's own terms."""

    def setUp(self):
        self.readme = (REPO / "README.md").read_text()
        self.licence = (REPO / "LICENSE").read_text()
        self.notice = (REPO / "LICENSING.md").read_text()
        self.decisions = (REPO / "docs/TECHNICAL_DECISIONS.md").read_text()

    def test_the_licence_is_pc5_s_own(self):
        """PC5 speaks for itself in `LICENSING.md`; `LICENSE` stays the standard document.

        Three separate properties, because one file cannot carry all three: the notice grants
        in PC5's own person, the official body is verbatim and therefore mentions no project at
        all, and the reader both gates share resolves exactly one identifier out of the pair.
        Asserting a sentence *inside* the licence text — which an earlier version of this test
        did — would have required PC5's wording to be pasted into the GPL to make it pass, which
        is the thing ADR-027 refuses to do.
        """
        result = licensing.read(REPO)
        self.assertEqual(result["spdx"], "GPL-3.0-only", result["problem"])
        granted = licensing.plain(self.notice)
        self.assertIn("PC5 is licensed under the GNU General Public License version 3 only",
                      granted, "the notice grants in PC5's own person, in one sentence")
        self.assertIn("PC5 vendors no third-party source archive", granted,
                      "and restates the build rule the GPL's reach depends on")
        self.assertIn("GNU GENERAL PUBLIC LICENSE", self.licence)
        self.assertIn("Version 3, 29 June 2007", self.licence)
        self.assertNotIn("PC5", self.licence,
                         "the official text carries no project-specific wording")
        self.assertNotIn("No third-party source archive is", self.licence,
                         "the vendoring rule belongs to ADR-025 and the notice, not to the GPL")
        self.assertEqual(hashlib.sha256(self.licence.encode("utf-8")).hexdigest(),
                         licensing.GPL3_ONLY_SHA256,
                         "`LICENSE` is the adopted document, byte for byte")

    def test_the_readme_states_the_destination_in_pc5_s_own_words(self):
        self.assertIn("genuine Sony PlayStation 5 system software", self.readme)

    def test_the_licence_decision_is_recorded_beside_the_no_vendoring_one(self):
        self.assertIn("## ADR-027", self.decisions)
        self.assertIn("GPL-3.0-only", self.decisions)
        self.assertIn("`LICENSING.md`", self.decisions)

    def test_the_no_vendoring_decision_is_recorded(self):
        self.assertIn("## ADR-025", self.decisions)
        self.assertIn("PC5 vendors no third-party source archive", self.decisions)

    def test_the_cleanup_is_recorded_where_the_audit_recorded_the_problem(self):
        audit = (REPO / "docs/PROJECT_AUDIT.md").read_text()
        self.assertIn("since been removed", audit)
        self.assertIn("test_project_independence.py", audit)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
