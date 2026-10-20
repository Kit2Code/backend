#include <stdio.h>
#include "adder.h"

int main(void) {
    long long a, b;
    scanf("%lld %lld", &a, &b);
    printf("%lld\n", add(a, b));
    return 0;
}
