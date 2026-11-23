#!/usr/bin/env python3
"""Derive `summary.json` for the milestone 7 boot-gate evidence from a run's logs.

Kept as a script rather than a one-off so the record can be re-derived and checked
against the logs, the way `verification.sh` does for the serial output.  It reads only
the files beside it: `build.log`, `gate.log`, `metadata.log`, the report and matrix the
gate wrote, and the serial logs.
"""
import collections
import hashlib
import json
import os
import re
import sys

D = os.path.dirname(os.path.abspath(sys.argv[0]))


#: The field set `$(BUILD)/iso/BUILDINFO.TXT` carries, and nothing else: a build log is
#: full of other `word: value` lines, and a summary that quietly absorbs them turns a
#: comparison of two records into a comparison of two accidents.
BUILDINFO_FIELDS = ("version", "build", "commit", "date", "kernel", "uefi-path",
                    "esp-loader")


def fields(text):
    out = {}
    for line in text.splitlines():
        m = re.match(r"^(%s): (.*)$" % "|".join(BUILDINFO_FIELDS), line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def main():
    out = {"commit": os.popen("git -C %s rev-parse HEAD" % os.path.dirname(os.path.dirname(D))).read().strip(),
           "gates": {}}
    for sub, rep_name, mat_name in (("x64", "x64-report.json", "x64-matrix.json"),
                                    ("ia32", "ia32-report.json", "ia32-matrix.json")):
        d = os.path.join(D, sub)
        rep = json.load(open(os.path.join(d, rep_name)))
        mat = json.load(open(os.path.join(d, mat_name)))
        env = rep["data"]["environment"]
        build = open(os.path.join(d, "build.log")).read()
        gate = open(os.path.join(d, "gate.log")).read()
        meta = open(os.path.join(d, "metadata.log")).read()
        out["gates"][sub] = {
            "make_build": "make %siso verify" % ("PC5_UEFI=64 " if sub == "x64" else ""),
            "make_gate": "make %sovmf-test" % ("PC5_UEFI=64 " if sub == "x64" else ""),
            "build_exit": int(re.search(r"BUILD_EXIT=(\d+)", build).group(1)),
            "gate_exit": int(re.search(r"GATE_EXIT=(\d+)", gate).group(1)),
            "metadata_exit": int(re.search(r"METADATA_EXIT=(\d+)", meta).group(1)),
            "metadata_verdict": re.search(r"verdict=(\w+)", meta).group(1),
            "iso_verify": "VALID + MANIFEST VALID" if "MANIFEST VALID" in build else "see build.log",
            "iso": {"path": "build/pc5.iso",
                    "sha256": env["iso_sha256"],
                    "bytes": re.search(r"wrote build/pc5.iso \((\d+) bytes", build).group(1)},
            "checks_pass": sum(1 for c in rep["checks"] if c["state"] == "pass"),
            "checks_fail": sum(1 for c in rep["checks"] if c["state"] == "fail"),
            "checks_not_tested": sum(1 for c in rep["checks"] if c["state"] == "skip"),
            "not_tested_names": [c["name"] for c in rep["checks"] if c["state"] == "skip"],
            "matrix_rows": len(mat),
            "matrix": dict(collections.Counter(r["state"] for r in mat)),
            "release_esp_identical": rep["data"]["release_esp"]["identical"],
            "release_esp_sha256": rep["data"]["release_esp"]["built_sha256"],
            "iso_buildinfo_quoted_by_report": env.get("buildinfo"),
            "iso_buildinfo_measured_from_file": fields(build),
            "profile_read_from_medium": env["profile"]["read_from"],
            "cold_stopped_on": rep["data"]["cold"]["stopped_on"],
            "warm_stopped_on": rep["data"]["warm"]["stopped_on"],
            "qemu_version": env["qemu_version"],
            "serial_sha256": {f: hashlib.sha256(open(os.path.join(d, f), "rb").read()).hexdigest()
                              for f in sorted(os.listdir(d)) if f.startswith("serial-")},
        }
    out["note"] = ("Measured in the workspace that ran the gates. `build/pc5.iso` is one "
                   "name shared by both modes, so a digest here means nothing without the "
                   "commit label beside it -- which is why both copies of the build record "
                   "are recorded, the one the boot read off the medium and the one read off "
                   "the file.")
    with open(os.path.join(D, "summary.json"), "w") as fh:
        json.dump(out, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print("summary.json written")


if __name__ == "__main__":
    main()
