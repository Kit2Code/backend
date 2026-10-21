/* Tries to become root through setuid programs. First, without leaving this program: every
 * setuid/setgid file in the usual program folders must sit on a nosuid mount, and setuid(0),
 * setgid(0) and seteuid(0) must fail. Then it runs ONE target with execv (the box allows one
 * process, so the target replaces this program): by default `isolate --box-id=100 --cleanup`,
 * found on PATH; the sandbox tests swap in other targets (su, mount, isolate by full path).
 *
 * How to read the verdict (expected output: CONTAINED):
 *   RE  the target ran without privileges and refused (contained);
 *   AC  no target could be started at all, so it printed CONTAINED (contained);
 *   WA  escaped: it printed ESCAPED lines, or the target ran as root, succeeded and printed
 *       nothing. */
#include <dirent.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <unistd.h>

static char *target[] = {"isolate", "--box-id=100", "--cleanup", NULL};

static const char *const folders[] = {
    "/bin", "/sbin", "/usr/bin", "/usr/sbin", "/usr/local/bin", "/usr/local/sbin",
    "/usr/lib/dbus-1.0", "/usr/lib/openssh", "/usr/libexec",
};

/* Prints and counts setuid/setgid files in `folder` that are not on a nosuid mount. */
static int usable_setuid_files(const char *folder) {
    DIR *dir = opendir(folder);
    if (!dir) return 0;
    int found = 0;
    struct dirent *entry;
    while ((entry = readdir(dir)) != NULL) {
        char path[4096];
        struct stat info;
        struct statvfs mount;
        snprintf(path, sizeof path, "%s/%s", folder, entry->d_name);
        if (stat(path, &info) != 0 || !S_ISREG(info.st_mode)) continue;
        if (!(info.st_mode & (S_ISUID | S_ISGID))) continue;
        if (statvfs(path, &mount) == 0 && (mount.f_flag & ST_NOSUID)) continue;
        printf("ESCAPED setuid file on a suid mount: %s\n", path);
        found++;
    }
    closedir(dir);
    return found;
}

static void try_exec(const char *path) {
    execv(path, target);
}

int main(void) {
    int escaped = 0;
    for (size_t i = 0; i < sizeof folders / sizeof folders[0]; i++) {
        escaped += usable_setuid_files(folders[i]);
    }
    if (geteuid() == 0 || getegid() == 0) {
        puts("ESCAPED running as root");
        escaped++;
    }
    if (setuid(0) == 0 || seteuid(0) == 0 || setgid(0) == 0) {
        puts("ESCAPED became root");
        escaped++;
    }
    if (escaped) return 0;
    fflush(stdout);
    if (strchr(target[0], '/')) {
        try_exec(target[0]);
    } else {
        const char *path = getenv("PATH");
        char copy[4096];
        snprintf(copy, sizeof copy, "%s", path ? path : "/usr/local/bin:/usr/bin:/bin");
        for (char *folder = strtok(copy, ":"); folder; folder = strtok(NULL, ":")) {
            char full[4096];
            snprintf(full, sizeof full, "%s/%s", folder, target[0]);
            try_exec(full);
        }
    }
    puts("CONTAINED"); /* no target could be started */
    return 0;
}
