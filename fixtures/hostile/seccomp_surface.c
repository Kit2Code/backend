/* Tries kernel interfaces often used to escalate privileges: io_uring, bpf, userfaultfd, the key
 * keyring, perf events, a new user namespace, and ptrace on its parent. Prints CONTAINED only if
 * every call failed. */
#define _GNU_SOURCE
#include <linux/bpf.h>
#include <linux/perf_event.h>
#include <sched.h>
#include <stdio.h>
#include <string.h>
#include <sys/ptrace.h>
#include <sys/syscall.h>
#include <unistd.h>

static int escaped = 0;

static void must_fail(const char *what, long result) {
    if (result >= 0) {
        printf("ESCAPED %s\n", what);
        escaped = 1;
    }
}

int main(void) {
    char params[120];
    memset(params, 0, sizeof params);
    must_fail("io_uring_setup", syscall(__NR_io_uring_setup, 8, params));

    union bpf_attr attr;
    memset(&attr, 0, sizeof attr);
    attr.map_type = BPF_MAP_TYPE_ARRAY;
    attr.key_size = 4;
    attr.value_size = 4;
    attr.max_entries = 1;
    must_fail("bpf map", syscall(__NR_bpf, BPF_MAP_CREATE, &attr, sizeof attr));

    must_fail("userfaultfd", syscall(__NR_userfaultfd, 0));
    must_fail("keyctl", syscall(__NR_keyctl, 1 /* KEYCTL_JOIN_SESSION_KEYRING */, "hostile"));
    must_fail("add_key", syscall(__NR_add_key, "user", "hostile", "x", 1, -3 /* session */));

    struct perf_event_attr pe;
    memset(&pe, 0, sizeof pe);
    pe.type = PERF_TYPE_SOFTWARE;
    pe.size = sizeof pe;
    pe.config = PERF_COUNT_SW_CPU_CLOCK;
    pe.disabled = 1;
    pe.exclude_kernel = 1;
    must_fail("perf_event_open", syscall(__NR_perf_event_open, &pe, 0, -1, -1, 0));

    must_fail("unshare user namespace", unshare(CLONE_NEWUSER));
    must_fail("ptrace parent", ptrace(PTRACE_ATTACH, getppid(), 0, 0));

    if (!escaped) puts("CONTAINED");
    return 0;
}
