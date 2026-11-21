"""Measure which tests actually discriminate each mutant of the v1 harness."""
import importlib.util, json, pathlib, sys
spec = importlib.util.spec_from_file_location(
    "v1h", "/home/user/pc5/evidence/usb-descriptor-remediation-v1/mutation_harness.py")
v1 = importlib.util.module_from_spec(spec)
sys.modules["v1h"] = v1
spec.loader.exec_module(v1)          # module-level only; main() is guarded
import re, shutil, tempfile
from pathlib import Path
REPO, TARGET, PATTERN = v1.REPO, v1.TARGET, v1.PATTERN
out = {}
with tempfile.TemporaryDirectory(prefix="pc5-usb-measure-") as tmp:
    root = Path(tmp)
    base = v1.prepare(root, "baseline")
    proc = v1.run_suite(base)
    outcomes, _ = v1.classify(proc.stdout + proc.stderr)
    inventory = sorted(k for k in outcomes)
    total = re.search(r"^Ran (\d+) tests", proc.stdout + proc.stderr, re.M)
    print("baseline:", total.group(1) if total else "?", "exit", proc.returncode,
          "FAILs", len([k for k, v in outcomes.items() if v == "FAIL"]))
    for name, description, old, new, expected in v1.MUTATIONS:
        work = v1.prepare(root, name)
        source = work / TARGET
        text = source.read_text()
        if text.count(old) != 1:
            out[name] = {"error": f"pattern x{text.count(old)}"}
            continue
        source.write_text(text.replace(old, new, 1))
        proc = v1.run_suite(work)
        outcomes, reasons = v1.classify(proc.stdout + proc.stderr)
        fails = sorted(k for k, v in outcomes.items() if v == "FAIL")
        errs = sorted(k for k, v in outcomes.items() if v == "ERROR")
        out[name] = {"failing": fails, "erroring": errs, "harness_expected": expected}
        print(f"{name}\n   fails({len(fails)}): {fails}\n   errs: {errs}\n   v1 listed: {expected}")
    print("\nINVENTORY SIZE:", len(inventory))
Path("/tmp/measure_map.json").write_text(json.dumps(out, indent=1))
