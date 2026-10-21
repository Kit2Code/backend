/* Sleeps forever without using CPU: only the wall-time limit can stop it. */
#include <unistd.h>

int main(void) {
    for (;;) pause();
}
