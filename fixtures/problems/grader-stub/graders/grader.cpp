#include <cstdio>
#include "adder.h"

int main() {
    long long a, b;
    std::scanf("%lld %lld", &a, &b);
    std::printf("%lld\n", add(a, b));
}
