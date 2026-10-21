/* Tries to reach the network: TCP and UDP to 1.1.1.1:53 and TCP to web-admin:8000. The test puts
 * web-admin's address in place of WEB_ADMIN_IP (the box has no DNS to look it up). It also looks
 * for any network interface besides loopback. Prints CONTAINED only if everything failed. */
#include <arpa/inet.h>
#include <netinet/in.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

static int reached(const char *ip, int port, int type) {
    struct sockaddr_in address;
    memset(&address, 0, sizeof address);
    address.sin_family = AF_INET;
    address.sin_port = htons(port);
    if (inet_pton(AF_INET, ip, &address.sin_addr) != 1) return 1; /* a bad address is a bug */
    int fd = socket(AF_INET, type, 0);
    if (fd < 0) return 0;
    int ok = connect(fd, (struct sockaddr *)&address, sizeof address) == 0;
    if (ok && type == SOCK_DGRAM) ok = send(fd, "x", 1, 0) == 1;
    close(fd);
    return ok;
}

static int other_interfaces(void) {
    FILE *devices = fopen("/proc/net/dev", "r");
    if (!devices) return 0;
    char line[512];
    int found = 0;
    while (fgets(line, sizeof line, devices)) {
        char *colon = strchr(line, ':');
        if (!colon) continue;
        *colon = '\0';
        char *name = line + strspn(line, " ");
        if (strcmp(name, "lo") != 0) {
            printf("ESCAPED interface %s\n", name);
            found = 1;
        }
    }
    fclose(devices);
    return found;
}

int main(void) {
    int escaped = other_interfaces();
    if (reached("1.1.1.1", 53, SOCK_STREAM)) escaped = 1, puts("ESCAPED tcp 1.1.1.1:53");
    if (reached("1.1.1.1", 53, SOCK_DGRAM)) escaped = 1, puts("ESCAPED udp 1.1.1.1:53");
    if (reached("WEB_ADMIN_IP", 8000, SOCK_STREAM)) escaped = 1, puts("ESCAPED web-admin:8000");
    if (!escaped) puts("CONTAINED");
    return 0;
}
