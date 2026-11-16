/* COM1 at 115200 8N1, polled.  Nothing here uses firmware services. */
#include "include/pc5_boot64.h"
#include "include/pc5_io.h"
#include "serial64.h"

#define COM1 0x3F8

void serial64_init(void)
{
    outb(COM1 + 1, 0x00); /* no interrupts */
    outb(COM1 + 3, 0x80); /* DLAB */
    outb(COM1 + 0, 0x01); /* divisor 1 = 115200 */
    outb(COM1 + 1, 0x00);
    outb(COM1 + 3, 0x03); /* 8N1 */
    outb(COM1 + 2, 0xC7); /* FIFO, clear */
    outb(COM1 + 4, 0x0B); /* IRQs off, RTS/DSR */
}

static void put(char c)
{
    u32 guard = 0;
    while ((inb(COM1 + 5) & 0x20) == 0) {
        if (++guard > 1000000u)
            return; /* a dead UART must not hang the kernel */
    }
    outb(COM1, (u8)c);
}

void serial64_write(const char *text)
{
    while (*text) {
        if (*text == '\n')
            put('\r');
        put(*text++);
    }
}

void serial64_hex(u64 value)
{
    static const char digits[] = "0123456789abcdef";
    char buffer[19];
    int index = 18;
    buffer[index--] = '\0';
    if (value == 0) {
        serial64_write("0");
        return;
    }
    while (value && index >= 0) {
        buffer[index--] = digits[value & 0xF];
        value >>= 4;
    }
    serial64_write("0x");
    serial64_write(&buffer[index + 1]);
}

void serial64_dec(u64 value)
{
    char buffer[21];
    int index = 20;
    buffer[index--] = '\0';
    if (value == 0) {
        serial64_write("0");
        return;
    }
    while (value && index > 0) {
        buffer[index--] = (char)('0' + (value % 10));
        value /= 10;
    }
    serial64_write(&buffer[index + 1]);
}
