#include <cstdio>

// Hostile on purpose: prints forever, to exercise the output limit.
int main() {
    for (;;) std::fputs("xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\n", stdout);
}
