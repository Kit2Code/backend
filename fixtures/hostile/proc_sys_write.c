/* Tries to change kernel settings from a box: opens /proc/sys/kernel/core_pattern (a core
 * dump handler runs as root on the host) and /proc/sysrq-trigger for writing, and tries to
 * mount over or unmount /proc/sys. Prints CONTAINED only if every attempt failed. */
#include <fcntl.h>
#include <stdio.h>
#include <sys/mount.h>
#include <unistd.h>

static const char *const TARGETS[] = {
    "/proc/sys/kernel/core_pattern",
    "/proc/sysrq-trigger",
    "/proc/sys/kernel/modprobe",
    "/proc/sys/vm/drop_caches",
    "/proc/1/root/proc/sys/kernel/core_pattern",
};

int main(void) {
    int escaped = 0;
    for (unsigned i = 0; i < sizeof TARGETS / sizeof TARGETS[0]; i++) {
        int fd = open(TARGETS[i], O_WRONLY);
        if (fd >= 0) {
            printf("ESCAPED opened %s for writing\n", TARGETS[i]);
            escaped = 1;
            close(fd);
        }
    }
    if (umount2("/proc/sys", MNT_DETACH) == 0) {
        puts("ESCAPED unmounted /proc/sys");
        escaped = 1;
    }
    if (mount("proc", "/proc/sys", "proc", 0, NULL) == 0) {
        puts("ESCAPED mounted over /proc/sys");
        escaped = 1;
    }
    if (mount(NULL, "/proc/sys", NULL, MS_REMOUNT, NULL) == 0) {
        puts("ESCAPED remounted /proc/sys");
        escaped = 1;
    }
    if (!escaped) puts("CONTAINED");
    return 0;
}
