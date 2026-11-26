# Recovery

## 1. Nothing is written at boot, so a boot breaks nothing

PC5 boots and runs entirely from the medium: no boot path partitions, formats or
writes to a disk. The kernel *has* a write path (`M1d` ATA sectors, `M1g` file
creation, `M1i` overwrite and directories) but it runs only when an explicit
command asks for it, against an explicitly named device, and it has only ever
been exercised on emulated disks. "Recovery" therefore still means getting back
to a working image, not restoring data — until the installer exists (`M8`).

## 2. Recovering a machine that will not boot PC5

1. Power off, remove the medium.
2. Boot the machine normally. If the firmware boot order was changed, reset it
   (or use the firmware's one-time boot menu).
3. Re-flash the medium: `python3 tools/pc5ctl.py usb --iso build/pc5.iso
   --device /dev/sdX --yes` — the tool re-verifies the checksum first.
4. The image is hybrid: it boots legacy/CSM (El Torito or the MBR) and UEFI (the
   ESP inside the ISO). If one path fails, try the other; the UEFI path has been
   verified under OVMF only, never on a physical board.

`docs/INSTALLATION.md` has the full flashing and troubleshooting matrix,
including the meaning of each loader failure code.

## 3. Recovery console (in the boot environment)

The serial port is a first-class console: COM1, 115200 8N1, 8 data bits, no
parity, 1 stop bit. It mirrors the boot log (every line beginning `PC5-LOG`) and
accepts commands at the `pc5>` prompt:

| Command | Effect |
|---|---|
| `status` | version, display, keyboard, CPU, RAM, PCI count |
| `chain` | all 13 compatibility stages with state and the blocked-stage evidence |
| `hw` | CPU, memory, PCI classes, GPU/NIC descriptors, SMBIOS system identity |
| `fw` | firmware probe result and an explicit `genuine_ps5_execution=running=no` line |
| `storage` | drives found on the legacy ATA channels, mounted volume, read-back result |
| `diag` | the self-test summary as one line |
| `log` | the in-memory boot log |
| `screen` | dump of the rendered character grid, so a headless machine can be inspected |
| `page N` | switch the on-screen page |
| `reboot` | 8042 reset line |
| `halt` | stop the CPU (safe to power off) |

This exists because a machine with a broken display, a wrong framebuffer mode or
a firmware quirk is exactly when you need the log.

## 4. Recovery of the build environment

```sh
make clean && make && make verify && make check
```

If `make boot-test` cannot find the emulator: `npm install v86-system` (or set
`V86_ASSETS`). If a boot test fails, `build/test/serial.log` and
`build/test/screens.txt` contain what the machine actually did.

## 5. Planned recovery environment

A dedicated recovery entry (separate boot option, minimal kernel, disk tools,
update rollback) belongs to `M8` — *Release-quality PC5: installer, updates,
rollback* — which is `not_started` and depends on `M1`, `M4` and `M7`. The
storage primitives it needs already exist (`M1d`–`M1i`); what is missing is an
installer that writes a system, and physical hardware to prove it. Recovery
without disk access would be a menu of buttons that do nothing, so it is not
scheduled before then.

`M1b` is **not** the recovery milestone. It is *Automated CI and release
publication*, depends only on `M0`, and was `blocked` for an unrelated reason: the
GitHub token available to this environment lacked the `workflows` permission, so
`ci/build.yml` could not be activated. That half is resolved — the pipeline is
installed at `.github/workflows/build.yml` — and `M1b` stays open on its own acceptance criteria, which need
a published tag whose hash equals a local rebuild. Earlier revisions of this page
conflated the milestone with recovery; the roadmap data is authoritative.
