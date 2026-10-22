/* Checks that the box's own seccomp filter (box-exec) is in place: ptrace(PTRACE_TRACEME) is
 * allowed by Docker's filter and by isolate, and only box-exec turns it into EPERM. Prints
 * CONTAINED only then. */
#include <errno.h>
#include <stdio.h>
#include <sys/ptrace.h>

int main(void) {
    if (ptrace(PTRACE_TRACEME, 0, NULL, NULL) == 0) {
        puts("ESCAPED ptrace(TRACEME) worked: no box-exec filter");
        return 0;
    }
    if (errno != EPERM) {
        printf("ESCAPED ptrace(TRACEME) failed with errno %d, not EPERM\n", errno);
        return 0;
    }
    puts("CONTAINED");
    return 0;
}
