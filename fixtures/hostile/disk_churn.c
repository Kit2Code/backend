/* Writes a 60 MB file, forces it onto the disk, deletes it, and does it again until stopped:
 * it never keeps much, but keeps the disk busy. Never prints CONTAINED. */
#include <stdio.h>
#include <string.h>
#include <unistd.h>

int main(void) {
    static char chunk[1 << 20];
    memset(chunk, 'x', sizeof chunk);
    for (;;) {
        FILE *out = fopen("hostile-churn", "w");
        if (!out) return 1;
        for (int i = 0; i < 60; i++) fwrite(chunk, 1, sizeof chunk, out);
        fflush(out);
        fsync(fileno(out));
        fclose(out);
        unlink("hostile-churn");
    }
}
