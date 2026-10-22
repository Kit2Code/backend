/* Reads the host's kernel log (syslog READ_ALL, i.e. dmesg) from a box. Prints CONTAINED only if
 * that fails. */
#include <stdio.h>
#include <sys/syscall.h>
#include <unistd.h>

int main(void) {
    static char buffer[4096];
    long got = syscall(SYS_syslog, 3 /* SYSLOG_ACTION_READ_ALL */, buffer, (long)sizeof buffer);
    if (got >= 0) {
        printf("ESCAPED read %ld bytes of the kernel log\n", got);
        return 0;
    }
    puts("CONTAINED");
    return 0;
}
