/* Checks who and where it runs: a UID and GID of 70000 or more (isolate's first_uid + box id),
 * and its cgroup is grader-judge/box-1NN, one of the judge's boxes 100-109. Prints CONTAINED only
 * if all of that holds, and otherwise what it found. */
#include <stdio.h>
#include <string.h>
#include <unistd.h>

int main(void) {
    int escaped = 0;
    if (getuid() < 70000 || geteuid() < 70000 || getgid() < 70000 || getegid() < 70000) {
        printf("ESCAPED uid=%u gid=%u\n", (unsigned)getuid(), (unsigned)getgid());
        escaped = 1;
    }
    FILE *cgroup = fopen("/proc/self/cgroup", "r");
    char line[512] = "";
    if (!cgroup || !fgets(line, sizeof line, cgroup)) {
        puts("ESCAPED no cgroup");
        escaped = 1;
    } else {
        unsigned box = 0;
        char rest[8] = "";
        line[strcspn(line, "\n")] = '\0';
        int parsed = sscanf(line, "0::/grader-judge/box-%u%7s", &box, rest);
        if (parsed != 1 || box < 100 || box > 109) {
            printf("ESCAPED cgroup %s\n", line);
            escaped = 1;
        }
    }
    if (cgroup) fclose(cgroup);
    unsigned uid_box = (unsigned)getuid() - 70000;
    if (uid_box < 100 || uid_box > 109) {
        printf("ESCAPED uid %u is not a judge box's\n", (unsigned)getuid());
        escaped = 1;
    }
    if (!escaped) puts("CONTAINED");
    return 0;
}
