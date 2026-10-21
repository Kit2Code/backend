/* Writes 1 GB as 32 files of 32 MB each, every one under the file-size limit. */
#include <stdio.h>
#include <string.h>

int main(void) {
    static char chunk[1 << 20];
    memset(chunk, 'x', sizeof chunk);
    for (int file = 0; file < 32; file++) {
        char name[32];
        snprintf(name, sizeof name, "hostile-fill-%02d", file);
        FILE *out = fopen(name, "w");
        if (!out) return 1;
        for (int i = 0; i < 32; i++) fwrite(chunk, 1, sizeof chunk, out);
        fclose(out);
    }
    puts("CONTAINED");
    return 0;
}
