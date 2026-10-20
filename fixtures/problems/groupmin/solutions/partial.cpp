#include <cstdio>

// Only right for small values (subtask 1): int overflows on the big ones.
int main() {
    int n;
    std::scanf("%d", &n);
    int total = 0;
    for (int i = 0; i < n; i++) {
        int x;
        std::scanf("%d", &x);
        total += x;
    }
    std::printf("%d\n", total);
}
