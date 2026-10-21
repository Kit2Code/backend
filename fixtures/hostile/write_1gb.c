/* Writes 1 GB to stdout (a file in the box). The file-size limit stops it long before that. */
#include <stdio.h>
#include <string.h>

int main(void) {
    static char chunk[1 << 20];
    memset(chunk, 'x', sizeof chunk);
    for (int i = 0; i < 1024; i++) fwrite(chunk, 1, sizeof chunk, stdout);
    return 0;
}
