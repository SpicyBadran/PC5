# Hardware compatibility strategy

Decision record: ADR-011. The strategy is a **qualification matrix**, not a
support promise. "PC5 supports mainstream PCs" is not a claim this project is
allowed to make; "this machine, this firmware version, this build, this result,
this log" is.

## 1. Why a matrix instead of a list

Three reasons, in order of importance:

1. **Firmware is part of the hardware.** The same laptop model with a different
   BIOS revision can fail to hand over a framebuffer. A model-level claim would
   be wrong for somebody.
2. **Drivers are per device, not per brand.** "AMD graphics supported" hides that
   an RX 6600 and a Ryzen 7000 iGPU are different code paths with different
   power-management behaviour.
3. **The project cannot test what it does not own.** A claim must be traceable to
   a machine someone actually ran, otherwise it is marketing.

## 2. Reference configurations

Three machines, chosen to cover the three GPU vendors and both CPU vendors, and
ordered so the machine closest to the PS5's architecture comes first.

| ID | Role | Target specification | Why this machine | Priority |
|---|---|---|---|---|
| **REF-AMD** | Primary bring-up | AMD Ryzen 5 5600 (Zen 3, 6C/12T), B550, Radeon RX 6600 (RDNA 2, 8 GB), 16 GB DDR4, 1 TB NVMe, UEFI, Ethernet | Zen + RDNA 2 is the architecture family the PS5 uses; AMD's Linux stack (`amdgpu`, RADV) is open and inspectable; no hybrid-graphics complications | **P0** |
| **REF-INTEL** | Integrated graphics | Intel Core i5-12400 (Alder Lake, 6C/12T), B660, UHD Graphics 730, 16 GB DDR4, 1 TB NVMe, UEFI, Ethernet | most common office/consumer configuration; tests ANV and a GOP-only display path with no discrete GPU | **P0** |
| **REF-NVIDIA** | Discrete NVIDIA | AMD Ryzen 5 5600 or Intel i5-12400, B550/B660, GeForce RTX 3060 (12 GB), 16 GB, 1 TB NVMe, UEFI, Ethernet | NVIDIA's driver model is materially different; qualification must not assume Mesa behaviour | **P1** |
| **REF-DELL** | Existing owner machine (opportunistic) | Dell Inspiron 15 7577, i7-7700HQ, 16 GB, GTX 1060 Max-Q + HD 630, SSD + HDD | it already exists, so it can produce the first physical log at no cost; Optimus makes it a *harder* case, not a representative one | **P1 (interim)** |

Requirements common to every reference machine:

* wired Ethernet (so network qualification never depends on a Wi-Fi blob),
* a USB port that is not behind a hub, for the DualSense,
* UEFI firmware with CSM available and Secure Boot toggleable,
* a serial port is **not** required, but if the machine has one, its log is
  preferred evidence. Otherwise a screen photograph plus the on-screen audit log
  page is the fallback record.

## 3. Device classes and priorities

Priority meanings: **P0** blocks a release; **P1** blocks the "recommended"
qualification tier but not a release; **P2** is best-effort with no promise.

| Class | Target | Priority | Verification | Principal risk |
|---|---|---|---|---|
| CPU — AMD Ryzen | Zen 2/3/4, x86-64-v2 baseline | P0 | `PC5-LOG cpu=`, count logical CPUs, run a sustained load test | SMT topology reporting, CPPC/power behaviour |
| CPU — Intel Core | 8th gen and newer | P0 | same | hybrid P/E cores on 12th gen and later |
| GPU — AMD Radeon | GCN 4 / RDNA 1–3 via `amdgpu` + RADV | P0 | renderer string, rendered frame, 30-minute play session | firmware blobs for newer IP, power management |
| GPU — Intel Graphics | Gen 9+ via `i915`/`xe` + ANV | P1 | same | which kernel driver owns the device on a given generation |
| GPU — NVIDIA | proprietary driver | P1 | same, with the driver version recorded | DKMS/kernel-version coupling, Wayland behaviour, reproducibility |
| NVMe SSD | any namespace, PCIe 3.0+ | P0 | install, 1 GiB write/read/verify, SMART health read | consumer drives with quirky power states |
| SATA SSD/HDD | AHCI | P0 | same on REF-INTEL | port multipliers (unsupported, documented) |
| USB controllers | xHCI | P0 | enumerate keyboard, stick and DualSense on each controller | firmware bugs in early xHCI implementations |
| DualSense | USB first, Bluetooth later | P1 | full input report dump + light bar/haptics/trigger test | Bluetooth stack and firmware differences |
| Audio | HDA codec over ALSA/PipeWire, HDMI audio | P1 | 60-second playback, no xrun storm, volume/mute | codec pin mappings, HDMI ELD |
| Ethernet | any `igc`/`r8169`/`e1000e` class NIC | P0 | DHCP lease, DNS, 100 MB transfer | none expected |
| Wi-Fi / Bluetooth | per-chipset, firmware-dependent | P1 | association + throughput, per chip | non-redistributable firmware; must be a per-device row |
| Display output | GOP framebuffer, then native modes | P0 | all six diagnostic pages render; no channel swap; text fallback works | VBE/GOP format differences, hybrid graphics output routing |

**Deliberately not promised:** Thunderbolt docks, port multipliers, SLI/CrossFire,
external GPUs, overclocked or undervolted configurations, machines whose firmware
enforces Secure Boot without an enrolled PC5 key, and devices whose Linux support
requires out-of-tree patches the project cannot maintain.

## 4. Qualification levels

Extends the seven levels already defined in `docs/HARDWARE_COMPATIBILITY.md`.
Each level has a pass condition that a log can prove.

| Level | Name | Pass condition |
|---|---|---|
| L1 | Boots | firmware reaches the PC5 loader and the boot environment draws its first page; full `PC5-LOG` captured |
| L2 | Installs | installer writes the system to the internal disk and the installed system boots without the medium |
| L3 | Operates | display, input, storage, audio and network each pass their check in §5; no kernel panic in a 30-minute session |
| L4 | PS5 firmware executes | not reachable; no path exists (Track B) |
| L5 | PS5 interface operates | not reachable |
| L6 | PS5 games execute | per-title, Track B |
| L7 | PS5 games playable | per-title, Track B |

A machine is described as **REF-qualified** only at L3 with every P0 device check
passing. Anything less is reported with the exact level and the failing check.

## 5. Test protocol

Run once per candidate machine per release build, in this order. Stop at the
first failure and record it — a partial matrix row is more useful than a
fabricated complete one.

1. **Record the machine**: firmware vendor/version/date, Secure Boot state, CSM
   state, CPU, GPU(s), NIC, storage controllers, RAM, BIOS settings changed.
2. **L1 boot**: boot from USB, capture the full log (serial if present, otherwise
   the audit log page and a photograph), confirm the loader's ISO probe and
   kernel verification lines.
3. **Inventory check**: `hw` page against the machine's specification; any
   disagreement is a finding, not a rounding error.
4. **Display**: all six pages, framebuffer read-back self-test, then the same at
   the highest mode the firmware offers.
5. **Storage**: install to the internal NVMe/SATA, boot installed, write 1 GiB,
   read it back, verify a SHA-256, read SMART health.
6. **Input**: keyboard, then DualSense over USB — every button, both sticks,
   touchpad, motion, light bar, haptics, adaptive triggers, each recorded.
7. **Audio**: 60-second playback on the analog output and over HDMI; count
   dropouts.
8. **Network**: DHCP lease, DNS resolution, 100 MB transfer, then unplug/replug
   and confirm recovery.
9. **Suspend/resume**: one cycle, then verify input and audio still work.
10. **Stability**: 30 minutes of a repeatable workload; record temperature,
    clock behaviour and any warning in the log.
11. **Repeat with the release build** and archive the evidence (§6).

Provisional pass thresholds — these are engineering targets to validate on the
reference machines, not inherited console specifications:

| Check | Provisional pass |
|---|---|
| display | every page renders with correct colours; no read-back mismatch |
| storage | 1 GiB write+read with matching SHA-256; no I/O error in the log |
| audio | ≤ 5 dropouts in 60 s |
| network | DHCP within 10 s; ≥ 90 MB/s on Gigabit Ethernet |
| controller | every input reported; end-to-end latency measured and recorded (target ≤ 20 ms on USB) |
| stability | 30 min without panic, hang or thermal shutdown |

Thresholds are recorded with the result that set them; changing one requires the
same review as changing a test.

## 6. Evidence format

Every result is a file, not a sentence. Directory:
`docs/evidence/<milestone>/<machine-id>/`.

```
docs/evidence/m1/ref-amd/
  machine.json        identity, firmware, components, BIOS settings
  boot.log            full PC5-LOG and serial transcript
  screens/            one image per UI page
  checks.json         one entry per check with pass/fail and measurement
  notes.md            anything the machine did that the log cannot show
```

`machine.json` + `checks.json` are the machine-readable row; the matrix in the
documentation is generated from them, so a claim cannot exist without a file.
Minimum fields:

```json
{
  "schema": "pc5.hardware-record/1",
  "machine_id": "ref-amd",
  "build": {"version": "0.3.0", "commit": "<sha>", "image_sha256": "<hash>"},
  "firmware": {"vendor": "", "version": "", "date": "", "secure_boot": false, "csm": false},
  "components": {"cpu": "", "gpu": [], "nic": "", "storage": [], "audio": "", "usb": []},
  "checks": [
    {"id": "l1.boot", "level": "L1", "pass": true, "measured": "PC5-BOOT-OK"},
    {"id": "l2.install", "level": "L2", "pass": false, "measured": "not attempted"}
  ],
  "level": "L1",
  "notes": ""
}
```

Rules: no record without a build hash; no pass without a measurement string; a
record is immutable once committed (corrections are new records); and a machine
never inherits another machine's row because the model name matches.

## 7. Milestone gates

| Gate | Requirement |
|---|---|
| M1 complete | REF-AMD and REF-INTEL reach L2; REF-DELL reaches L1 with a recorded log |
| M2 complete | REF-AMD and REF-INTEL reach L3; REF-NVIDIA reaches L1 and its GPU check is recorded as pass or fail |
| M3 complete | all three references install and boot the PC5 system image |
| M7 complete | every qualified machine has benchmark records from the same release build |
| M8 release | the published qualification list is exactly the set of machines with release-build L3 evidence |

## 8. What "support" will mean in user-facing text

| Phrase | Allowed only when |
|---|---|
| "Qualified" | L3 evidence exists for that machine and release build |
| "Boots" | L1 evidence exists, with the failing check named |
| "Known issue" | a record exists, with the machine and build |
| "Not supported" | the device class or machine is outside §3, or repeatedly fails a P0 check |

No other phrasing is permitted in release notes. This rule exists because
"should work" is the most expensive sentence in open-source hardware projects.

## 9. Open hardware questions (tracked in the risk register)

1. Can a GOP-only machine be brought to a usable console without VBE, and does
   the current loader's text fallback hide a defect rather than cover a case?
2. Does hybrid graphics (Optimus) require explicit GPU selection at install time,
   or can it be a runtime setting?
3. Which Wi-Fi chipsets used in the reference machines need non-redistributable
   firmware, and what is the documented install step for the user's own copy?
4. Does the NVIDIA proprietary driver make reproducible builds impossible, or
   only reproducible *runtime* claims? (Driver version pinning decides this.)
5. What is the measurement method for input-to-photon latency that a machine can
   reproduce without a camera? This must be decided before M4 claims latency.
