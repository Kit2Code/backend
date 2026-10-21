import sys

line = "7" * (1 << 20)
while True:
    sys.stdout.write(line)
