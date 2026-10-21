/* Tries to use 600 MB under a 256 MB limit through memory that isn't the heap: 300 MB of
 * anonymous memfds and 300 MB of files in /dev/shm, all filled. These are tmpfs pages, charged to
 * the box's cgroup, so it must be stopped: MLE (or RE if it gets an error instead of being killed).
 * The memory is spread over 50 MB files so the 64 MB file-size limit doesn't stop it first. If it
 * gets through it prints ESCAPED. */
#define _GNU_SOURCE
#include <fcntl.h>
#include <stdio.h>
#include <sys/mman.h>
#include <unistd.h>

#define PIECE (50L << 20)
#define PIECES 6

static int fill(int fd) {
    if (fd < 0 || ftruncate(fd, PIECE) != 0) return -1;
    char *memory = mmap(NULL, PIECE, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (memory == MAP_FAILED) return -1;
    for (long i = 0; i < PIECE; i += 4096) memory[i] = 1;
    return 0;
}

int main(void) {
    for (int i = 0; i < PIECES; i++) {
        if (fill(memfd_create("hostile", 0)) != 0) return 1;
        char name[64];
        snprintf(name, sizeof name, "/dev/shm/hostile-shm-%d", i);
        if (fill(open(name, O_RDWR | O_CREAT | O_TRUNC, 0600)) != 0) return 1;
    }
    puts("ESCAPED 600 MB in memfds and /dev/shm");
    return 0;
}
