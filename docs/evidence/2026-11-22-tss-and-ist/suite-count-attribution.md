# Suite-count attribution: 971, 994, 1011 and 1019 are all correct, of different trees

This began as a correction and is not one. `commands-and-exit-codes.txt` here records
`Ran 971 tests in 357s … OK` for `make check`, and #45's description quotes `Ran 1019`. A
48-test gap between two documents about "the suite" is the kind of thing that means someone is
comparing numbers from different trees, so it was measured rather than argued — and the
measurements say nobody was wrong.

Counting collected tests, without running them, at each tree in the chain:

| tree | what it is | collected | import errors |
|---|---|---|---|
| `b2587fe` | #44 head when this evidence was written | **971** | 0 |
| `42dd5da` | #44 head at the merge request | 971 | 0 |
| `e575c7d` | merge of #34 into `main` | 994 | 0 |
| `aeca706` | merge of #44 (i.e. both) | **1011** | 0 |
| `4ad9523` | #45 head | **1019** | 0 |

`Ran 971` was therefore exact for `b2587fe`; the merge of #34's frame-allocator work added 23
tests and #44's own added 17 more on top of the shared base, which is where 1011 comes from;
#45 adds 8 (7 in `tests/test_make_check_contract.py`, 1 in `tests/test_release.py`).  Every
number in the table reconciles arithmetically with its predecessor.

Reproduce with:

```sh
git archive <sha> | tar -x -C <dir>
python3 -c "
import sys, unittest; sys.path.insert(0, '<dir>/tests')
l = unittest.TestLoader()
s = l.discover('<dir>/tests', pattern='test_*.py', top_level_dir='<dir>/tests')
def c(x): return sum(c(y) if isinstance(y, unittest.TestSuite) else 1 for y in x)
print(c(s), len(l.errors))"
```

Two things this exercise is actually worth recording, both of which would have bitten anyone
comparing counts later:

* **The count belongs to a tree, not to a milestone.** A suite total quoted without its commit
  is unanchored, which is how a 40-test difference came to look like a defect.  Both this
  directory and #45 quote the SHA their numbers came from, and #45's evidence README does the
  same for its gate lines.
* **Discovery is sensitive to the working directory in a way that fails silently.** Running the
  collector from inside `tests/` (instead of `-s tests -t .` from the repository root) makes
  four modules unimportable: they collapse into 4 `_FailedTest` placeholders and 52 real tests
  disappear, and the total still looks like a suite size.  `errors: 0` is printed above for
  every row precisely because a count without it is not a count.  Note also that a checkout of
  this repository can arrive **shallow** — this one was, at depth 1 — which makes the ancestral
  comparisons above impossible until `git fetch --unshallow`; that command is what allowed this
  table to be built at all.
