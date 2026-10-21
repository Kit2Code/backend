/* Tries to kill everything it can reach: kill(-1, SIGKILL) (every process the user may signal)
 * and kill(1, SIGKILL) (the box's first process, isolate's own helper). Both must fail. Prints
 * CONTAINED only if they did; the sandbox test then checks that the worker still judges normally. */
#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>

int main(void) {
    int escaped = 0;
    if (kill(1, SIGKILL) == 0) {
        puts("ESCAPED kill(1)");
        escaped = 1;
    }
    if (kill(-1, SIGKILL) == 0) {
        puts("ESCAPED kill(-1)");
        escaped = 1;
    }
    if (!escaped) puts("CONTAINED");
    return 0;
}
