/* Tries to start a thread, fork, and run another program. The box allows one process (threads
 * count too), so all three must fail. Prints CONTAINED only if they all did. */
#include <pthread.h>
#include <spawn.h>
#include <stdio.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;

static void *thread_body(void *unused) {
    (void)unused;
    return NULL;
}

int main(void) {
    int escaped = 0;
    pthread_t thread;
    if (pthread_create(&thread, NULL, thread_body, NULL) == 0) {
        puts("ESCAPED thread");
        escaped = 1;
        pthread_join(thread, NULL);
    }
    pid_t child = fork();
    if (child == 0) _exit(0);
    if (child > 0) {
        puts("ESCAPED fork");
        escaped = 1;
        waitpid(child, NULL, 0);
    }
    char *argv[] = {"/bin/true", NULL};
    pid_t spawned;
    if (posix_spawn(&spawned, "/bin/true", NULL, NULL, argv, environ) == 0) {
        puts("ESCAPED spawn");
        escaped = 1;
        waitpid(spawned, NULL, 0);
    }
    if (!escaped) puts("CONTAINED");
    return 0;
}
