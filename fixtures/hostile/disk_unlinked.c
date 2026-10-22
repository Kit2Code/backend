/* Hides files from a size check that looks at folders: opens a file, deletes it, then writes 64 MB
 * to it through the still-open descriptor, for up to 1000 files. Never prints CONTAINED. */
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

int main(void) {
    static char chunk[1 << 20];
    memset(chunk, 'x', sizeof chunk);
    for (int file = 0; file < 1000; file++) {
        int fd = open("hidden", O_WRONLY | O_CREAT | O_TRUNC, 0600);
        if (fd < 0) return 1;
        unlink("hidden");
        for (int i = 0; i < 64; i++) {
            if (write(fd, chunk, sizeof chunk) < 0) return 1;
        }
        fsync(fd);
    }
    return 0;
}
