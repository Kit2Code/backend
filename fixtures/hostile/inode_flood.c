/* Makes empty files (no disk blocks, so a size check sees nothing) until stopped: slows down every
 * check that walks the box. Never prints CONTAINED. */
#include <stdio.h>

int main(void) {
    for (long i = 0;; i++) {
        char name[32];
        snprintf(name, sizeof name, "e%ld", i);
        FILE *out = fopen(name, "w");
        if (!out) return 1;
        fclose(out);
    }
}
