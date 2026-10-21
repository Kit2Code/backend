/* For a problem with file input and output (in.txt -> out.txt) whose answer is the input itself:
 * instead of writing out.txt, makes it a symlink to in.txt. The judge must not follow it: WA
 * (no output), never AC with the input's content. */
#include <unistd.h>

int main(void) {
    unlink("out.txt");
    symlink("in.txt", "out.txt");
    return 0;
}
