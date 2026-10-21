int main() {
    for (volatile unsigned long spin = 0;; spin = spin + 1) {}
}
