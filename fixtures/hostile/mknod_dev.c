/* Tries to make device nodes in the box (a disk, the kernel log, memory) and open them.
 * Prints CONTAINED only if no device could be made or opened. */
#include <fcntl.h>
#include <stdio.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <unistd.h>

struct node { const char *name; mode_t type; unsigned major, minor; };

static const struct node NODES[] = {
    {"hostile-sda", S_IFBLK, 8, 0},     /* first disk */
    {"hostile-kmsg", S_IFCHR, 1, 11},   /* kernel log */
    {"hostile-mem", S_IFCHR, 1, 1},     /* physical memory */
    {"hostile-loop", S_IFBLK, 7, 0},    /* loop device */
};

int main(void) {
    int escaped = 0;
    for (unsigned i = 0; i < sizeof NODES / sizeof NODES[0]; i++) {
        const struct node *n = &NODES[i];
        if (mknod(n->name, n->type | 0600, makedev(n->major, n->minor)) == 0) {
            int fd = open(n->name, O_RDONLY);
            printf("ESCAPED made %s%s\n", n->name, fd >= 0 ? " and opened it" : "");
            if (fd >= 0) close(fd);
            unlink(n->name);
            escaped = 1;
        }
    }
    if (!escaped) puts("CONTAINED");
    return 0;
}
