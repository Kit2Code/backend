/* Tries to make new namespaces with clone() and clone3(), the new mount API (fsopen, open_tree,
 * move_mount), and other calls box-exec denies: memfd_secret, pidfd_getfd, kcmp. Prints CONTAINED
 * only if every one fails. */
#define _GNU_SOURCE
#include <fcntl.h>
#include <sched.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>

#ifndef SYS_clone3
#define SYS_clone3 435
#endif

struct clone_args_v0 {
    uint64_t flags, pidfd, child_tid, parent_tid, exit_signal, stack, stack_size, tls;
};

static int escaped = 0;

static void report(const char *what, long result) {
    if (result >= 0) {
        printf("ESCAPED %s\n", what);
        escaped = 1;
    }
}

static long try_clone(unsigned long flags) {
    long pid = syscall(SYS_clone, flags | SIGCHLD, 0, 0, 0, 0);
    if (pid == 0) _exit(0);
    if (pid > 0) waitpid((pid_t)pid, NULL, 0);
    return pid;
}

static long try_clone3(uint64_t flags) {
    struct clone_args_v0 args;
    memset(&args, 0, sizeof args);
    args.flags = flags;
    args.exit_signal = SIGCHLD;
    long pid = syscall(SYS_clone3, &args, sizeof args);
    if (pid == 0) _exit(0);
    if (pid > 0) waitpid((pid_t)pid, NULL, 0);
    return pid;
}

int main(void) {
    static const unsigned long FLAGS[] = {CLONE_NEWNS, CLONE_NEWUTS, CLONE_NEWIPC, CLONE_NEWUSER,
                                          CLONE_NEWPID, CLONE_NEWNET, CLONE_NEWCGROUP};
    for (unsigned i = 0; i < sizeof FLAGS / sizeof FLAGS[0]; i++) {
        char what[64];
        snprintf(what, sizeof what, "clone(0x%lx)", FLAGS[i]);
        report(what, try_clone(FLAGS[i]));
        snprintf(what, sizeof what, "clone3(0x%lx)", FLAGS[i]);
        report(what, try_clone3(FLAGS[i]));
    }
    report("fsopen", syscall(SYS_fsopen, "tmpfs", 0));
    report("open_tree", syscall(SYS_open_tree, AT_FDCWD, "/", 1 /* OPEN_TREE_CLONE */));
    report("move_mount", syscall(SYS_move_mount, AT_FDCWD, "/", AT_FDCWD, "/tmp", 0));
    report("memfd_secret", syscall(SYS_memfd_secret, 0));
    report("pidfd_getfd", syscall(SYS_pidfd_getfd, 0, 0, 0));
    report("kcmp", syscall(SYS_kcmp, getpid(), getpid(), 0 /* KCMP_FILE */, 0, 0));
    if (!escaped) puts("CONTAINED");
    return 0;
}
