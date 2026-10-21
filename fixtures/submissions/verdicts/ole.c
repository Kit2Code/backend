#include <stdio.h>
#include <string.h>

int main(void) {
    static char line[1 << 20];
    memset(line, '7', sizeof line);
    for (;;) fwrite(line, 1, sizeof line, stdout);
}
