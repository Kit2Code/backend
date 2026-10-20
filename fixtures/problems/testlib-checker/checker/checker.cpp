// testlib checker: checker <input> <contestant output> <answer>
// testlib.h is provided by the judge.
#include "testlib.h"
#include <vector>

int main(int argc, char *argv[]) {
    registerTestlibCmd(argc, argv);
    int n = inf.readInt();
    std::vector<bool> seen(n + 1, false);
    for (int i = 0; i < n; i++) {
        int x = ouf.readInt(1, n, "x");
        if (seen[x]) quitf(_wa, "%d appears twice", x);
        seen[x] = true;
    }
    quitf(_ok, "a permutation of 1..%d", n);
}
