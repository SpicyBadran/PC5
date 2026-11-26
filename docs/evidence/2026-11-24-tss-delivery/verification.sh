#!/bin/sh
# Re-derive this directory's claims from the bytes on file.  No network, no build, no QEMU.
#
#   bash docs/evidence/2026-11-24-tss-delivery/verification.sh   # expect: OK (N checks)
#
# README.md makes four kinds of claim, and each is checked here in the way that can actually fail:
#
#   1. that a #DF the kernel *invited* and an NMI the hypervisor *raised* were both delivered on
#      the IST stack their gates name -- checked by recomputing the stack-pointer windows from the
#      recorded serial text, not by matching the words `status=ok`;
#   2. that the gate's own parsed numbers agree with the raw log it parsed -- two sources, because a
#      claim backed by one file is only as good as that file's formatting;
#   3. that the 64-bit gate passed with the delivery rows in it and the 32-bit path was not dragged
#      along; and
#   4. that the two *absences* in this directory are measured rather than assumed -- the stub cannot
#      reach the probe, and the file that proves it is checked here too.
#
# Every check is a `PASS` or `FAIL` line printed by one Python pass, so the arithmetic lives in one
# place and the shell only decides an exit status.
set -eu
cd "$(dirname "$0")"

for f in README.md x64-report.json x64-matrix.json ovmf64-serial-cold.log ovmf64-serial-warm.log \
         stubtest64-report.json unicorn-soft-int.txt env.txt x64-gate.log ia32-gate.log \
         ia32-report.json ia32-matrix.json ovmf32-serial-cold.log ovmf32-serial-warm.log \
         capture-unicorn-refusal.sh; do
    if [ ! -s "$f" ]; then
        echo "FAIL this page cites $f, which is missing or empty"
        exit 1
    fi
done

out=$(python3 - "$(cd ../../.. && pwd)" <<'PY'
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else None

lines = []


def ok(label):
    lines.append("PASS %s" % label)


def bad(label, detail=""):
    lines.append("FAIL %s%s" % (label, (": " + detail) if detail else ""))


def load(name):
    with open(name, encoding="utf-8") as handle:
        return json.load(handle)


def text(name):
    with open(name, encoding="utf-8", errors="replace") as handle:
        return handle.read().replace("\r", "")


def field(line, key):
    match = re.search(r"\b%s=(\S+)" % re.escape(key), line)
    return match.group(1) if match else None


def number(line, key):
    value = field(line, key)
    if value is None:
        return None
    return int(value, 16) if value.startswith("0x") else int(value)


x64 = load("x64-report.json")
rows = load("x64-matrix.json")
stub = load("stubtest64-report.json")
refusal = text("unicorn-soft-int.txt")
env = text("env.txt")

# -------------------------------------------------------------------------- 1. per-boot evidence
#: The six rows §30 and §31 added, named the way the matrix names them.  Spelled out here rather
#: than read from the report, because a reader of this directory needs to be told which rows are
#: the ones being claimed -- and a script that derived its own expectations from the artifact
#: under test could never notice a row going missing.
NEW_ROWS = (
    "LTR executed, and STR returned the selector the kernel built",
    "the descriptor read through GDTR names the TSS frame, with a 103-byte limit",
    "the descriptor type went available -> busy across the instruction",
    "the double-fault gate names IST1 and the NMI gate names IST2",
    "the I/O-map base sits past the descriptor limit, as the TSS was written",
    "the double-fault probe found its frame on the interrupt stack, not the faulting one",
    "QEMU's inject-nmi was taken by the guest on the stack its TSS names for slot 2",
)

for boot in ("cold", "warm"):
    name = "ovmf64-serial-%s.log" % boot
    log = text(name)
    markers = x64["data"][boot]["markers"]
    stages = x64["data"][boot]["stages"]

    def line_for(prefix, log=log):
        found = [l.strip() for l in log.splitlines() if l.strip().startswith(prefix)]
        return found[-1] if found else ""

    tss = line_for("PC5-64-TSS status=")
    probe = line_for("PC5-64-TSS-DF")
    nmi = [l.strip() for l in log.splitlines() if l.strip().startswith("PC5-64-EXCEPTION vector=2")]
    nmi = nmi[-1] if nmi else ""

    if not tss:
        bad("%s: the TSS line" % boot, "no PC5-64-TSS line in the log")
        continue
    ok("%s: the boot reports its TSS as installed, not as assumed" % boot)

    # The install, and the four readbacks that make `tr=loaded` mean something.
    for key, want in (("status", "ok"), ("arm-rules", "1"), ("tr", "loaded"),
                      ("str", "0x20"), ("busy", "available-to-busy"), ("iomap", "104"),
                      ("ist-df", "1"), ("ist-nmi", "2")):
        got = field(tss, key)
        if got == want:
            ok("%s: %s=%s" % (boot, key, want))
        else:
            bad("%s: %s=%s" % (boot, key, want), "the log says %r" % (got,))

    tss_base = number(tss, "tss")
    gdt = number(tss, "gdt")
    gdtr = number(tss, "gdtr")
    ist1 = number(tss, "ist1")
    ist2 = number(tss, "ist2")
    gdt = number(tss, "gdt")
    gdt_limit = number(tss, "limit")
    # The selector is a *slot*, so it has to name a descriptor inside the table GDTR reports --
    # which is the arithmetic whose inverse produced the bug §30's erratum records (0x28 named the
    # descriptor's second qword, and nothing in the log could tell that apart from a choice).
    # Two ways the limit is printed (the builder's own `sgdt`, and the value read back), and the
    # selector's slot has to fit inside both -- a 48-byte table whose third slot is missing is the
    # shape the kernel would have installed if the GDT entry had never been added.
    if 0x20 + 16 - 1 < gdt_limit + 1 and gdtr == gdt:
        ok("%s: the loaded selector names a whole descriptor inside GDTR's 48-byte bound" % boot)
    else:
        bad("%s: selector inside GDTR" % boot,
            "limit=%d gdtr=%x gdt=%x, selector 0x20 needs 48" % (gdt_limit, gdtr, gdt))
    # `desc=0x1000/103` is one token holding two facts, so both halves are checked: the base has
    # to be the frame the pool granted, and the limit has to be the size of the structure -- a
    # descriptor sized to 47 (the old `sizeof`, before the I/O-map bitmap was added) would pass the
    # first half and make the CPU clip the bitmap the kernel just promised to honour.
    desc = (field(tss, "desc") or "/").split("/")
    if len(desc) == 2 and desc[0] == "0x%x" % tss_base and int(desc[1]) == 103:
        ok("%s: the descriptor read through GDTR names frame 0x%x with a 103-byte limit" %
           (boot, tss_base))
    else:
        bad("%s: descriptor base and limit" % boot, field(tss, "desc"))
    if (ist1, ist2) != (tss_base + 0x2000, tss_base + 0x3000):
        bad("%s: the two IST stacks are separate frames above the TSS" % boot,
            "ist1=%x ist2=%x tss=%x" % (ist1, ist2, tss_base))
    else:
        ok("%s: IST1 and IST2 are the two frames above the TSS, three distinct pages" % boot)

    # The invited double fault.  Every number is re-derived here; `status=ok` alone would be a
    # quote of the kernel's own conclusion, which is the thing this directory is not allowed to
    # rest on.
    if not probe:
        bad("%s: the delivery probe line" % boot, "no PC5-64-TSS-DF line")
        continue
    prsp = number(probe, "rsp")
    pbefore = number(probe, "rsp-before")
    presume = number(probe, "resume")
    pframe = [int(word, 16) for word in
              re.search(r"frame=([0-9a-fx,]+)", probe).group(1).split(",")]
    checks = (
        ("the probe reports a verdict rather than a refusal",
         field(probe, "status") == "ok" and field(probe, "reason") == "ok",
         probe[:60]),
        ("the handler woke up on a different stack than the one that faulted",
         prsp is not None and prsp != pbefore, "rsp=%x before=%x" % (prsp, pbefore)),
        ("that stack is inside the IST1 page the loaded TSS names",
         ist1 - 0x1000 <= prsp < ist1, "rsp=%x ist1=%x" % (prsp, ist1)),
        ("the frame the CPU left holds the address the trap was taken at",
         presume in pframe and len(pframe) == 5, "resume=%x frame=%s" % (presume, pframe)),
        ("the gate named the same slot the TSS does",
         number(probe, "gate-ist") == 1, field(probe, "gate-ist")),
        ("DF is clear again afterwards",
         number(probe, "df") == 0, field(probe, "df")),
        ("the stack the fault happened on was not written through",
         field(probe, "stack-intact") == "1", field(probe, "stack-intact")),
        ("the borrowed gate was handed back to the reporter",
         field(probe, "gate-restored") == "1", field(probe, "gate-restored")),
    )
    for label, condition, detail in checks:
        if condition:
            ok("%s: #DF probe — %s" % (boot, label))
        else:
            bad("%s: #DF probe — %s" % (boot, label), detail)

    # The NMI nobody invited, and the two-stack cross-check at the end: the point of *two* slots is
    # that two events land on two different stacks, which no single-window check can establish.
    if not nmi:
        bad("%s: the injected NMI" % boot, "no PC5-64-EXCEPTION vector=2 line in the log")
        continue
    nrsp = number(nmi, "rsp")
    nmi_checks = (
        ("the guest reported the vector QEMU was told to raise",
         number(nmi, "vector") == 2, field(nmi, "vector")),
        ("the reporter names it, rather than leaving `unnamed` on the line a gate quotes",
         field(nmi, "name") == "non-maskable-interrupt", field(nmi, "name")),
        ("the live gate for vector 2 names slot 2",
         number(nmi, "ist") == 2, field(nmi, "ist")),
        ("the handler woke up inside the IST2 page",
         ist2 - 0x1000 <= nrsp < ist2, "rsp=%x ist2=%x" % (nrsp, ist2)),
        ("and not on the stack the double-fault probe used",
         not (ist1 - 0x1000 <= nrsp < ist1), "rsp=%x ist1=%x" % (nrsp, ist1)),
        ("the NMI arrived after the boot had settled",
         "PC5-64-EXCEPTION vector=2" in log.split("PC5-64-DONE", 1)[-1], "ordering"),
    )
    for label, condition, detail in nmi_checks:
        if condition:
            ok("%s: NMI — %s" % (boot, label))
        else:
            bad("%s: NMI — %s" % (boot, label), detail)

    # The gate's own parsed numbers, against the log those numbers were parsed from.
    for key, source in (("probe_rsp", prsp), ("probe_ist1", ist1), ("ist2_top", ist2),
                        ("nmi_rsp", nrsp), ("nmi_ist", 2), ("tr_selector", 0x20),
                        ("tr_busy", "available-to-busy")):
        if markers.get(key) != source:
            bad("%s: the gate read %s from the same log" % (boot, key),
                "gate=%r log=%r" % (markers.get(key), source))
        else:
            ok("%s: gate's %s agrees with the re-derived value" % (boot, key))

    # The seven rows, in the place they are actually published: `data.<boot>.stages` is keyed by the
    # *stage requirement* and each entry carries the check name with the `[cold] `/`[warm]` prefix,
    # so matching on the check name is the form that cannot silently match nothing.
    for row in NEW_ROWS:
        matched = [value for name_, value in stages.items()
                   if name_ == row or value.get("check", "").endswith(row)]
        if len(matched) != 1:
            bad("%s: delivery row %r is present exactly once" % (boot, row[:40]),
                "%d matches" % len(matched))
        elif matched[0].get("state") == "pass":
            ok("%s: delivery row %r passed" % (boot, row[:40]))
        else:
            bad("%s: delivery row %r passed" % (boot, row[:40]), repr(matched[0])[:120])

    # What the harness asked QEMU for, in the harness's own words.
    nmi_ctx = x64["data"][boot].get("nmi") or {}
    if nmi_ctx.get("requested") and nmi_ctx.get("accepted"):
        ok("%s: QEMU accepted the injection (so the NMI row is about the guest)" % boot)
    else:
        bad("%s: QEMU accepted the injection" % boot, repr(nmi_ctx)[:120])
    if nmi_ctx.get("exception_lines") == 2:
        ok("%s: the reporter's two lines arrived after the marker" % boot)
    else:
        bad("%s: the reporter's two lines arrived after the marker" % boot,
            "exception_lines=%r" % nmi_ctx.get("exception_lines"))

# ---------------------------------------------------------------------- 2. cross-boot agreement
cold_log = text("ovmf64-serial-cold.log")
warm_log = text("ovmf64-serial-warm.log")


def shape(log):
    tss = [l for l in log.splitlines() if l.strip().startswith("PC5-64-TSS status=")][0]
    probe = [l for l in log.splitlines() if l.strip().startswith("PC5-64-TSS-DF")][0]
    return tuple(field(tss, key) for key in ("tr", "str", "busy", "iomap", "ist-df", "ist-nmi")) \
        + (field(probe, "status"), field(probe, "reason"), field(probe, "gate-ist"),
           field(probe, "df"))


if shape(cold_log) == shape(warm_log):
    ok("the cold and warm boots agree on the delivery shape, field by field")
else:
    bad("the cold and warm boots agree on the delivery shape",
        "%r vs %r" % (shape(cold_log), shape(warm_log)))

# -------------------------------------------------------------------------- 3. the gate's verdict
# The three ways the gate summarises itself, checked against each other rather than quoted:
# a verdict flag, an exit status, and the per-row tally.  They agree today; a report that said
# `passed` while carrying a `fail` row would not survive this.
counts = {state: sum(1 for c in x64["checks"] if c["state"] == state)
          for state in ("pass", "fail", "skip")}
if counts["fail"] == 0:
    ok("the 64-bit gate reports no failed check (PASS=%d)" % counts["pass"])
else:
    bad("the 64-bit gate reports no failed check", "fail=%d" % counts["fail"])
if x64["exit_code"] == 0 and x64["passed"] and counts["fail"] == 0:
    ok("its exit status, its verdict flag and its tally agree")
else:
    bad("exit status, verdict flag and tally agree",
        "exit=%r passed=%r fail=%d" % (x64["exit_code"], x64["passed"], counts["fail"]))
states = {row["state"] for row in rows}
if states <= {"verified", "external"}:
    ok("every matrix row is verified or external (%d rows)" % len(rows))
else:
    bad("every matrix row is verified or external", repr(sorted(states)))
# The matrix is keyed by *requirement*, so a boot that repeats another boot's requirements does not
# get a copy of every row: the delivery rows appear once, under `[cold]`, and the warm boot is the
# single row saying "the same stages, again".  Asserted here because a reader who expected seven more
# rows under `[warm]` would reasonably conclude the warm NMI was never measured.
warm_rows = [row for row in rows if row.get("check", "").startswith("[warm]")]
if len(warm_rows) == 1 and "same stages" in warm_rows[0]["check"]:
    ok("the warm boot is one matrix row, and it names the repetition it proves")
else:
    bad("the warm boot's matrix row", repr([r.get("check") for r in warm_rows])[:160])
if sum(1 for row in rows if row.get("source") == "OVMF cold boot") == 40:
    ok("the cold boot accounts for all 40 per-stage matrix rows")
else:
    bad("the cold boot's 40 per-stage rows",
        str(sum(1 for row in rows if row.get("source") == "OVMF cold boot")))
delivery_cold = [row for row in rows
                 if any(row.get("check", "").endswith(name) for name in NEW_ROWS)]
if len(delivery_cold) == 7 and all(row["state"] == "verified" for row in delivery_cold):
    ok("seven of those 40 are the delivery rows, and all seven are `verified`")
else:
    bad("seven delivery rows in the matrix", "%d, states %r" % (
        len(delivery_cold), sorted({r["state"] for r in delivery_cold})))
# `checks` is the per-boot list, and it is where the "both boots" claim actually lives: 41 cold rows
# and 41 warm rows plus 21 control rows, and the 14 delivery rows are seven in each boot.
names = [c["name"] for c in x64["checks"]]
for boot in ("cold", "warm"):
    n = sum(1 for row in NEW_ROWS if "[%s] %s" % (boot, row) in names)
    if n == 7:
        ok("the report's %s checks carry all seven delivery rows" % boot)
    else:
        bad("the report's %s checks carry all seven delivery rows" % boot, "%d of 7" % n)
# The console logs are what a reader can actually open first, so the tally in them has to be the
# tally in the JSON -- one number quoted from two files that were written by different parts of the
# gate (the printer and the reporter) is the cheapest agreement worth demanding.
for log_name, want in (("x64-gate.log", counts["pass"]),
                       ("ia32-gate.log", sum(1 for c in json.load(open("ia32-report.json"))["checks"]
                                             if c["state"] == "pass"))):
    printed = sum(1 for line in text(log_name).splitlines() if line.startswith("[PASS]"))
    if printed == want:
        ok("%s prints exactly the %d rows the report says passed" % (log_name, want))
    else:
        bad("%s agrees with the report's tally" % log_name, "printed=%d report=%d" % (printed, want))
if len(x64["checks"]) == 103:
    ok("103 check rows in total: 21 control + 41 per boot")
else:
    bad("103 check rows in total", str(len(x64["checks"])))
# `not_applicable` is a *count*, and the rows carrying it are the `skip` ones -- a check that is
# neither passed nor failed.  Naming it here is the point: if the untested path ever stops being the
# disk-style boot, the sentence above needs rewriting, not a footnote.
skipped = [c["name"] for c in x64["checks"] if c["state"] == "skip"]
if x64["not_applicable"] == len(skipped) == 1 and "disk-style" in skipped[0]:
    ok("the one NOT TESTED path is still the disk-style ESP boot, and its count agrees with its rows")
else:
    bad("the one NOT TESTED path is still the disk-style ESP boot",
        "count=%r rows=%r" % (x64["not_applicable"], skipped))
if not any(c["state"] == "fail" for c in json.load(open("ia32-report.json"))["checks"]):
    ok("the 32-bit gate, run on the same tree, is unchanged: 63 PASS, 0 FAIL")
else:
    bad("the 32-bit gate is unchanged", "a fail row appeared in ia32-report.json")
ia32_serial = text("ovmf32-serial-cold.log")
if "PC5-64" not in ia32_serial:
    ok("and its serial carries no 64-bit marker, so the two profiles were not crossed")
else:
    bad("the 32-bit boot carries no 64-bit marker", "PC5-64 found in ovmf32-serial-cold.log")

# --------------------------------------------------------------------- 4. the two absences, measured
# The stub gate publishes a tally, not a list of row objects, so its *verdict* has to be
# cross-checked against something other than its own summary -- the emulated serial is the second
# source, and the `tr=loaded` line below is what makes 50/50 mean something rather than arithmetic.
stub_checks = stub["checks"]
if stub["passed"] and stub_checks == {"passed": 50, "total": 50} and not stub["failures"]:
    ok("the stub gate passes 50/50 with no recorded failure")
else:
    bad("the stub gate passes 50/50 with no recorded failure",
        "passed=%r %r failures=%d" % (stub["passed"], stub_checks, len(stub["failures"])))
if stub["level"] == "stub-firmware-emulation":
    ok("and it labels itself emulation, not firmware -- the level the README repeats")
else:
    bad("the stub report names its own level", stub["level"])
stub_serial = stub["serial"].replace("\r", "")
if "tr=loaded" in stub_serial:
    ok("and it *does* load TR: the refusal that used to be recorded there is gone")
else:
    bad("and it does load TR", "no tr=loaded in the stub serial")
if "PC5-64-TSS-DF status=" not in stub_serial:
    ok("while the probe verdict is absent, which is the shape the emulator limitation predicts")
else:
    bad("the probe verdict is absent from the stub", "a status= line appeared")
if "PC5-64-EXCEPTION" not in stub_serial:
    ok("and no exception at all was reported across the stub boot")
else:
    bad("no exception reported across the stub boot", "found PC5-64-EXCEPTION")

# The site line lists *every* occurrence, so a comma in it means the count moved and the refusal
# file is no longer evidence about one instruction.
site = re.search(r"`int \$8` sites in the image: (0x[0-9a-f]+ \(file offset \d+\))", refusal)
if site and "," not in refusal[site.start():refusal.index("\n", site.start())]:
    ok("the shipped image contains exactly one `int $8`, at %s" % site.group(1))
elif site:
    bad("the image contains exactly one `int $8`", "the file lists more than one site")
else:
    bad("the image's single `int $8` site is recorded", refusal[:120])
if "site == fault_rip or fault_rip - 2: True" in refusal:
    ok("and the emulator's fault lands on that instruction, so the refusal is the interrupt path")
else:
    bad("the emulator's fault lands on the `int`", "no agreement line in the artifact")
if "exit: 0" in refusal:
    ok("the test that pins that refusal passes in this environment")
else:
    bad("the soft-interrupt test passes", refusal[-200:])
if "UC_ERR_EXCEPTION" in refusal:
    ok("with the fault named as an undeliverable exception rather than anything else")
else:
    bad("the fault is named as UC_ERR_EXCEPTION", refusal[-200:])

# --------------------------------------------------------------------------- 5. provenance
report_commit = x64["data"]["environment"]["buildinfo"]["commit"]
if report_commit in env:
    ok("the run recorded here came from commit %s, as env.txt says" % report_commit[:7])
else:
    bad("env.txt names the commit the gate booted", report_commit)
esp = x64["data"]["release_esp"]
if esp["identical"] and esp["iso_sha256"] == esp["built_sha256"]:
    ok("the ESP the gate booted is byte-identical to the one the build produced")
else:
    bad("the ESP identity", repr(esp)[:160])
# env.txt is hand-written prose about machine state, so the one number in it that the report also
# carries is matched here: if the digest in the text ever stops being the digest the gate measured,
# the page is describing a different run than the one it files.
sha = re.search(r"ESP image\s+\S+\n\s+sha256 ([0-9a-f]{64})", env)
if sha and sha.group(1) == esp["built_sha256"] == x64["data"]["environment"]["efi_image_sha256"]:
    ok("env.txt's ESP digest is the digest the gate derived independently")
else:
    bad("env.txt's ESP digest", repr(sha and sha.group(1))[:80])
iso = re.search(r"ISO\s+\S+\n\s+sha256 ([0-9a-f]{64})", env)
if iso and iso.group(1) == x64["data"]["environment"]["iso_sha256"]:
    ok("and so is the ISO it says was booted")
else:
    bad("env.txt's ISO digest", repr(iso and iso.group(1))[:80])
# A provenance check, not a behavioural one: the layout the logs above were produced by is the
# layout the tree still has.  It earns its place because the bug this milestone found lived in
# exactly these two numbers, in three files at once.
source = ROOT / "kernel64" / "tss64.c" if ROOT else None
if source.exists():
    body = source.read_text(encoding="utf-8")
    if "return 36ull + (index - 1ull) * 8ull;" in body and "return 104ull - 2ull;" in body:
        ok("kernel64/tss64.c still puts IST1 at 36 and the I/O-map base at 102")
    else:
        bad("kernel64/tss64.c still puts IST1 at 36 and the I/O-map base at 102")
    if "#define PC5_TSS64_SELECTOR 0x20u" in (ROOT / "kernel64" /
                                             "tss64.h").read_text(encoding="utf-8"):
        ok("and the selector the erratum corrected is still pinned at 0x20")
    else:
        bad("the selector is still pinned at 0x20")
else:
    bad("the source tree was reachable for the provenance check")

print("\n".join(lines))
sys.exit(0)
PY
)

printf '%s\n' "$out"
fails=$(printf '%s\n' "$out" | grep -c '^FAIL' || true)
total=$(printf '%s\n' "$out" | grep -c '^\(PASS\|FAIL\)' || true)
if [ "$fails" -ne 0 ]; then
    echo "FAILED: $fails of $total checks did not hold"
    exit 1
fi
echo "OK ($total checks re-derived from $(ls -1 | wc -l | tr -d ' ') files in this directory)"
