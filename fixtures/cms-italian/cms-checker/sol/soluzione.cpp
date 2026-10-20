#include <cstdio>

int main() {
    long long n;
    std::scanf("%lld", &n);
    for (long long a = 2; a * a <= n; a++) {
        if (n % a == 0) {
            std::printf("%lld %lld\n", a, n / a);
            return 0;
        }
    }
    std::printf("1 %lld\n", n);
}
