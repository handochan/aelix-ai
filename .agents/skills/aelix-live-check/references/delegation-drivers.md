# Live drivers for delegation and the extension exec surface

A live check of a kill path, a delegated child or `ExtensionAPI.exec` needs a real
model and real processes. These rules come from drivers that misfired or did
damage. Read them before you write a new one.

1. **Never point a yolo child at a checkout.** Give the run a scratch cwd. A child
   with write and bash tools, looking for a tool it could not find, once wrote an
   extension file into the repository (#220). Give the driver its credential
   through its own environment, by name. `build_child_env` (in
   `aelix_agents/print_channel.py`) forwards the parent's environment to the child.
2. **A delegated child cannot call an extension tool.** `narrow_tools` removes
   extension tool names from a delegated child's grant by design. To start a
   grandchild process from a child, do it from the extension's `setup()` on a worker
   thread. In a top-level `aelix -p` run, which is not a delegation, extension tools
   are callable. Load one with `-e <scratch>/probe.py`, and call `api.exec(...)`
   from the tool's `execute()`: `setup()` runs at load time, before the runtime is
   active.
3. **Run every shape inside one tool call, one after another.** Asked to call a tool
   twice with different arguments, a small model issued both calls in parallel. The
   first call's survivor count then included the second call's children. One tool
   with no arguments that runs each shape in turn fixes this.
4. **Count survivors by an argv marker or a process group, not by `pgrep -f`.** The
   child aelix's own argv holds the task text, so a name match counts it. Use
   `ps -axo pid,ppid,pgid,stat,command`, filtered by a marker unique to the run or by
   the delegation tree's pgid. A `#` inside a `sh -c` string comments out the rest
   of that command line.
5. **Kill only what you started.** Record each pid or pgid your driver starts, and
   kill only those. Never kill by name pattern (`pkill -f`, `killall`): other
   sessions on the machine run the same programs.
6. **Measure each platform you claim.** The kill paths differ by platform, and the
   record has changed under earlier drivers. On macOS a delegated child's bash
   grandchild used to outlive a stop; since #691 (ADR-0291) the stop takes what its
   bash started. Linux also arms a parent-death signal (#721). Read the current
   ADRs before you write an expected outcome into a driver, and run the Linux leg
   in a container if you are on macOS.

For Windows-only behaviour (Job objects, the absence of `SIGKILL`), the windows-latest
CI leg is the only place to measure. Put the measurement in the assertion message
or a `warnings.warn`, so it shows in a `-q` log.
