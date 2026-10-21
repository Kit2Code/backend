"""Fixed limits for the steps that aren't the contestant's run (SPEC: "Limits per step")."""

from grader_worker.isolate import Limits

# Compiling a submission or a checker: 10 s CPU, 20 s wall, 512 MB, 32 processes, 64 MB files.
COMPILE = Limits(
    cpu_time=10, wall_time=20, extra_time=0, memory_kb=512 * 1024, processes=32,
    fsize_kb=64 * 1024, stack_kb=64 * 1024,
)  # fmt: skip

# Running a checker: 10 s CPU, 20 s wall, 512 MB, 1 process, 16 MB files.
CHECKER = Limits(
    cpu_time=10, wall_time=20, extra_time=0, memory_kb=512 * 1024, processes=1,
    fsize_kb=16 * 1024, stack_kb=64 * 1024,
)  # fmt: skip

# A contestant's output file may grow to 64 MB.
OUTPUT_FSIZE_KB = 64 * 1024
