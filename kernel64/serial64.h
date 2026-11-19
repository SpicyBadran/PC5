#ifndef PC5_SERIAL64_H
#define PC5_SERIAL64_H
void serial64_init(void);
void serial64_write(const char *text);
void serial64_hex(u64 value);
void serial64_dec(u64 value);
#endif
