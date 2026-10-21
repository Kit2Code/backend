/* Allocates 600 MB and writes every page, under a 256 MB limit. The pointer goes through a
 * volatile variable so the compiler can't drop the allocation. */
#include <stdio.h>
#include <stdlib.h>

int main(void) {
    size_t size = (size_t)600 << 20;
    char *volatile memory = malloc(size);
    if (!memory) return 1;
    unsigned long sum = 0;
    for (size_t i = 0; i < size; i += 4096) memory[i] = (char)i;
    for (size_t i = 0; i < size; i += 4096) sum += (unsigned char)memory[i];
    printf("%lu\n", sum);
    return 0;
}
