#include <iostream>
#include <vector>

int main() {
    std::vector<char> memory(std::size_t{512} << 20, 1);
    std::cout << int(memory.back()) << '\n';
}
