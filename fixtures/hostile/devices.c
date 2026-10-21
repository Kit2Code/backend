/* Looks at the box's /dev: it must hold only null, zero, full, random, urandom (and the box's own
 * shm folder), none of the worker's other devices may open, a device node made inside the box
 * must not work, and the five allowed devices must still behave. Prints CONTAINED only if all of
 * that holds, and otherwise what it found. */
#include <dirent.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <unistd.h>

static int escaped = 0;

static void must_not_open(const char *path) {
    int flags[] = {O_RDONLY, O_WRONLY, O_RDWR};
    for (int i = 0; i < 3; i++) {
        int fd = open(path, flags[i] | O_NONBLOCK | O_NOCTTY);
        if (fd >= 0) {
            printf("ESCAPED opened %s (flags %d)\n", path, flags[i]);
            escaped = 1;
            close(fd);
        }
    }
}

static void check_listing(void) {
    static const char *allowed[] = {".", "..", "null", "zero", "full", "random", "urandom", "shm"};
    DIR *dev = opendir("/dev");
    if (!dev) {
        puts("ESCAPED cannot list /dev");
        escaped = 1;
        return;
    }
    struct dirent *entry;
    while ((entry = readdir(dev))) {
        int ok = 0;
        for (size_t i = 0; i < sizeof allowed / sizeof allowed[0]; i++)
            if (strcmp(entry->d_name, allowed[i]) == 0) ok = 1;
        if (!ok) {
            printf("ESCAPED /dev/%s exists\n", entry->d_name);
            escaped = 1;
        }
    }
    closedir(dev);
}

static void check_allowed_devices_work(void) {
    unsigned char buffer[64];
    int fd = open("/dev/null", O_WRONLY);
    if (fd < 0 || write(fd, "x", 1) != 1) {
        puts("BROKEN /dev/null");
        escaped = 1;
    }
    if (fd >= 0) close(fd);

    fd = open("/dev/zero", O_RDONLY);
    memset(buffer, 0xff, sizeof buffer);
    if (fd < 0 || read(fd, buffer, sizeof buffer) != (ssize_t)sizeof buffer) {
        puts("BROKEN /dev/zero");
        escaped = 1;
    } else {
        for (size_t i = 0; i < sizeof buffer; i++)
            if (buffer[i] != 0) {
                puts("BROKEN /dev/zero is not zeros");
                escaped = 1;
                break;
            }
    }
    if (fd >= 0) close(fd);

    fd = open("/dev/urandom", O_RDONLY);
    if (fd < 0 || read(fd, buffer, sizeof buffer) != (ssize_t)sizeof buffer) {
        puts("BROKEN /dev/urandom");
        escaped = 1;
    }
    if (fd >= 0) close(fd);
}

int main(void) {
    const char *forbidden[] = {
        "/dev/kmsg", "/dev/mem",   "/dev/sda", "/dev/fuse", "/dev/vsock", "/dev/dxg",
        "/dev/kvm",  "/dev/net/tun", "/dev/ptmx", "/dev/tty", "/dev/loop0", "/dev/vhost-net",
    };
    for (size_t i = 0; i < sizeof forbidden / sizeof forbidden[0]; i++) must_not_open(forbidden[i]);

    /* Making our own node for the kernel log (char 1:11) must fail, or at least not open. */
    if (mknod("hostile-kmsg", S_IFCHR | 0666, makedev(1, 11)) == 0) {
        must_not_open("hostile-kmsg");
        unlink("hostile-kmsg");
    }
    if (mkdir("/dev/hostile-dir", 0755) == 0) {
        puts("ESCAPED created a folder in /dev");
        escaped = 1;
    }

    check_listing();
    check_allowed_devices_work();
    if (!escaped) puts("CONTAINED");
    return 0;
}
