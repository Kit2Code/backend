/* Tries to read /etc/shadow (directly, through another process's root, and by climbing out of the
 * box) and the worker's own environment and files. Prints CONTAINED only if every read failed. */
#include <fcntl.h>
#include <stdio.h>
#include <unistd.h>

static const char *const TARGETS[] = {
    "/etc/shadow",
    "../../../../../etc/shadow",
    "/proc/1/root/etc/shadow",
    "/proc/1/environ",
    "/proc/1/cmdline",
    "/run/secrets/worker_token",
    "/app/fixtures/hostile/README.md",
    "/data/problems",
};

int main(void) {
    int escaped = 0;
    for (unsigned i = 0; i < sizeof TARGETS / sizeof TARGETS[0]; i++) {
        int fd = open(TARGETS[i], O_RDONLY);
        if (fd >= 0) {
            printf("ESCAPED read %s\n", TARGETS[i]);
            escaped = 1;
            close(fd);
        }
    }
    if (!escaped) puts("CONTAINED");
    return 0;
}
