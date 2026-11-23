# PC5 build system
#
#   make            build build/pc5.iso (the release artifact)
#   make check      unit tests for the host tools
#   make boot-test  boot the ISO in the emulator harness and assert the log
#   make verify     validate the ISO structure and every checksum
#   make roadmap    validate the milestone graph and its generated document
#   make clean
#
# Reproducibility: set SOURCE_DATE_EPOCH to pin the build timestamp; the
# toolchain needs only binutils, gcc (with 32-bit support), python3 and make.

VERSION      ?= 0.2.0
COMMIT       ?= $(shell git rev-parse HEAD 2>/dev/null || echo unknown)
# Does the tree hold edits that `commit` cannot name?  Tracked files only: a scratch
# directory must not make a release look hand-built.  This is what `BUILD_ID` carries,
# because a record that says "built from dbd7500" while the loader in the image came
# from an uncommitted diff misdates the artifact -- and the ISO is a *file*, so it
# survives a `git commit` and would otherwise keep naming the previous tree.
PC5_DIRTY    := $(shell test -n "$$(git status --porcelain --untracked-files=no 2>/dev/null)" && echo dirty)
BUILD_ID     ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo local)$(if $(PC5_DIRTY),-dirty)
TAG          ?=
# Use $(SOURCE_DATE_EPOCH) rather than $$SOURCE_DATE_EPOCH so that a value given
# on the command line (make SOURCE_DATE_EPOCH=...) pins the timestamp too; a
# make command-line variable is not exported to the shell.
STAMP_EPOCH  ?= $(or $(SOURCE_DATE_EPOCH),$(shell date -u +%s))
BUILD_DATE   ?= $(shell date -u -d @$(STAMP_EPOCH) +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ)

# `?=` so a test or a CI job can build into its own directory and leave the
# developer's `build/` alone -- the release-ESP tests below build both UEFI paths,
# and they must not do it in a tree the reader is working in.
BUILD        ?= build
OBJ          := $(BUILD)/obj
ISO          := $(BUILD)/pc5.iso

CC           := gcc
LD           := ld
AS           := as

KERNEL_SRC   := $(filter-out kernel/manifest_default.c,$(wildcard kernel/*.c))
KERNEL_OBJ   := $(patsubst kernel/%.c,$(OBJ)/%.o,$(KERNEL_SRC))
KERNEL_OBJ   += $(OBJ)/start.o

FW_MANIFEST_SRC := kernel/manifest_default.c
FW_MANIFEST_OBJ := $(OBJ)/fwmanifest.o
ifneq ($(wildcard firmware/manifest.json),)
FW_MANIFEST_SRC := $(BUILD)/generated/manifest.c
endif

KERNEL_CFLAGS := -m32 -std=gnu11 -O2 -Wall -Wextra -Wno-unused-parameter \
                 -ffreestanding -fno-pic -fno-stack-protector -fno-builtin \
                 -fno-asynchronous-unwind-tables -fno-unwind-tables \
                 -mno-sse -mno-mmx -mno-80387 -mno-sse2 -msoft-float \
                 -fno-tree-loop-distribute-patterns \
                 -Ikernel/include \
                 -DPC5_VERSION='"$(VERSION)"' \
                 -DPC5_BUILD_ID='"$(BUILD_ID)"' \
                 -DPC5_BUILD_DATE='"$(BUILD_DATE)"'

.PHONY: ovmf-setup ovmf-check emulator-probe all uefi kernel64 uefi64 stubtest64 check docs-check citations-check citation-refs presentation-check presentation validation record-validation test boot-test stubtest ovmf-test ovmf-test-both verify roadmap clean distclean info unit metadata release-assets psm psm-render manifest hw-record brand brand-check

all: iso

iso: $(ISO)

# Build and verify the 64-bit release image in one go, so the artifact checked is
# the artifact built.  `make PC5_UEFI=64 iso verify` is the same thing; this exists
# because CI and the release notes should not have to remember two variables.
iso64:
	@$(MAKE) --no-print-directory PC5_UEFI=64 iso verify

info:
	@echo "version   $(VERSION)"
	@echo "build id  $(BUILD_ID)"
	@echo "date      $(BUILD_DATE)"
	@echo "sources   $(KERNEL_SRC)"

$(OBJ):
	@mkdir -p $(OBJ)

$(BUILD)/generated:
	@mkdir -p $(BUILD)/generated

$(BUILD)/generated/manifest.c: firmware/manifest.json tools/firmware.py | $(BUILD)/generated
	python3 tools/firmware.py manifest --input firmware/manifest.json --c-output $@

$(OBJ)/%.o: kernel/%.c | $(OBJ)
	$(CC) $(KERNEL_CFLAGS) -c $< -o $@

$(OBJ)/fwmanifest.o: $(FW_MANIFEST_SRC) | $(OBJ)
	$(CC) $(KERNEL_CFLAGS) -c $(FW_MANIFEST_SRC) -o $@

$(OBJ)/start.o: kernel/start.S | $(OBJ)
	$(CC) -m32 -c $< -o $@

$(OBJ)/loader.o: boot/loader.S | $(OBJ)
	$(AS) --32 $< -o $@

$(OBJ)/mbr.o: boot/mbr.S | $(OBJ)
	$(AS) --32 $< -o $@

$(BUILD)/kernel.bin: $(KERNEL_OBJ) $(FW_MANIFEST_OBJ) kernel/kernel.ld
	$(LD) -m elf_i386 -T kernel/kernel.ld --oformat binary -nostdlib -o $@ $(KERNEL_OBJ) $(FW_MANIFEST_OBJ)
	@size=$$(stat -c %s $@); echo "pc5: kernel image $$size bytes (limit 585728)"; \
	 if [ $$size -gt 585728 ]; then echo "pc5: kernel too large for the loader window"; exit 1; fi

$(BUILD)/loader.bin: $(OBJ)/loader.o
	$(LD) -m elf_i386 -Ttext 0x7c00 --oformat binary -e _start -o $@ $<
	@size=$$(stat -c %s $@); echo "pc5: loader image $$size bytes"; \
	 if [ $$size -ne 4096 ]; then echo "pc5: loader must be exactly 4096 bytes (descriptor at 0xE00)"; exit 1; fi

# The hybrid MBR is the first 512 bytes of the image.  Only its first 440 bytes
# are code: bytes 440..511 are the disk signature, the partition table and 55AA,
# which tools/mkiso.py writes from the real layout so the table can never
# disagree with the image it describes.
$(BUILD)/mbr.bin: $(OBJ)/mbr.o
	$(LD) -m elf_i386 -Ttext 0x7c00 --oformat binary -e _start -o $@ $<
	@size=$$(stat -c %s $@); echo "pc5: MBR bootstrap $$size bytes"; \
	 if [ $$size -ne 440 ]; then echo "pc5: MBR bootstrap must be exactly 440 bytes (partition table starts at 0x1B8)"; exit 1; fi

# ------------------------------------------------------- 64-bit path ----
# The experimental 64-bit foundation lives beside the 32-bit one and is *not*
# part of `all`, `iso` or the release artifact: `make uefi64` builds the
# experimental long-mode image, `make kernel64` the flat 64-bit kernel, and
# `make stubtest64` runs both under the long-mode stub firmware.  Nothing in
# the release path depends on any of it, which is what keeps the 32-bit image
# the thing that ships while the 64-bit path is still being proven.
KERNEL64_CFLAGS := -m64 -std=gnu11 -O2 -Wall -Wextra -Werror -ffreestanding \
                   -fno-pic -fno-pie -fno-builtin -fno-stack-protector \
                   -mno-red-zone -fno-asynchronous-unwind-tables \
                   -fno-unwind-tables -fno-tree-loop-distribute-patterns \
                   -Ikernel64/include
KERNEL64_SRC := kernel64/start.S kernel64/main.c kernel64/serial64.c \
                kernel64/cpu64.c kernel64/apic64.c kernel64/idt64.c \
                kernel64/timer64.c kernel64/madt64.c kernel64/mem64.c \
                kernel64/tss64.c kernel64/kimage64_head.c kernel64/idt64.S
KERNEL64_HDR := kernel64/include/pc5_boot64.h kernel64/include/pc5_map.h \
                kernel64/include/pc5_kimage64.h \
                kernel64/include/pc5_io.h kernel64/include/pc5_mem64.h \
                kernel64/serial64.h kernel64/cpu64.h kernel64/apic64.h kernel64/idt64.h kernel64/timer64.h kernel64/madt64.h \
                kernel64/tss64.h kernel64/kernel64.ld
LOADER64_SRC := boot/uefi64/uefi64_main.c boot/uefi64/pagetables.c \
                boot/uefi64/kimage64.c boot/uefi64/bootx64.S
LOADER64_HDR := boot/uefi64/pagetables.h boot/uefi64/handover64.h \
                kernel64/include/pc5_kimage64.h \
                boot/uefi64/../uefi/efi.h kernel64/include/pc5_boot64.h \
                boot/uefi/bootx64.ld

$(BUILD)/kernel64/start.o: kernel64/start.S | $(BUILD)/kernel64
	$(CC) -m64 -c $< -o $@

$(BUILD)/kernel64/%.S.o: kernel64/%.S $(KERNEL64_HDR) | $(BUILD)/kernel64
	$(CC) -m64 -c $< -o $@

$(BUILD)/kernel64/%.o: kernel64/%.c $(KERNEL64_HDR) | $(BUILD)/kernel64
	$(CC) $(KERNEL64_CFLAGS) -c $< -o $@

$(BUILD)/kernel64:
	@mkdir -p $@

# The image is flat and linked at 0x100000: it is loaded unchanged, so there is
# no relocation step and no PIE.
# $(filter %.o,$^): the linker script is a prerequisite so edits relink, but it is
# already named by -T, and passing it twice is a hard ld error ("appears multiple
# times") - which is what a from-scratch build of this target used to hit.
$(BUILD)/kernel64/pc5-64.elf: $(BUILD)/kernel64/start.o $(BUILD)/kernel64/main.o \
                              $(BUILD)/kernel64/serial64.o $(BUILD)/kernel64/cpu64.o $(BUILD)/kernel64/apic64.o \
                              $(BUILD)/kernel64/idt64.o $(BUILD)/kernel64/timer64.o $(BUILD)/kernel64/madt64.o \
                              $(BUILD)/kernel64/mem64.o \
                              $(BUILD)/kernel64/tss64.o \
                              $(BUILD)/kernel64/kimage64_head.o \
                              $(BUILD)/kernel64/kimage64.o \
                              $(BUILD)/kernel64/idt64.S.o kernel64/kernel64.ld
	$(LD) -m elf_x86_64 -T kernel64/kernel64.ld -nostdlib -o $@ $(filter %.o,$^)

# .bss carries the kernel stack, so it has to travel with the image: a binary
# that stops before .bss would put the stack outside the loaded window.
# The header is stamped into the *binary*, after objcopy, because the three values it
# records -- the file's length, where `_start64` landed, and how much `.bss` travelled with
# the image -- do not exist until the image is flat.  The tool reads them from the ELF's own
# symbols, writes them, then re-reads the file with the rules the loader applies and refuses
# to leave a stamped-but-invalid image on disk: a format bug has to fail the build, not
# produce a kernel that boots slightly wrong.
$(BUILD)/kernel64/KERNEL64.BIN: $(BUILD)/kernel64/pc5-64.elf tools/kimage64.py \
                                kernel64/include/pc5_kimage64.h
	objcopy -O binary --set-section-flags .bss=alloc,load,contents $(BUILD)/kernel64/pc5-64.elf $@
	python3 tools/kimage64.py --stamp --elf $(BUILD)/kernel64/pc5-64.elf --bin $@
	@size=$$(stat -c %s $@); echo "KERNEL64 OK: $$size bytes at 0x100000"

kernel64: $(BUILD)/kernel64/KERNEL64.BIN

$(BUILD)/kernel64/pagetables.o: boot/uefi64/pagetables.c boot/uefi64/pagetables.h | $(BUILD)/kernel64
	$(CC) $(KERNEL64_CFLAGS) -c $< -o $@

$(BUILD)/kernel64/uefi64_main.o: boot/uefi64/uefi64_main.c $(LOADER64_HDR) | $(BUILD)/kernel64
	$(CC) $(UEFI_CFLAGS) -Werror -Ikernel64/include -c $< -o $@

$(BUILD)/kernel64/bootx64-64.o: boot/uefi64/bootx64.S | $(BUILD)/kernel64
	$(CC) -m64 -c $< -o $@

# The image-header validator, compiled three times on purpose: once into BOOTX64.EFI, where
# it decides whether the kernel is jumped to; once into KERNEL64.BIN, where the kernel re-runs
# the header half of the same rules against its own bytes and against the size it was handed
# (see `validate_image_self`); and once on the host by `tests/test_kimage64.py`, which feeds it
# every malformed header the format can be handed and expects a named refusal.  One
# implementation, three rigs -- which is the only way the host test says anything about what
# either boot-time caller does.  That file also asserts both link lines name this object,
# because a kernel linking its own copy of the rules would pass the host battery and prove
# nothing about the loader it quietly disagrees with.
$(BUILD)/kernel64/kimage64.o: boot/uefi64/kimage64.c $(LOADER64_HDR) | $(BUILD)/kernel64
	$(CC) $(UEFI_CFLAGS) -Werror -Ikernel64/include -c $< -o $@

$(BUILD)/uefi64/BOOTX64.EFI: $(BUILD)/kernel64/uefi64_main.o \
                             $(BUILD)/kernel64/pagetables.o \
                             $(BUILD)/kernel64/kimage64.o \
                             $(BUILD)/kernel64/bootx64-64.o boot/uefi/bootx64.ld
	@mkdir -p $(BUILD)/uefi64
	$(LD) $(UEFI_LDFLAGS) -e _start $(BUILD)/kernel64/bootx64-64.o \
	  $(BUILD)/kernel64/uefi64_main.o $(BUILD)/kernel64/pagetables.o \
	  $(BUILD)/kernel64/kimage64.o -o $@
	@python3 tools/eficheck.py $@ --require-relocs --max-bytes 196608

# A separate ESP image: the release ESP never carries KERNEL64.BIN.
$(BUILD)/efi64.img: $(BUILD)/uefi64/BOOTX64.EFI $(BUILD)/kernel64/KERNEL64.BIN \
                    tools/mkefiimg.py
	python3 tools/mkefiimg.py --size-kb 1440 \
	  --file $(BUILD)/uefi64/BOOTX64.EFI=EFI/BOOT/BOOTX64.EFI \
	  --file $(BUILD)/kernel64/KERNEL64.BIN=KERNEL64.BIN \
	  --output $@ --info $(BUILD)/efi64-info.json
	@sha=$$(sha256sum $@ | cut -d" " -f1); echo "UEFI64 OK: $$(stat -c %s $@) bytes sha256=$$sha"

uefi64: $(BUILD)/efi64.img

# Stub firmware, not OVMF: the loader's and kernel's own code runs under a
# long-mode emulator.  `make ovmf-test` remains the only gate that can claim
# real firmware, and it is still not available here (see TEST_RESULTS.md).
stubtest64: $(BUILD)/efi64.img $(BUILD)/kernel64/KERNEL64.BIN
	python3 tests/boot64_stubtest.py --image $(BUILD)/uefi64/BOOTX64.EFI \
	  --kernel $(BUILD)/kernel64/KERNEL64.BIN \
	  --json-out $(BUILD)/stubtest64-report.json

# ---------------------------------------------------------------- UEFI ----
# The UEFI boot application is an ordinary PE32+ EFI image built with the
# same binutils/gcc the rest of the tree uses: no EDK2, no mingw toolchain.
# `ld -m i386pep` is binutils' 64-bit PE emulation, and --enable-reloc-section
# derives the base relocation table from the absolute addresses the compiler
# emits with -fno-pic.
UEFI_CFLAGS := -m64 -std=gnu11 -O2 -Wall -Wextra -Wno-unused-parameter \
               -ffreestanding -fno-pic -fno-stack-protector -fno-builtin \
               -mno-red-zone -fno-asynchronous-unwind-tables -fno-unwind-tables \
               -fno-tree-loop-distribute-patterns \
               -Ikernel/include -Iboot/uefi
UEFI_LDFLAGS := -m i386pep -nostdlib -T boot/uefi/bootx64.ld \
                --image-base 0x10000000 --subsystem 10 \
                --section-alignment 0x1000 --file-alignment 0x200 \
                --major-subsystem-version 6 --minor-subsystem-version 0 \
                --minor-os-version 0 --no-insert-timestamp --enable-reloc-section

UEFI_SRC     := boot/uefi/uefi_main.c
UEFI_ASM     := boot/uefi/bootx64.S
UEFI_HDR     := boot/uefi/efi.h boot/uefi/bootx64.ld kernel/include/pc5.h

$(OBJ)/uefi_main.o: $(UEFI_SRC) $(UEFI_HDR) | $(OBJ)
	$(CC) $(UEFI_CFLAGS) -c $< -o $@

$(OBJ)/bootx64.o: $(UEFI_ASM) | $(OBJ)
	$(CC) -m64 -c $< -o $@

UEFI_OBJ := $(OBJ)/bootx64.o $(OBJ)/uefi_main.o

$(BUILD)/uefi/BOOTX64.EFI: $(UEFI_OBJ) boot/uefi/bootx64.ld
	@mkdir -p $(BUILD)/uefi
	$(LD) $(UEFI_LDFLAGS) -e _start $(UEFI_OBJ) -o $@
	@python3 tools/eficheck.py $@ --require-relocs --max-bytes 196608

# The ESP carries the kernel next to BOOTX64.EFI.  Firmware has no ISO9660
# driver (OVMF ships none), so on an El Torito UEFI boot the ESP the firmware
# mounts is the *only* volume the loader can read: a kernel that lived only in
# the ISO9660 root would be unreachable there.  The BIOS path keeps reading the
# ISO9660 copy, and tests/boot_test.py proves the two copies are identical.
$(BUILD)/efi.img: $(BUILD)/uefi/BOOTX64.EFI $(BUILD)/kernel.bin docs/ISO_README.txt tools/mkefiimg.py
	python3 tools/mkefiimg.py --size-kb 1440 \
	  --file $(BUILD)/uefi/BOOTX64.EFI=EFI/BOOT/BOOTX64.EFI \
	  --file $(BUILD)/kernel.bin=KERNEL.BIN \
	  --file docs/ISO_README.txt=README.TXT \
	  --output $@ --info $(BUILD)/efi-info.json

uefi: $(BUILD)/efi.img

$(BUILD)/iso/README.TXT: docs/ISO_README.txt | $(BUILD)
	@mkdir -p $(BUILD)/iso
	cp $< $@

#: One stamp file for the inputs the in-image build record quotes, defined before the
#: rule that lists it.
#:
#: Two ordering rules live here, and both were got wrong while this was being written.
#:
#: A rule's prerequisites are expanded while the makefile is *read*, so a definition
#: appearing below the rule contributes nothing to it -- silently, with no error, and
#: in a way `make -n` cannot see.
#:
#: A stamp has to compare contents, not timestamps.  `BUILDINFO.TXT` quotes three things
#: the file name `pc5.iso` does not carry: the boot mode, the source commit, and the
#: digest of the loader embedded in the ESP.  In a build directory that already holds
#: both loaders, switching `PC5_UEFI` moves no mtime the record depends on, so a rule
#: that merely "depends on the loader" regenerates nothing and the image ships the other
#: mode's label.  `make ovmf-test` caught a build in exactly that state by reading the
#: ISO instead of trusting the file.
#:
#: So the recipe re-runs on every invocation -- `FORCE`, because a prerequisite-free
#: target that already exists is otherwise up to date forever, which is how a stamp can
#: be written once and then quietly stop guarding anything -- and rewrites the file only
#: when a quoted input actually differs (`cmp -s -`).  An unchanged build costs one
#: comparison and no rebuild.  Working-tree dirtiness is deliberately not stamped, which
#: would rebuild on every `touch`; it rides in the `build:` label instead, so an artifact
#: no commit produced is never mistaken for one.
#:
#: `tests/test_release_esp.py::ReleaseAttributionRebuild` runs the mechanism instead of
#: reading it: build, move HEAD, build again, require the image to have moved; then
#: build the other mode in the same directory and require the record to follow.
ISO_STAMP := $(BUILD)/pc5.iso.stamp
FORCE:
.PHONY: FORCE

$(BUILD):
	@mkdir -p $(BUILD)

# ------------------------------------------------- which UEFI path ships? ----
# The El Torito ESP has exactly one fallback boot file, `EFI/BOOT/BOOTX64.EFI`, so
# "put the 64-bit path in the release image" is a choice of *which loader sits in
# that slot*, not an addition.  `PC5_UEFI` makes the choice explicit instead of
# silently promoting one path over the other:
#
#   PC5_UEFI=32 (default)  the bring-up loader, loading KERNEL.BIN in protected
#                          mode -- the path `make boot-test` and the release notes
#                          describe, byte-for-byte what `make iso` produced before
#                          this option existed.
#   PC5_UEFI=64            the long-mode loader, loading KERNEL64.BIN -- the path
#                          `make stubtest64` executes and the one the 64-bit
#                          milestone is about.
#
# Both modes are verified the same way: `make verify` compares the ESP's loader and
# kernel bytes against the *individually built and gated* files, so the release
# image is provably the artifact the gates ran on.  What differs is which claim is
# earned: only OVMF can show a path genuinely boots, and `make ovmf-test` keeps
# saying NOT TESTED until it can run.
PC5_UEFI ?= 32
ifeq ($(PC5_UEFI),64)
ESP_IMAGE       := $(BUILD)/efi64-release.img
ESP_LOADER      := $(BUILD)/uefi64/BOOTX64.EFI
ESP_KERNEL      := $(BUILD)/kernel64/KERNEL64.BIN
ESP_KERNEL_NAME := KERNEL64.BIN
# Also published into the ISO9660 root, so the flat-file copy the BIOS path uses
# for KERNEL.BIN has a 64-bit counterpart and `make verify` can cross-check both.
ISO_ESP_EXTRA   := --file KERNEL64.BIN=$(BUILD)/kernel64/KERNEL64.BIN
else ifeq ($(PC5_UEFI),32)
ESP_IMAGE       := $(BUILD)/efi.img
ESP_LOADER      := $(BUILD)/uefi/BOOTX64.EFI
ESP_KERNEL      := $(BUILD)/kernel.bin
ESP_KERNEL_NAME := KERNEL.BIN
ISO_ESP_EXTRA   :=
else
$(error PC5_UEFI must be 32 or 64, got '$(PC5_UEFI)')
endif

# Where this rule sits is part of what it means, and so is what it depends on.
#
# A rule's prerequisite list is expanded when the makefile is *parsed*, so a variable
# assigned further down the file contributes its earlier value -- or nothing.  The
# 64-bit mode sets `ESP_LOADER` in the block immediately above, which is why the rule
# has to follow it: before that, `BUILDINFO.TXT` depended on the *32-bit* loader, and
# in a 64-bit scratch build relinking `BOOTX64.EFI` left the file untouched, so the
# ISO shipped a record quoting the previous loader's digest.  `$(shell sha256sum …)`
# inside the recipe reads the current value, which is what made it look right while the
# dependency was wrong; `tests/test_release_esp.py::ReleaseAttributionRebuild` relinks
# the loader and requires this file to move.
# Depends on the loader it names, because it quotes that loader's digest: switching
# PC5_UEFI has to change this file, or a 64-bit ISO would ship a 32-bit label.  It
# depends on the revision stamp for the same reason in the other direction: the file
# quotes the source commit, so *committing* has to change the file (and thus the ISO
# that embeds it) instead of leaving an image that names the commit before the one
# that produced it.  `verify` and `tools/iso_manifest.py` can only say something is
# stale if the thing it says is regenerated when it goes stale.
#
# `$(ISO_STAMP)` is in the list because `$(ESP_LOADER)` alone is not enough, and because
# the record quotes the commit as well: see the stamp's own comment above.  A rebuild that
# picked up the other mode's ESP while still labelling itself with the previous one is the
# error this guards; `verify` and `tools/iso_manifest.py` can only call something stale if
# the thing it says is regenerated when it goes stale.
$(BUILD)/iso/BUILDINFO.TXT: $(ESP_LOADER) $(ISO_STAMP) | $(BUILD)
	@mkdir -p $(BUILD)/iso
	@printf 'PC5\nversion: %s\nbuild: %s\ncommit: %s\ndate: %s\nkernel: %s\nuefi-path: %s\nesp-loader: %s\n' \
	 "$(VERSION)" "$(BUILD_ID)" "$(COMMIT)" "$(BUILD_DATE)" \
	 "$(if $(filter 64,$(PC5_UEFI)),64-bit PC5 long-mode kernel,32-bit PC5 bring-up kernel)" \
	 "$(PC5_UEFI)" "$(shell sha256sum $(ESP_LOADER) 2>/dev/null | cut -c1-16)" > $@

VERIFY_ESP_ARGS := --expect-esp-loader $(ESP_LOADER) \
                   --expect-esp-file $(ESP_KERNEL_NAME)=$(ESP_KERNEL)

# The release ESP for the 64-bit path.  Distinct from $(BUILD)/efi64.img, which is
# the bare `make uefi64` artefact used by the stub gate: the release image also
# carries README.TXT, so `make verify` sees the same ESP shape in both modes and a
# difference in boot behaviour means a difference in the boot path, not in padding.
$(BUILD)/efi64-release.img: $(BUILD)/uefi64/BOOTX64.EFI $(BUILD)/kernel64/KERNEL64.BIN \
                            docs/ISO_README.txt tools/mkefiimg.py
	python3 tools/mkefiimg.py --size-kb 1440 \
	  --file $(BUILD)/uefi64/BOOTX64.EFI=EFI/BOOT/BOOTX64.EFI \
	  --file $(BUILD)/kernel64/KERNEL64.BIN=KERNEL64.BIN \
	  --file docs/ISO_README.txt=README.TXT \
	  --output $@ --info $(BUILD)/efi64-release-info.json
	@sha=$$(sha256sum $@ | cut -d" " -f1); \
	 echo "RELEASE ESP (64-bit): $$(stat -c %s $@) bytes sha256=$$sha"

# The mode stamp, and why it is a prerequisite at all.
#
# `PC5_UEFI` changes what goes *into* `$(ISO)` but appears nowhere in its name, so
# `make PC5_UEFI=64 ovmf-test` right after a 32-bit build would find `build/pc5.iso`
# newer than every file it knows about and boot the 32-bit image while grading the
# 64-bit expectations.  That is not hypothetical: it is what happened on the first run
# of the 64-bit gate, which is why the release ESP the ISO embeds is now stamped with
# the mode -- and why the gate reads the profile off the medium instead of being told.
#
$(ISO_STAMP): FORCE | $(BUILD)
	@mkdir -p $(BUILD)
	@{ printf 'mode=%s\ncommit=%s\nloader=%s\n' "$(PC5_UEFI)" "$(COMMIT)" \
	     "$(shell sha256sum $(ESP_LOADER) 2>/dev/null | cut -c1-16)"; } | \
	  cmp -s - $@ || \
	  { printf 'mode=%s\ncommit=%s\nloader=%s\n' "$(PC5_UEFI)" "$(COMMIT)" \
	      "$(shell sha256sum $(ESP_LOADER) 2>/dev/null | cut -c1-16)" > $@; \
	    echo "pc5: the ISO's build record now names mode=$(PC5_UEFI) commit=$(COMMIT)"; }

# The recipe must therefore *read* the mode, not merely be ordered after the stamp: it
# writes `build-info.json` with `--uefi-path`, and a rebuild that picked up the 64-bit
# ESP while still labelling itself `uefi-path: 32` would leave the release note
# contradicting the medium -- the same class of error as booting the stale ISO, only
# harder to notice.  So the stamp is a normal prerequisite here.
$(ISO): $(BUILD)/loader.bin $(BUILD)/mbr.bin $(BUILD)/kernel.bin $(ESP_IMAGE) $(BUILD)/iso/README.TXT $(BUILD)/iso/BUILDINFO.TXT tools/mkiso.py $(ISO_STAMP)
	python3 tools/mkiso.py \
	  --volume-id PC5 \
	  --boot-image $(BUILD)/loader.bin \
	  --mbr-code $(BUILD)/mbr.bin \
	  --desc-offset 0xE00 \
	  --uefi-image $(ESP_IMAGE) \
	  --file KERNEL.BIN=$(BUILD)/kernel.bin \
	  $(ISO_ESP_EXTRA) \
	  --file README.TXT=$(BUILD)/iso/README.TXT \
	  --file BUILDINFO.TXT=$(BUILD)/iso/BUILDINFO.TXT \
	  --output $(ISO) \
	  --info $(BUILD)/build-info.json

# The unit binary is written to a path private to this invocation and to this
# process.  A single shared name under build/ made two concurrent `make check`
# runs collide: one links the binary while the other executes it, which Linux
# refuses with ETXTBSY ("Text file busy") and make reports as exit 127.  The
# binary is therefore created under a name nothing else holds and removed by a
# trap, so no other target or run can observe a half-written or in-use file.
#
# `mktemp` rather than a $$-based name: the shell's PID is not visible to make
# for expansion, and a name derived at parse time is shared by every invocation
# of that make process.  mktemp guarantees a name nothing else holds.
unit: | $(BUILD)
	@unit_bin=$$(mktemp $(BUILD)/kernel-unit.XXXXXXXX); \
	 trap 'rm -f "$$unit_bin"' EXIT; \
	 $(CC) -std=gnu11 -O1 -Wall -Wextra -Wno-unused-parameter -ffreestanding \
	   -fno-builtin -fno-stack-protector -DPC5_HOST_TEST -Ikernel/include \
	   -o "$$unit_bin" tests/kernel_unit.c kernel/lib.c kernel/mem.c kernel/chain.c \
	   kernel/block.c kernel/sha256.c kernel/part.c kernel/fat32.c && \
	 "$$unit_bin"

roadmap:
	python3 tools/roadmap.py check

# The platform contract's own gates: the registry's rules, the claim-to-interface
# linkage audit, the conformance suite against the PC5-authored doubles, and the
# check that the document's generated tables match their renderers.  None of this
# touches hardware or the PS5 package, and none of it is evidence about Sony's
# system software (docs/architecture/PS5_PLATFORM_CONTRACT.md §7).
psm:
	python3 -m psm.contract
	python3 -m psm.audit
	python3 -m psm.conformance
	python3 -m psm.render --check

psm-render:
	python3 -m psm.render --write

# `make check` is the one command that answers "is the tree sound?", so it has to run
# everything it claims to run -- in an order where a failure says what failed.
#
# Listing the documentation and contract gates as prerequisites looked equivalent and
# was not.  Make treats a failed prerequisite as an early exit: when `docs-check`
# tripped over a missing evidence file or a roadmap milestone, make stopped there, the
# suite never started, and the exit code described the gate rather than the code.  A
# green-looking `make check` could therefore mean "no test ran", and a red one could
# mean nothing about the tests at all.
#
# Inside a single recipe the shell stops at the first failure too, so chaining with
# `&&` would repeat the same mistake.  Each leg's status is therefore captured, the
# suite goes first because it is what people are asking about, and the first failing
# leg is named.  A failing gate still blocks `check`; it just no longer silences the
# suite.
#
# What this cannot fix: `make` exits 2 for any failed command, so neither the old form
# nor this one can hand a caller the suite's own status -- `make check` reports 2 and
# the log has to say what failed.  Measured, both forms, same stub project, in
# tests/test_make_check_contract.py.  Anything that needs the precise code (CI, a
# script) runs the leg directly, e.g. `python3 -m unittest discover -s tests
# -p 'test_*.py'`; `check` exists so that a human is never left without the suite.
check:
	@status=0; failed=""; \
	echo "==> suite"; \
	python3 -m unittest discover -s tests -p 'test_*.py' -v || { \
		status=$$?; failed=suite; }; \
	for target in unit psm docs-check citations-check presentation-check brand-check; do \
		[ -z "$$failed" ] || break; \
		echo "==> make $$target"; \
		$(MAKE) --no-print-directory $$target || { status=$$?; failed=$$target; }; \
	done; \
	if [ -n "$$failed" ]; then \
		echo "make check FAILED in '$$failed' (exit $$status); every later leg was skipped"; \
		exit $$status; \
	fi; \
	echo "make check: suite and all gates passed"

# The README's figures and status table are generated from the milestone graph.
# `presentation` writes; `presentation-check` reads and is what `make check` runs, so a
# normal test run never rewrites tracked documentation.  A hand edit that overstates
# progress fails `presentation-check` rather than shipping.
presentation:
	python3 tools/presentation.py sync

presentation-check:
	python3 tools/presentation.py check

# A citation is only evidence while Git can hand the object back, and a rebase
# rewrites commit ids silently.  `docs-check` validates links, anchors and make
# targets and cannot see this, so a rewrite used to be able to rot the whole
# evidence trail while every gate stayed green.  `citations-check` resolves each
# backticked object reference in the dated evidence documents.
citations-check:
	python3 tools/commit_citations.py

# Provision what `citations-check` needs to be able to see.  Some evidence citations
# name a pull-request head, and GitHub publishes those objects only under
# `refs/pull/N/head`, which no default clone fetches -- so in a fresh clone the gate
# is not wrong but it is blind.  This is a setup step, deliberately not a
# prerequisite of `check`: `make check` must not need the network.  A clone with no
# remote exits 2, "cannot be provisioned", which is neither a pass nor a verdict.
citation-refs:
	@tools/citation_refs.sh

# The logo and its variants are generated from tools/brand.py, so a hand edit to
# brand/*.svg is a stale edit.  `brand` writes; `brand-check` reads.
brand:
	python3 tools/brand.py sync

brand-check:
	python3 tools/brand.py check

# What the gates actually reported, as committed records: command, exit code, commit,
# date. `list` only reads; `record-validation` runs the gates and rewrites the records
# (which is why it is never part of `make check`).
validation:
	python3 tools/validation.py list
	python3 tools/validation.py verify-records

# presentation-check runs last: writing a record for `check` changes the README
# block it validates, so the block is regenerated before the check is recorded.
# `check` includes `presentation-check`, so the generated block is brought up to
# date before the gates run and again before the check that reads it is recorded;
# otherwise `check` fails on a block it is about to rewrite.
record-validation:
	@$(MAKE) -s presentation
	python3 tools/validation.py run check verify roadmap docs-check \
	  boot-test stubtest kernel64 uefi64 stubtest64 ovmf-test
	@$(MAKE) -s presentation
	python3 tools/validation.py run presentation-check

# Every Markdown file: relative links, heading anchors and `make <target>` mentions.
# The README is also covered by the architecture tests; this covers the rest of the
# tree, where a renamed heading or a removed target would otherwise go unnoticed.
docs-check:
	python3 tools/docs_check.py

# The build manifest is the second, independent reader of the image: what files
# it contains, their sizes and hashes, and which boot entries firmware would
# execute.  A release ships it so somebody else can confirm the image without
# trusting our writer (BUILD-VERIFIED, level 1).
# Every filed physical-boot record has to be complete; the template must not be.
hw-record:
	@set -e; for f in docs/evidence/*.json; do \
	  case "$$f" in *TEMPLATE*) continue;; esac; \
	  python3 tools/hw_record.py "$$f"; \
	done
	python3 tools/hw_record.py --check-template docs/evidence/M1_RECORD_TEMPLATE.json

manifest: $(ISO)
	python3 tools/iso_manifest.py $(ISO) --output $(BUILD)/pc5.iso.manifest.json

verify: $(ISO)
	python3 tools/verify_iso.py $(ISO) --info $(BUILD)/build-info.json \
	  $(VERIFY_ESP_ARGS) \
	  --json-out $(BUILD)/verify-report.json
	python3 tools/iso_manifest.py $(ISO) --output $(BUILD)/pc5.iso.manifest.json
	python3 tools/iso_manifest.py $(ISO) --verify $(BUILD)/pc5.iso.manifest.json

boot-test: $(ISO)
	python3 tests/boot_test.py --iso $(ISO)

# Execute the real UEFI loader image against the stub firmware in
# tests/uefi_stub_firmware.py.  This is not OVMF and never claims to be: it runs
# the loader's own PE32+ code and the handover stub so the loader's logic is
# executed rather than inspected.  Needs the `unicorn` Python package; exits 2
# with an explanation when it is absent, so a missing dependency can never be
# mistaken for a passing boot.
stubtest: $(ISO)
	python3 tests/uefi_stubtest.py --json-out $(BUILD)/stubtest-report.json

# Real OVMF/UEFI integration gate.  Deliberately NOT part of `make test` or
# `make verify`: it needs genuine TianoCore firmware and a real emulator, which
# most machines do not have, and a gate that silently degrades to "skipped"
# must not be able to look like part of a green run.  Exit code 3 means NOT
# TESTED (no firmware/emulator); it is never a pass.
#
#   PC5_QEMU=/usr/bin/qemu-system-x86_64
#   PC5_OVMF_CODE=/usr/share/OVMF/OVMF_CODE_4M.fd
#   PC5_OVMF_VARS=/usr/share/OVMF/OVMF_VARS_4M.fd
# The gate takes the same `PC5_UEFI` switch as the release target, from the same
# variables, so `make PC5_UEFI=64 ovmf-test` boots and grades the 64-bit release
# artifact -- `$(ESP_IMAGE)` is the ESP that was embedded, not a lookalike.  Passing
# the 32-bit `efi.img`/`kernel.bin` here while claiming to test the 64-bit path would
# compare the ISO against the wrong build artifacts, and `tools/build_release.py`
# hardcodes exactly that pair, which is why the release-side 64-bit gate is driven
# through this target rather than through that script.
ovmf-test: $(ISO) $(ESP_IMAGE) $(ESP_KERNEL)
	python3 tests/ovmf_boot.py --iso $(ISO) --efi-image $(ESP_IMAGE) \
	  --kernel $(ESP_KERNEL) --json-out $(BUILD)/ovmf-report.json \
	  --matrix-out $(BUILD)/ovmf-matrix.json \
	  --serial-dir $(BUILD)/ovmf-serial $(OVMF_ARGS)

# Both paths in one command, for a release run: the 32-bit artifact first because it
# is the one shipping, then the 64-bit one.  Each is built and booted from its own
# ESP, so the second boot cannot be influenced by the first ISO.
ovmf-test-both:
	@$(MAKE) --no-print-directory PC5_UEFI=32 ovmf-test
	@$(MAKE) --no-print-directory PC5_UEFI=64 ovmf-test

# Provision the external dependencies of the gate above (QEMU and a real
# TianoCore OVMF build) with the distribution's package manager, then print the
# firmware paths and hashes the report has to record.  Nothing is downloaded
# into the tree: firmware stays where the distribution put it.
ovmf-setup:
	sh tools/ovmf_setup.sh

# What the installed emulator can and cannot prove about exceptions and the task
# register.  It is a measurement, not a gate, so it always exits 0: run it when a
# documentation sentence about `ltr`, IST or `tr=not-loaded` needs re-checking, or when
# a newer Unicorn might have grown the ability.  See docs/evidence/ below for the run
# this line of reasoning rests on.
emulator-probe:
	@python3 tools/emulator_capabilities.py $(EMULATOR_PROBE_ARGS)

ovmf-check:
	sh tools/ovmf_setup.sh --check

test: check boot-test stubtest

# Provenance for a release: version, commit, toolchain and whatever the test
# logs in $(BUILD) can prove.  Reports that do not exist are simply not cited.
metadata: $(ISO)
	@mkdir -p $(BUILD)
	python3 tools/buildmeta.py --iso $(ISO) --info $(BUILD)/build-info.json \
	  --version "$(VERSION)" --tag "$(TAG)" --commit "$(COMMIT)" \
	  --build-id "$(BUILD_ID)" --source-date-epoch "$(STAMP_EPOCH)" \
	  --build-date "$(BUILD_DATE)" --uefi-path "$(PC5_UEFI)" \
	  $$([ -f $(BUILD)/ovmf-report.json ] && echo --ovmf-evidence $(BUILD)/ovmf-report.json) \
	  $$([ -f $(BUILD)/verify-report.json ] && echo --verify-report $(BUILD)/verify-report.json) \
	  $$([ -f $(BUILD)/test/boot-evidence.json ] && echo --boot-evidence $(BUILD)/test/boot-evidence.json) \
	  $$([ -f $(BUILD)/logs/unit.log ] && echo --unit-log $(BUILD)/logs/unit.log) \
	  $$([ -f $(BUILD)/logs/host.log ] && echo --host-log $(BUILD)/logs/host.log)

# Stage exactly the three release assets in one directory.
release-assets: metadata
	@mkdir -p $(BUILD)/release
	cp $(ISO) $(BUILD)/release/pc5.iso
	cp $(BUILD)/pc5.iso.sha256 $(BUILD)/release/pc5.iso.sha256
	cp $(BUILD)/build-info.json $(BUILD)/release/build-info.json
	cp $(BUILD)/pc5.iso.manifest.json $(BUILD)/release/pc5.iso.manifest.json
	@ls -l $(BUILD)/release
	@echo "pc5: release assets staged in $(BUILD)/release"
	@sha256sum $(BUILD)/release/*

clean:
	rm -rf $(BUILD)

distclean: clean
	rm -rf firmware/manifest.json
