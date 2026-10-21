/* Fork bomb: forks forever, ignoring every failure. The box allows one process, so no fork can
 * succeed and the program only burns CPU until the time limit stops it. */
#include <unistd.h>

int main(void) {
    for (;;) fork();
}
