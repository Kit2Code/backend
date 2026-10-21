int main(void) {
    volatile int *nowhere = 0;
    *nowhere = 1;
    return 0;
}
