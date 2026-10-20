#include <cstdio>

int main() {
    int n;
    std::scanf("%d", &n);
    for (int i = n; i >= 1; i--) std::printf("%d%c", i, i == 1 ? '\n' : ' ');
}
