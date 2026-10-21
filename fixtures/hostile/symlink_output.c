/* Replaces its own stdout file in the box (`_stdout`) with a symlink. The judge must not follow
 * it: no output, so WA. The default target is a decoy file in the box that holds the expected
 * answer, so a judge that followed the link would give AC; the sandbox tests also point it at
 * /etc/shadow and /proc/1/environ (which the judge must never read). */
#include <stdio.h>
#include <unistd.h>

#define TARGET "decoy"

int main(void) {
    FILE *decoy = fopen("decoy", "w");
    if (decoy) {
        fputs("CONTAINED\n", decoy);
        fclose(decoy);
    }
    if (unlink("_stdout") != 0) return 0;
    symlink(TARGET, "_stdout");
    return 0;
}
