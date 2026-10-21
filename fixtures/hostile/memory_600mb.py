"""Allocates 600 MB (every byte written) under a 256 MB limit."""

data = b"x" * (600 << 20)
print(len(data))
