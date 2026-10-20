#include <cstdio>

// Wrong on purpose: int overflows on the last test.
int main() {
    int a, b;
    std::scanf("%d %d", &a, &b);
    std::printf("%d\n", a + b);
}
