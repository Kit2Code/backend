/* Tries to leave its cgroup or change cgroup limits: writes to cgroup.procs, memory.max,
 * cgroup.kill and cgroup.freeze (directly, by climbing up, and through /proc), and mounts its own
 * cgroup2 filesystem. Prints CONTAINED only if every attempt failed. */
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <unistd.h>

static const char *const TARGETS[] = {
    "/sys/fs/cgroup/cgroup.procs",
    "/sys/fs/cgroup/../cgroup.procs",
    "/sys/fs/cgroup/memory.max",
    "/sys/fs/cgroup/cgroup.kill",
    "/sys/fs/cgroup/cgroup.freeze",
    "/sys/fs/cgroup/grader-judge/memory.max",
    "/sys/fs/cgroup/grader-judge/cgroup.procs",
    "/proc/1/root/sys/fs/cgroup/cgroup.procs",
    "/proc/self/root/sys/fs/cgroup/cgroup.procs",
};

static int wrote(const char *path, const char *text) {
    int fd = open(path, O_WRONLY);
    if (fd < 0) return 0;
    int ok = write(fd, text, strlen(text)) > 0;
    close(fd);
    return ok;
}

int main(void) {
    int escaped = 0;
    for (unsigned i = 0; i < sizeof TARGETS / sizeof TARGETS[0]; i++) {
        /* "0" = our own process for cgroup.procs; harmless values for the others. */
        if (wrote(TARGETS[i], "0\n") || wrote(TARGETS[i], "max\n")) {
            printf("ESCAPED wrote %s\n", TARGETS[i]);
            escaped = 1;
        }
    }
    mkdir("hostile-cg", 0700);
    if (mount("none", "hostile-cg", "cgroup2", 0, NULL) == 0) {
        puts("ESCAPED mounted cgroup2");
        escaped = 1;
        umount("hostile-cg");
    }
    if (!escaped) puts("CONTAINED");
    return 0;
}
