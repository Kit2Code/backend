"""Tries to start a thread, fork, and run another program. The box allows one process (threads
count too), so all three must fail. Prints CONTAINED only if they all did."""

import os
import subprocess
import threading

escaped = False
try:
    thread = threading.Thread(target=lambda: None)
    thread.start()
    thread.join()
    print("ESCAPED thread")
    escaped = True
except RuntimeError:
    pass
try:
    child = os.fork()
    if child == 0:
        os._exit(0)
    os.waitpid(child, 0)
    print("ESCAPED fork")
    escaped = True
except OSError:
    pass
try:
    subprocess.run(["/bin/true"], check=False)
    print("ESCAPED spawn")
    escaped = True
except OSError:
    pass
if not escaped:
    print("CONTAINED")
