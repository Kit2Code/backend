// CMS-style checker: checker <input> <correct output> <contestant output>.
// Half of the points for the right sum, half for the right product.
#include <cstdio>

int main(int argc, char **argv) {
    if (argc != 4) return 1;
    long long want_sum = 0, want_product = 0, sum = 0, product = 0;
    FILE *correct = std::fopen(argv[2], "r");
    FILE *out = std::fopen(argv[3], "r");
    if (!correct || std::fscanf(correct, "%lld %lld", &want_sum, &want_product) != 2) return 1;
    int read = out ? std::fscanf(out, "%lld %lld", &sum, &product) : 0;
    double outcome = 0.0;
    if (read >= 1 && sum == want_sum) outcome += 0.5;
    if (read >= 2 && product == want_product) outcome += 0.5;
    std::printf("%.1f\n", outcome);
    if (outcome == 1.0) std::fputs("translate:success\n", stderr);
    else if (outcome > 0.0) std::fputs("translate:partial\n", stderr);
    else std::fputs("translate:wrong\n", stderr);
    return 0;
}
