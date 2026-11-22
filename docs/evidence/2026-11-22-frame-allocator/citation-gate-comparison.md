Citation gate: pristine `ae9f3e25` worktree (no slice) vs the landing tree (slice applied).

* pristine tip: `make -s citations-check` -> exit 2, 258 dead-citation lines
* landing tree: `make citations-check` -> exit 2 (inside `make check`), 516 dead-citation lines

Same count, same files: the failure is a property of this checkout, not of the change. The cited objects live only under `refs/pull/N/head`, which a default clone does not fetch, so `git cat-file -t <sha>` fails locally while GitHub answers for the same object (`gh api repos/SpicyBadran/PC5/commits/141f91f0ca192ca72972ca9afdbee8c86e6896c5` -> that sha). Three of the 258 lines, verbatim, from the pristine tip:

    [FAIL] ADVERSARIAL_REVIEW_PR26_PR27_2026-11-16.md:12: `141f91f0ca192ca72972ca9afdbee8c86e6896c5` does not name any Git object (commit citation is dead): | **Tested revision** | `141f91f0ca192ca72972ca9afdbee8c86e6896c5` (PR #27 head at the time) |
    [FAIL] ADVERSARIAL_REVIEW_PR26_PR27_2026-11-16.md:14: `141f91f` does not name any Git object (commit citation is dead): | **Historical?** | Yes — it examines `141f91f`, which is superseded by later commits. |
    [FAIL] ADVERSARIAL_REVIEW_PR26_PR27_2026-11-16.md:18: `8aab6d38` does not name any Git object (commit citation is dead): **Reviewed refs:** `main` `8aab6d38`, PR #26 `170e611`, PR #27 `141f91f`
