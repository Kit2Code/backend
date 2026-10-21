/* Touches 480 MB (every page), keeps it for about a second of CPU time, then answers. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(void) {
    size_t size = (size_t)480 << 20;
    char *memory = malloc(size);
    if (!memory) return 1;
    memset(memory, 1, size);
    volatile unsigned long sum = 0;
    for (int round = 0; round < 6; round++)
        for (size_t i = 0; i < size; i += 4096) sum += memory[i] + round;
    for (volatile long spin = 0; spin < 400000000L; spin++) {}
    puts(sum ? "ok" : "ok");
    return 0;
}
