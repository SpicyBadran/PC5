/* The record the 64-bit loader publishes for whoever built the image.
 *
 * The 32-bit loader publishes `PC5HND01` (bridge address + far-jump address) because
 * its handover includes a mode switch that a harness has to emulate.  The 64-bit
 * loader stays in long mode, so it publishes `PC5HND02` instead of pretending to have
 * a bridge it does not use, and states the boot-protocol version and the boot-info
 * size it hands over.
 */
#ifndef PC5_HANDOVER64_H
#define PC5_HANDOVER64_H

#define PC5_HANDOVER64_MAGIC "PC5HND02"

struct pc5_handover64 {
    char magic[8];              /* "PC5HND02", no terminator in the comparison */
    unsigned long long protocol;      /* pc5_boot64.version this loader emits */
    unsigned long long boot_info_size;
    unsigned long long page_table_pages;
    unsigned long long identity_span; /* bytes identity-mapped below 4 GiB */
};

#endif
