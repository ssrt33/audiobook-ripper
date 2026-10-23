"""Demo drive helper: copies a WAV into place a chunk at a time, like a real drive ripping.

python -m ripper.demo_writer SRC DST STUCK   (STUCK=1 stops halfway and hangs, like a scratch)
"""
import os
import sys
import time

src, dst, stuck = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
data = open(src, "rb").read()
os.remove(src)
step = max(len(data) // 8, 1)
with open(dst, "wb") as f:
    for pos in range(0, len(data), step):
        if stuck and pos >= len(data) // 2:
            time.sleep(3600)
        f.write(data[pos:pos + step])
        f.flush()
        time.sleep(0.25)
