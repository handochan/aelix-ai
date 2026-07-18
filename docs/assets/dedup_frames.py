"""Collapse idle spans in a VHS frame dump.

Consecutive frames whose frame-text PNG is byte-identical are idle (the cursor
lives in a separate overlay stream). Runs longer than THRESHOLD keep only the
first KEEP frames; everything else survives. Kept frame pairs are symlinked
into seq/ with fresh contiguous numbering for ffmpeg.
"""

import hashlib
import os
import sys

SRC = sys.argv[1] if len(sys.argv) > 1 else "demoframes"
DST = sys.argv[2] if len(sys.argv) > 2 else "seq"
THRESHOLD = 12   # runs longer than this (0.8s @15fps) get collapsed
KEEP = 8         # frames retained from each collapsed run

texts = sorted(f for f in os.listdir(SRC) if f.startswith("frame-text-"))
hashes = []
for f in texts:
    with open(os.path.join(SRC, f), "rb") as fh:
        hashes.append(hashlib.md5(fh.read()).hexdigest())

keep_idx = []
i = 0
while i < len(texts):
    j = i
    while j < len(texts) and hashes[j] == hashes[i]:
        j += 1
    run = j - i
    if run > THRESHOLD:
        keep_idx.extend(range(i, i + KEEP))
    else:
        keep_idx.extend(range(i, j))
    i = j

os.makedirs(DST, exist_ok=True)
for f in os.listdir(DST):
    os.unlink(os.path.join(DST, f))
for n, idx in enumerate(keep_idx, start=1):
    src_t = texts[idx]
    src_c = src_t.replace("frame-text-", "frame-cursor-")
    os.symlink(os.path.abspath(os.path.join(SRC, src_t)), os.path.join(DST, f"frame-text-{n:05d}.png"))
    os.symlink(os.path.abspath(os.path.join(SRC, src_c)), os.path.join(DST, f"frame-cursor-{n:05d}.png"))

print(f"{len(texts)} frames -> {len(keep_idx)} kept "
      f"({(len(texts) - len(keep_idx)) / 15:.1f}s of idle removed)")
