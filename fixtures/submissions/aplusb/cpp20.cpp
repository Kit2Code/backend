// Uses C++20 features, so it compiles only as C++20.
#include <iostream>
#include <span>
#include <array>

int main() {
    std::array<long long, 2> v{};
    std::cin >> v[0] >> v[1];
    std::span<long long> s(v);
    long long total = 0;
    for (long long x : s) total += x;
    std::cout << total << "\n";
}
