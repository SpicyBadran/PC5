# PC5's licence

This notice is where PC5 states its own licensing decision. It lives outside
[`LICENSE`](LICENSE) on purpose: that file is the verbatim GNU General Public License
version 3, and a project's grant must not be spliced into the standard text it is quoting.
Read the two files together — this one says what PC5 does, `LICENSE` says what it means.

PC5 is licensed under the **GNU General Public License version 3 only** (`GPL-3.0-only`).

Those terms, and no others, apply to everything tracked in this repository — `boot/`,
`kernel/`, `kernel64/`, `docs/`, `tests/`, `tools/` and the root scripts.

## Why version 3, and why *only*

PC5 distributes a bootable image. Its obligations run from the published recipe, through
`SOURCE_DATE_EPOCH`, to a `build-info.json` that carries the hash of what was actually
verified, so the terms under which the source travels with that image are load-bearing
engineering decisions rather than boilerplate. That is the whole of the reasoning, and it
lives in
[ADR-027](docs/TECHNICAL_DECISIONS.md#adr-027--pc5s-licence-is-gpl-30-only-and-the-grant-is-stated-outside-the-licence-body),
which records what was considered and what would reverse it.

The version is *only*, not *or later*. PC5 ships no *or any later version* clause because it
has run no compatibility review against a licence it has not chosen, and such a clause is a
promise about future terms rather than a description of present ones. Widening the grant is
allowed — it is simply not allowed to happen by accident: anyone who wants the later-versions
alternative has to say so in this file, and no tool here will infer it. The README table,
`docs/ABOUT.md` §1 and every identifier in the controlled documents are derived from the
grant above, so an undocumented change cannot land silently.

Copyright in PC5's sources is held by its contributors, and this notice deliberately names no
single holder and no year range. That leaves one question genuinely open, and it is stated
rather than papered over: no file in the tree carries a copyright line and the repository has
no contributor agreement, so the exact set of licensors whose permission a future relicensing
would need is not recorded anywhere. Naming a holder would not resolve that — it would only
make a false statement look like a resolved one. The section at the end of this file lists what
would have to be true before a per-file `SPDX-License-Identifier` could be added.

## Components that are not PC5's to license

PC5 vendors no third-party **source** archive: no upstream source tree is copied in, and
this is a build rule, not a preference
([ADR-025](docs/TECHNICAL_DECISIONS.md#adr-025--pc5-vendors-no-third-party-source-archive-and-borrows-no-project-identity)).
That rule bounds the GPL's reach, and it does not make the licence a formality, because a
build still consumes things PC5 does not license at all:

* **Host toolchain** — the compiler, linker, assembler and Python that produce the image are
  used under their own terms and are never incorporated into `build/pc5.iso`.
* **OVMF / EDK II firmware** — a UEFI firmware build fetched for the boot gate, never copied
  into the repository, never distributed by PC5, and absent from every release artifact.
  Results produced against it are described as emulator results
  ([`docs/TEST_RESULTS.md`](docs/TEST_RESULTS.md),
  [`docs/HARDWARE_COMPATIBILITY.md`](docs/HARDWARE_COMPATIBILITY.md)).
* **A GitHub Actions runner image**, used to build and to publish.
* **No third-party asset source, and no image blob.** Every mark is generated from
  `tools/brand.py` into `brand/*.svg`, so there is no imported font, image or binary whose
  notices would have to be preserved here.
* **`PS5UPDATE.PUP` is a pointer, and what it points at is not ours.** The 135-byte file at the
  repository root is a Git LFS pointer to a 1,423,697,408-byte system-software package that is
  proprietary to its publisher. It is a protected external input, never a payload in the tree
  ([ADR-022](docs/TECHNICAL_DECISIONS.md#adr-022--the-ps5-system-software-package-is-a-protected-input-never-a-payload-in-the-tree)),
  and no sentence in this notice licenses it. Its ownership is *resolved* in one direction only:
  it belongs to somebody else, which is exactly why it is listed here rather than covered by the
  grant above.

## What the tooling checks

A generic copy of the GPL text is not evidence of a project declaration, and a project
declaration is not an edit of the official text. Both halves are enforced:

* `make presentation` derives the licence identifier **only** from a grant sentence in
  `LICENSING.md` that names PC5. `tools/licensing.py::read` — the one reader both the renderer
  and the gate call — will not read the FSF's *How to Apply These Terms to Your New Programs*
  template ("either version 3 of the License, or (at your option) any later version") as this
  project's decision, refuses a grant that omits a version or an *only* / *any later version*
  clause, and refuses a notice whose asserted identifier disagrees with its own grant sentence.
* `make docs-check` fails on either contradiction: a `LICENSE` that is not verbatim
  `GPL-3.0-only`, or a `LICENSING.md` that does not grant PC5 those exact terms.
* `make presentation-check` refuses a hand-written identifier in any controlled document, so
  a licence change cannot land in prose while the derived row still shows the old one.

## Before a per-file header could be added

A `SPDX-License-Identifier` comment in a source file is a copyright statement. For it to be
true, and for it not to break the evidence this repository keeps about its own sources, three
things are needed first, and none of them is a formatting job:

1. confirmation of the licence by whoever holds the copyright in each file;
2. the same confirmation for any contributor whose work is already in the tree;
3. a re-baseline of every release-reproducibility digest that hashes a source file, since a
   header changes bytes and therefore changes hashes — the ISO digest is deliberately
   insensitive to `docs/` and `tools/`, and would need re-measuring under whatever rule the
   headers land on.
