/* Prints "1 " over and over on one line, stopping just under the 64 MB file-size limit.
 *
 * The output is then compared by white-diff in the worker itself, outside any sandbox: about 33
 * million tokens on one line. A comparison that splits whole lines into token lists would need
 * gigabytes for this, so it must be compared without doing that. */
#include <stdio.h>
#include <string.h>

int main(void) {
    static char chunk[1 << 20];
    for (size_t i = 0; i < sizeof chunk; i += 2) memcpy(chunk + i, "1 ", 2);
    for (int i = 0; i < 63; i++) fwrite(chunk, 1, sizeof chunk, stdout);
    fwrite(chunk, 1, sizeof chunk - 2, stdout); /* 64 MB - 2 bytes in all */
    return 0;
}
