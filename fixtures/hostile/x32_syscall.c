/* Makes system calls the box's filter only knows by another number: an x32 call (getpid with the
 * x32 bit) and a 32-bit call through int 0x80. Either one kills the program (SIGSYS, so RE); if both
 * work it prints ESCAPED. */
#include <stdio.h>
#include <unistd.h>

int main(void) {
    long result;
    __asm__ volatile("syscall" : "=a"(result) : "a"(0x40000000L | 39) : "rcx", "r11", "memory");
    printf("x32 getpid returned %ld\n", result);
    __asm__ volatile("int $0x80" : "=a"(result) : "a"(20L) : "memory");  /* i386 getpid */
    printf("ESCAPED int 0x80 getpid returned %ld\n", result);
    return 0;
}
