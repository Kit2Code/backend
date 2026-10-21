/* Replaces its own stdout file in the box (`_stdout`, in the working folder) with a FIFO. The
 * judge must not block opening or reading it: no output, so WA, and quickly. It still writes
 * CONTAINED to its real stdout, which nobody can read any more once the name is gone. */
#include <stdio.h>
#include <sys/stat.h>
#include <unistd.h>

int main(void) {
    puts("CONTAINED");
    fflush(stdout);
    if (unlink("_stdout") != 0) return 0;
    mkfifo("_stdout", 0666);
    return 0;
}
