#include <cstdio>

int main() {
    int n;
    std::scanf("%d", &n);
    long long total = 0;
    for (int i = 0; i < n; i++) {
        long long x;
        std::scanf("%lld", &x);
        total += x;
    }
    std::printf("%lld\n", total);
}
