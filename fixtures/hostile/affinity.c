/* Checks that the box is pinned to one CPU and can't raise its own limits: sched_getaffinity
 * shows exactly 1 CPU; after asking for every CPU it still has exactly that one; and raising the
 * hard limit of any resource limit fails. Prints CONTAINED only if all of that holds. */
#define _GNU_SOURCE
#include <sched.h>
#include <stdio.h>
#include <sys/resource.h>

int main(void) {
    int escaped = 0;
    cpu_set_t before, all, after;
    CPU_ZERO(&before);
    if (sched_getaffinity(0, sizeof before, &before) != 0 || CPU_COUNT(&before) != 1) {
        printf("ESCAPED %d CPUs\n", CPU_COUNT(&before));
        escaped = 1;
    }
    CPU_ZERO(&all);
    for (int cpu = 0; cpu < CPU_SETSIZE; cpu++) CPU_SET(cpu, &all);
    sched_setaffinity(0, sizeof all, &all); /* may "succeed": the cpuset still applies */
    CPU_ZERO(&after);
    if (sched_getaffinity(0, sizeof after, &after) != 0 || !CPU_EQUAL(&before, &after)) {
        printf("ESCAPED now on %d CPUs\n", CPU_COUNT(&after));
        escaped = 1;
    }
    const int resources[] = {RLIMIT_NPROC, RLIMIT_FSIZE, RLIMIT_STACK, RLIMIT_NOFILE,
                             RLIMIT_CORE,  RLIMIT_MEMLOCK, RLIMIT_AS, RLIMIT_CPU};
    const char *names[] = {"NPROC", "FSIZE", "STACK", "NOFILE", "CORE", "MEMLOCK", "AS", "CPU"};
    for (int i = 0; i < 8; i++) {
        struct rlimit limit;
        if (getrlimit(resources[i], &limit) != 0) continue;
        if (limit.rlim_max == RLIM_INFINITY) continue; /* nothing to raise */
        struct rlimit raised = {limit.rlim_max + 1, limit.rlim_max + 1};
        struct rlimit unlimited = {RLIM_INFINITY, RLIM_INFINITY};
        if (setrlimit(resources[i], &raised) == 0 || setrlimit(resources[i], &unlimited) == 0) {
            printf("ESCAPED raised RLIMIT_%s\n", names[i]);
            escaped = 1;
        }
    }
    if (!escaped) puts("CONTAINED");
    return 0;
}
