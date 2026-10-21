// CMS-style checker for judge tests: the contestant's first token chooses the behaviour.
#include <cstdio>
#include <cstdlib>
#include <cstring>

int main(int argc, char **argv) {
    if (argc != 4) return 1;
    char token[64] = "";
    FILE *out = std::fopen(argv[3], "r");
    if (out) std::fscanf(out, "%63s", token);
    if (!std::strcmp(token, "ok")) { std::puts("1.0"); std::fputs("translate:success\n", stderr); }
    else if (!std::strcmp(token, "half")) { std::puts("0.5"); std::fputs("translate:partial\n", stderr); }
    else if (!std::strcmp(token, "big")) { std::puts("2.0"); }           // not in [0, 1]: judge error
    else if (!std::strcmp(token, "garbage")) { std::puts("lots"); }      // not a number: judge error
    else if (!std::strcmp(token, "crash")) { std::abort(); }            // checker crash: judge error
    else { std::puts("0.0"); std::fputs("translate:wrong\n", stderr); }
    return 0;
}
