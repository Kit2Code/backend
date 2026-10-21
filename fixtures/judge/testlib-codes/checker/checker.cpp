// testlib checker for judge tests: the contestant's token chooses the testlib result.
#include "testlib.h"
#include <string>

int main(int argc, char *argv[]) {
    registerTestlibCmd(argc, argv);
    std::string token = ouf.readToken();
    if (token == "ok") quitf(_ok, "fine");
    if (token == "pe") quitf(_pe, "presentation");
    if (token == "fail") quitf(_fail, "the checker gave up");
    if (token == "points") quitp(0.5, "half the points");
    if (token == "dirt") quitf(_dirt, "extra data");
    quitf(_wa, "expected ok, got %s", token.c_str());
}
