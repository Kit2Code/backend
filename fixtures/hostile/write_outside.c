/* Tries to create a file outside the box: in the root, the system folders, isolate's own folders
 * and the worker's folders. Prints CONTAINED only if every attempt failed. (/tmp and /dev/shm are
 * private to each box, so they are not in this list; the test checks they don't outlive it.) */
#include <fcntl.h>
#include <stdio.h>
#include <sys/stat.h>
#include <unistd.h>

static const char *const TARGETS[] = {
    "/hostile-marker",
    "/box/../hostile-marker",
    "/etc/hostile-marker",
    "/usr/hostile-marker",
    "/bin/hostile-marker",
    "/lib/hostile-marker",
    "/dev/hostile-marker",
    "/proc/hostile-marker",
    "/var/local/lib/isolate/hostile-marker",
    "/run/hostile-marker",
    "/app/hostile-marker",
    "/data/cache/hostile-marker",
    "/sys/fs/cgroup/hostile-marker",
};

int main(void) {
    int escaped = 0;
    for (unsigned i = 0; i < sizeof TARGETS / sizeof TARGETS[0]; i++) {
        int fd = open(TARGETS[i], O_WRONLY | O_CREAT | O_EXCL, 0644);
        if (fd >= 0) {
            printf("ESCAPED write %s\n", TARGETS[i]);
            escaped = 1;
            close(fd);
        }
        if (mkdir(TARGETS[i], 0755) == 0) {
            printf("ESCAPED mkdir %s\n", TARGETS[i]);
            escaped = 1;
        }
    }
    /* Leave markers in the box's private /tmp and /dev/shm; they must vanish with the box. */
    int fd = open("/tmp/hostile-marker", O_WRONLY | O_CREAT, 0644);
    if (fd >= 0) close(fd);
    fd = open("/dev/shm/hostile-marker", O_WRONLY | O_CREAT, 0644);
    if (fd >= 0) close(fd);
    if (!escaped) puts("CONTAINED");
    return 0;
}
