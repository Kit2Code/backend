/* box-exec: the first program in every isolate box. It installs a seccomp filter, then runs the
 * real program (compiler, submission, checker) in its place:
 *
 *     box-exec ./main arg...
 *
 * The filter makes the kernel interfaces most used to escalate privileges fail with EPERM:
 * io_uring, bpf, userfaultfd, the key ring, perf events, ptrace and reading other processes'
 * memory, loading kernel modules or a new kernel, new namespaces (unshare, setns, and clone with
 * a CLONE_NEW* flag), mounts (old and new mount API), the kernel log, and a few rarely used calls
 * that have had bugs. clone3 fails with ENOSYS (its flags are out of the filter's reach), so the C
 * library falls back to clone. Only native x86-64 system calls are allowed at all (no 32-bit or
 * x32 calls, which have other numbers).
 * The filter can't be removed and is inherited by every child. If it can't be installed, the
 * program is not run (exit 125). Built static, so it needs nothing from the box.
 * docs/DECISIONS.md, "Seccomp filter for sandboxed programs". */
#include <errno.h>
#include <linux/audit.h>
#include <linux/filter.h>
#include <linux/seccomp.h>
#include <stddef.h>
#include <stdio.h>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <unistd.h>

#define X32_SYSCALL_BIT 0x40000000
/* CLONE_NEWNS | NEWCGROUP | NEWUTS | NEWIPC | NEWUSER | NEWPID | NEWNET (sched.h). */
#define CLONE_NEW_FLAGS 0x7e020000
#define DENY(nr) \
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, (nr), 0, 1), \
    BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | (EPERM & SECCOMP_RET_DATA))

static struct sock_filter FILTER[] = {
    /* Native x86-64 only. */
    BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, arch)),
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, AUDIT_ARCH_X86_64, 1, 0),
    BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS),
    BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, nr)),
    BPF_JUMP(BPF_JMP | BPF_JGE | BPF_K, X32_SYSCALL_BIT, 0, 1),
    BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS),
    DENY(__NR_io_uring_setup), DENY(__NR_io_uring_enter), DENY(__NR_io_uring_register),
    DENY(__NR_bpf), DENY(__NR_userfaultfd), DENY(__NR_perf_event_open),
    DENY(__NR_keyctl), DENY(__NR_add_key), DENY(__NR_request_key),
    DENY(__NR_ptrace), DENY(__NR_process_vm_readv), DENY(__NR_process_vm_writev),
    DENY(__NR_kexec_load), DENY(__NR_kexec_file_load),
    DENY(__NR_init_module), DENY(__NR_finit_module), DENY(__NR_delete_module),
    DENY(__NR_unshare), DENY(__NR_setns), DENY(__NR_mount), DENY(__NR_umount2),
    DENY(__NR_pivot_root), DENY(__NR_open_by_handle_at), DENY(__NR_name_to_handle_at),
    DENY(__NR_swapon), DENY(__NR_swapoff), DENY(__NR_reboot), DENY(__NR_acct),
    DENY(__NR_syslog), DENY(__NR_fsopen), DENY(__NR_fsconfig), DENY(__NR_fsmount),
    DENY(__NR_fspick), DENY(__NR_open_tree), DENY(__NR_move_mount), DENY(__NR_mount_setattr),
    DENY(__NR_pidfd_getfd), DENY(__NR_kcmp), DENY(__NR_process_madvise), DENY(__NR_move_pages),
    DENY(__NR_migrate_pages), DENY(__NR_memfd_secret), DENY(__NR_vmsplice),
    DENY(__NR_personality), DENY(__NR_quotactl), DENY(__NR_quotactl_fd),
    DENY(__NR_lookup_dcookie), DENY(__NR_fanotify_init),
#ifdef __NR_open_tree_attr
    DENY(__NR_open_tree_attr),
#endif
    /* clone3: ENOSYS, so the C library uses clone. */
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_clone3, 0, 1),
    BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | (ENOSYS & SECCOMP_RET_DATA)),
    /* clone with any CLONE_NEW* flag (the low 32 bits of its first argument): EPERM. */
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_clone, 0, 3),
    BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, args[0])),
    BPF_JUMP(BPF_JMP | BPF_JSET | BPF_K, CLONE_NEW_FLAGS, 0, 1),
    BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | (EPERM & SECCOMP_RET_DATA)),
    BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW),
};

int main(int argc, char **argv) {
    if (argc < 2) {
        fputs("usage: box-exec PROGRAM [ARGUMENT...]\n", stderr);
        return 125;
    }
    struct sock_fprog program = {
        .len = (unsigned short)(sizeof FILTER / sizeof FILTER[0]),
        .filter = FILTER,
    };
    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0) {
        perror("box-exec: no_new_privs");
        return 125;
    }
    if (prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &program) != 0) {
        perror("box-exec: seccomp");
        return 125;
    }
    execvp(argv[1], argv + 1);
    perror("box-exec: exec");
    return 127;
}
