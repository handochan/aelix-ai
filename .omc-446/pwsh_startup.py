"""#446 micro-probe (measure branch only): what one pwsh root costs on this runner.

Times, N each, interleaved: the product's own launch line (resolved shell,
``-Command``, the #239 preamble) running ``exit 0``; the same with
``-NoProfile``; and the product's line running the python root's import
(``& python -c "from aelix_ai.utils._process_tree import containment_spawn_kwargs"``).
Wall seconds per spawn, measured from Popen to wait() return.
"""

from __future__ import annotations

import statistics
import subprocess
import sys
import time

from aelix_ai.utils._shell import utf8_output_preamble
from aelix_coding_agent.tools.bash import _resolve_shell
from aelix_coding_agent.util.shell_env import get_shell_env

N = int(sys.argv[1]) if len(sys.argv) > 1 else 20
env = get_shell_env()
shell = _resolve_shell(env)
pre = utf8_output_preamble(shell.path)
imp = f'& "{sys.executable}" -c "from aelix_ai.utils._process_tree import containment_spawn_kwargs"'
arms = {
    "product-exit0": [shell.path, shell.command_flag, pre + "exit 0"],
    "noprofile-exit0": [shell.path, "-NoProfile", shell.command_flag, pre + "exit 0"],
    "product-pyimport": [shell.path, shell.command_flag, pre + imp],
    "bare-pyimport": [sys.executable, "-c", "from aelix_ai.utils._process_tree import containment_spawn_kwargs"],
}
print("shell", shell.path, shell.command_flag)
res: dict[str, list[float]] = {k: [] for k in arms}
for _ in range(N):
    for name, argv in arms.items():
        t0 = time.perf_counter()
        subprocess.run(argv, env=env, stdin=subprocess.DEVNULL, capture_output=True, check=False)
        res[name].append(time.perf_counter() - t0)
for name, xs in res.items():
    xs.sort()
    print(
        f"{name}: n={len(xs)} min={xs[0]:.3f} med={statistics.median(xs):.3f} "
        f"p90={xs[int(len(xs) * 0.9) - 1]:.3f} max={xs[-1]:.3f}"
    )
