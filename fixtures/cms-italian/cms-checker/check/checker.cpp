// CMS-style checker: checker <input> <correct output> <contestant output>
// Prints the outcome (0.0 to 1.0) on stdout and a message on stderr.
#include <cstdio>

int main(int argc, char **argv) {
    if (argc != 4) return 1;
    FILE *in = std::fopen(argv[1], "r");
    FILE *out = std::fopen(argv[3], "r");
    long long n, a, b;
    if (!in || !out || std::fscanf(in, "%lld", &n) != 1) return 1;
    if (std::fscanf(out, "%lld %lld", &a, &b) != 2 || a * b != n) {
        std::printf("0.0\n");
        std::fprintf(stderr, "translate:wrong\n");
    } else if (a == 1 || b == 1) {
        std::printf("0.5\n");
        std::fprintf(stderr, "translate:partial\n");
    } else {
        std::printf("1.0\n");
        std::fprintf(stderr, "translate:success\n");
    }
    return 0;
}
