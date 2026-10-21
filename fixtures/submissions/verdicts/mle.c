#include <stdio.h>
#include <stdlib.h>

int main(void) {
    size_t size = (size_t)512 << 20;
    /* volatile, so the compiler can't drop the allocation and the writes. */
    volatile char *memory = malloc(size);
    if (!memory) return 1;
    for (size_t i = 0; i < size; i += 4096) memory[i] = 1;
    printf("%d\n", memory[size - 4096]);
    return 0;
}
