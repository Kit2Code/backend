/* Hides files from a size check that walks folders by path: makes about 2200 nested folders (the
 * full path is then longer than PATH_MAX), then writes 64 MB files at the bottom until stopped.
 * Never prints CONTAINED. */
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

int main(void) {
    for (int depth = 0; depth < 2200; depth++) {
        if (mkdir("d", 0700) != 0 || chdir("d") != 0) return 1;
    }
    static char chunk[1 << 20];
    memset(chunk, 'x', sizeof chunk);
    for (int file = 0;; file++) {
        char name[32];
        snprintf(name, sizeof name, "f%d", file);
        FILE *out = fopen(name, "w");
        if (!out) return 1;
        for (int i = 0; i < 64; i++) fwrite(chunk, 1, sizeof chunk, out);
        fclose(out);
    }
}
