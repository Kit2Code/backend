#include <cstdio>
#include <string>

int main() {
    const std::string line(1 << 20, '7');
    for (;;) std::fwrite(line.data(), 1, line.size(), stdout);
}
