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
BUILD_ID     ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo local)
COMMIT       ?= $(shell git rev-parse HEAD 2>/dev/null || echo unknown)
TAG          ?=
# Use $(SOURCE_DATE_EPOCH) rather than $$SOURCE_DATE_EPOCH so that a value given
# on the command line (make SOURCE_DATE_EPOCH=...) pins the timestamp too; a
# make command-line variable is not exported to the shell.
STAMP_EPOCH  ?= $(or $(SOURCE_DATE_EPOCH),$(shell date -u +%s))
BUILD_DATE   ?= $(shell date -u -d @$(STAMP_EPOCH) +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ)

BUILD        := build
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

.PHONY: ovmf-setup ovmf-check all iso uefi kernel64 uefi64 stubtest64 check docs-check presentation-check presentation validation record-validation test boot-test stubtest ovmf-test verify roadmap clean distclean info unit metadata release-assets psm psm-render manifest hw-record

all: iso

iso: $(ISO)

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
                kernel64/cpu64.c
KERNEL64_HDR := kernel64/include/pc5_boot64.h kernel64/include/pc5_io.h \
                kernel64/serial64.h kernel64/cpu64.h kernel64/kernel64.ld
LOADER64_SRC := boot/uefi64/uefi64_main.c boot/uefi64/pagetables.c \
                boot/uefi64/bootx64.S
LOADER64_HDR := boot/uefi64/pagetables.h boot/uefi64/handover64.h \
                boot/uefi64/../uefi/efi.h kernel64/include/pc5_boot64.h \
                boot/uefi/bootx64.ld

$(BUILD)/kernel64/start.o: kernel64/start.S | $(BUILD)/kernel64
	$(CC) -m64 -c $< -o $@

$(BUILD)/kernel64/%.o: kernel64/%.c $(KERNEL64_HDR) | $(BUILD)/kernel64
	$(CC) $(KERNEL64_CFLAGS) -c $< -o $@

$(BUILD)/kernel64:
	@mkdir -p $@

# The image is flat and linked at 0x100000: it is loaded unchanged, so there is
# no relocation step and no PIE.
$(BUILD)/kernel64/pc5-64.elf: $(BUILD)/kernel64/start.o $(BUILD)/kernel64/main.o \
                              $(BUILD)/kernel64/serial64.o $(BUILD)/kernel64/cpu64.o \
                              kernel64/kernel64.ld
	$(LD) -m elf_x86_64 -T kernel64/kernel64.ld -nostdlib -o $@ $^

# .bss carries the kernel stack, so it has to travel with the image: a binary
# that stops before .bss would put the stack outside the loaded window.
$(BUILD)/kernel64/KERNEL64.BIN: $(BUILD)/kernel64/pc5-64.elf
	objcopy -O binary --set-section-flags .bss=alloc,load,contents $< $@
	@size=$$(stat -c %s $@); echo "KERNEL64 OK: $$size bytes at 0x100000"

kernel64: $(BUILD)/kernel64/KERNEL64.BIN

$(BUILD)/kernel64/pagetables.o: boot/uefi64/pagetables.c boot/uefi64/pagetables.h | $(BUILD)/kernel64
	$(CC) $(KERNEL64_CFLAGS) -c $< -o $@

$(BUILD)/kernel64/uefi64_main.o: boot/uefi64/uefi64_main.c $(LOADER64_HDR) | $(BUILD)/kernel64
	$(CC) $(UEFI_CFLAGS) -Werror -Ikernel64/include -c $< -o $@

$(BUILD)/kernel64/bootx64-64.o: boot/uefi64/bootx64.S | $(BUILD)/kernel64
	$(CC) -m64 -c $< -o $@

$(BUILD)/uefi64/BOOTX64.EFI: $(BUILD)/kernel64/uefi64_main.o \
                             $(BUILD)/kernel64/pagetables.o \
                             $(BUILD)/kernel64/bootx64-64.o boot/uefi/bootx64.ld
	@mkdir -p $(BUILD)/uefi64
	$(LD) $(UEFI_LDFLAGS) -e _start $(BUILD)/kernel64/bootx64-64.o \
	  $(BUILD)/kernel64/uefi64_main.o $(BUILD)/kernel64/pagetables.o -o $@
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

$(BUILD)/iso/BUILDINFO.TXT: | $(BUILD)
	@mkdir -p $(BUILD)/iso
	@printf 'PC5\nversion: %s\nbuild: %s\ndate: %s\nkernel: %s\n' \
	 "$(VERSION)" "$(BUILD_ID)" "$(BUILD_DATE)" "32-bit PC5 bring-up kernel" > $@

$(BUILD):
	@mkdir -p $(BUILD)

$(ISO): $(BUILD)/loader.bin $(BUILD)/mbr.bin $(BUILD)/kernel.bin $(BUILD)/efi.img $(BUILD)/iso/README.TXT $(BUILD)/iso/BUILDINFO.TXT tools/mkiso.py
	python3 tools/mkiso.py \
	  --volume-id PC5 \
	  --boot-image $(BUILD)/loader.bin \
	  --mbr-code $(BUILD)/mbr.bin \
	  --desc-offset 0xE00 \
	  --uefi-image $(BUILD)/efi.img \
	  --file KERNEL.BIN=$(BUILD)/kernel.bin \
	  --file README.TXT=$(BUILD)/iso/README.TXT \
	  --file BUILDINFO.TXT=$(BUILD)/iso/BUILDINFO.TXT \
	  --output $(ISO) \
	  --info $(BUILD)/build-info.json

unit: | $(BUILD)
	$(CC) -std=gnu11 -O1 -Wall -Wextra -Wno-unused-parameter -ffreestanding \
	  -fno-builtin -fno-stack-protector -DPC5_HOST_TEST -Ikernel/include \
	  -o $(BUILD)/kernel-unit tests/kernel_unit.c kernel/lib.c kernel/mem.c kernel/chain.c \
	  kernel/block.c kernel/sha256.c kernel/part.c kernel/fat32.c
	$(BUILD)/kernel-unit

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

check: unit psm docs-check presentation-check
	python3 -m unittest discover -s tests -p 'test_*.py' -v

# The README's figures and status table are generated from the milestone graph.
# `presentation` writes; `presentation-check` reads and is what `make check` runs, so a
# normal test run never rewrites tracked documentation.  A hand edit that overstates
# progress fails `presentation-check` rather than shipping.
presentation:
	python3 tools/presentation.py sync

presentation-check:
	python3 tools/presentation.py check

# What the gates actually reported, as committed records: command, exit code, commit,
# date. `list` only reads; `record-validation` runs the gates and rewrites the records
# (which is why it is never part of `make check`).
validation:
	python3 tools/validation.py list
	python3 tools/validation.py verify-records

record-validation:
	python3 tools/validation.py run check verify roadmap docs-check presentation-check \
	  boot-test stubtest ovmf-test

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
ovmf-test: $(ISO) $(BUILD)/efi.img
	python3 tests/ovmf_boot.py --iso $(ISO) --efi-image $(BUILD)/efi.img \
	  --kernel $(BUILD)/kernel.bin --json-out $(BUILD)/ovmf-report.json \
	  --matrix-out $(BUILD)/ovmf-matrix.json $(OVMF_ARGS)

# Provision the external dependencies of the gate above (QEMU and a real
# TianoCore OVMF build) with the distribution's package manager, then print the
# firmware paths and hashes the report has to record.  Nothing is downloaded
# into the tree: firmware stays where the distribution put it.
ovmf-setup:
	sh tools/ovmf_setup.sh

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
	  --build-date "$(BUILD_DATE)" \
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
