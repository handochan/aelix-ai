# 0317. RPC output is written by one thread, and a host that stops reading holds the turn, not the loop

Status: Accepted (2026-11-22, #852).
Date: 2026-11-22
Issue: [#852](https://github.com/handochan/aelix-ai/issues/852) (a host that stops reading stdout
stalls the native's whole event loop). Refs #612, #57.
Related decisions: ADR-0307 (K8, which this replaces), ADR-0180 (exit 141 when the stdout consumer
is gone), ADR-0302 (control-v1, unchanged), ADR-0238 (the RPC mode's signals), ADR-0235 (pi as the
reference).

Every choice under "Decision" is the lead's call (the briefs for #852 and its fix rounds 1 and 3,
2026-11-22), except where a line says it is the implementer's. The owner's instruction for the
batch was only to do every issue on the list ("모두 다 진행하세요", 2026-11-22). "The previous
head" below is this change before its fix round 1, which an independent verify and a Codex review
measured; "the round 2 head" is this change after that round, which a second verify measured
before fix round 3.

## Context

`aelix --mode rpc` wrote each frame (a reply, an agent event, `aelix_child`, `aelix_notice`,
`aelix_subagent`, `extension_ui_request`) with `buffer.write` and `buffer.flush` on the real stdout,
on the event loop's thread. ADR-0307 K8 recorded, by reading the code, that a host that stops
reading would stall the loop. #852 measured it: a real `aelix --mode rpc --no-session -e hb.py`,
whose extension pings the loop every 100 ms from a thread, answered 100 `resources/list` requests
(about 4.6 kB each); with the host not reading for 3.0 s, one ping waited 2.969 s, and with the
host reading at once the worst was 0.025 s (macOS, Python 3.12). Everything on the loop waited:
the turn, the children's pumps, every handler, the signal handlers.

pi (`earendil-works/pi` `origin/main` of 2026-11-21, `packages/coding-agent/src/core/output-guard.ts`)
never writes on its loop either: `writeRawStdout` chains each write onto a promise tail and
returns; a write that fails `EAGAIN`, `EWOULDBLOCK` or `ENOBUFS` is tried again after 10 ms, and
any other error exits the process with status 1. Its RPC mode (`modes/rpc/rpc-mode.ts`) awaits
`waitForRawStdoutBackpressure()`, which resolves when the whole tail has been written, in three
places: an agent listener the agent awaits (line 336), after each command's reply or error reply
(lines 802 and 813), and after a parse error's reply (line 771). The last two are inside each
line's own handler, which the line reader does not await, so pi keeps reading its input. At
shutdown it waits for the tail without a bound, except after a SIGTERM.

## Decision

1. **One writer thread for the real stdout.** The loop encodes a frame and enqueues it
   (`rpc/_stdout_writer.py`, `ThreadedFrameWriter.write`, callable from any thread); one daemon
   thread, started by the first frame, writes the frames in the order they were enqueued, each
   whole before the next, and continues every short write. A sink that the caller hands in
   (`run_rpc_mode(stdout_write=...)`: the WebSocket host and the tests) is still called inline on
   the producer's thread (`InlineFrameWriter`), unchanged.
   - *The implementer's call:* the thread writes with `os.write` on the descriptor, a pipe or a
     terminal, never through `sys.__stdout__.buffer`. A daemon thread blocked inside the buffered
     writer holds the buffer's lock, and the interpreter's exit then waits on that lock: a probe
     whose daemon thread was blocked that way on a full pipe did not exit within 15 s on Python
     3.12, and exited in 0.57 s with `os.write` (macOS); with a terminal nobody read, the previous
     head (which wrote a terminal through the buffer) ended with CPython's
     `_enter_buffered_busy` fatal error and status -6
     (`test_a_terminal_that_is_not_read_does_not_hold_the_exit`). #202 met the same lock on the
     stdin side. A Windows console is written through its raw console object, which takes UTF-8,
     may take only part of a frame and holds no lock; a stream with no descriptor (pytest's
     capture) keeps the base's buffered path.
2. **Back-pressure as pi's, with a mark, and the input held.** The bytes queued and not yet
   written are counted. While they are above 1 MiB (`_HIGH_WATER_BYTES`):
   - the agent-event listener waits: an async harness listener (`ThreadedFrameWriter.follow`),
     which the harness awaits, so the turn waits; it moves to the new harness at every session
     change and ends at the exit;
   - each command's reply waits: the dispatch task awaits the writer after enqueuing the reply;
   - **the host's input is held** (`InputGate`): its lines are still read, and not parsed, so
     no new command runs and no new reply or parse error is made. One held line goes through
     after each drain to the mark, even when a turn has filled the queue past it again, so the
     turn cannot starve the input; that line waits for the command let in before it, as below.
     The input goes on being read while less than 1 MiB of lines is held, so an EOF behind them
     is seen, and at the EOF, or a failed read, every held line is parsed at once. pi does not
     hold its input; this is the lead's call of fix round 1 (Codex's queue probe below).

   At or under the mark a line is parsed when the queue and the replies still owed leave room
   for it. A reply is enqueued by its command's task, some loop turns after its line, so each
   command let in counts as 16 KiB of the room (`_REPLY_RESERVE_BYTES`) until its reply is
   enqueued, its task ends, or 50 ms have passed since its line (`_SETTLE_SECONDS`); one command
   may always be owed. With an empty queue 64 commands may owe their replies, and within 16 KiB
   of the mark one, so there each line's reply counts before the next line is judged. A line
   waits only when that many commands are unanswered, and then at most until the oldest one's
   50 ms are up (a `bash` or a `compact` answers when it is done, and an `abort_bash` behind it
   has to get in). This is fix round 3: the lead asked for the room to be judged per line against
   what is queued and what the commands already started will still enqueue, and for a host that
   reads to pay no wait; the rule and its 16 KiB are the implementer's, and so is what follows
   from them, that with an empty queue a line still waits once 64 commands are unanswered. The
   round 2 head judged a whole chunk of lines (at most 4,096 bytes) at once and then waited up
   to 50 ms for the commands it had started, whatever was queued: at the first drain after a
   stall it parsed every held line in one pass (the round 2 verify: 12,520,050 bytes queued from
   20,000 `get_state` lines, 0.54 s after the host read again), and with a host that read at
   once a `get_state` written 5 ms after a `bash` `sleep 0.6` was answered after 44.6 to 47.4 ms
   (0.4 to 1.9 ms at the base).

   Every other producer is a synchronous callback and enqueues regardless: `aelix_child`,
   `aelix_notice`, `aelix_subagent`, an extension's `extension_ui_request`. Nothing is dropped
   while the mode runs. A non-blocking stdout that is full (`EAGAIN`, `EWOULDBLOCK`, `ENOBUFS`,
   on Windows also `ENOSPC`, or a write that takes no byte) is tried again after 10 ms, as pi
   does; `EINTR` is tried again at once. Windows' C runtime reports a full non-blocking pipe as
   `ENOSPC`: CPython 3.12's `_Py_write` turns it into `EAGAIN` and 3.11's does not (CPython's
   `Python/fileutils.c` at v3.12.13 and v3.11.15, by reading), so on 3.11 this mapping is what
   retries it. On POSIX `ENOSPC` is a full disk and ends the stream (limit 4).
3. **A closed or broken stdout ends the mode, and the process exits 141 (#57, ADR-0180).** Any
   other failed write ends the stream: a closed pipe (`BrokenPipeError`, and on Windows `EINVAL`,
   which is how a write to a pipe with no reader fails there), or a stream broken mid-frame, where
   writing the next frame would append it to a partial one. The queue is dropped, every waiting
   producer is released, the shutdown starts, and after its teardown `run_rpc_mode` raises
   `BrokenPipeError`, which `main_sync` maps to 141, as the base's last flush of its buffered
   stdout did. A closed pipe is quiet; any other error writes one line on stderr.
4. **The exit's drain waits at most 5 s.** When the shutdown starts (EOF, a signal, a closed
   pipe) and again at the top of the teardown (a cancelled `run_rpc_mode`, as Ctrl+C under
   `asyncio.run` is), the waiting producers are released and no later wait waits. After the
   disposal, whose `session_shutdown` writes the children's terminal frames, the exit waits at
   most 5 s (`_DRAIN_SECONDS`, the lead's call) for the queue to reach the host; from then on no
   new frame is taken. Past the bound one stderr line says how many frames and how many of their
   bytes did not reach the host; it is written by a daemon thread with `os.write`, waited for at
   most 0.2 s, so a full stderr does not hold the exit either. The writer is closed even when the
   controls' `retire()` raises.

## What a host that stops reading costs now

- **The loop:** no stall the driver can see (it logs to the millisecond). The issue's driver, run
  before and after on one machine in turns (macOS, Python 3.12, other sessions' suites running),
  with the pings counted from the requests on: with the host not reading for 3.0 s the worst
  ping was 2.937 s and 2.909 s before, and 0.000 s (39 pings, none over 0.5 s) at the previous
  head, in two runs each; all 100 replies arrived in order in every run. On this head the
  verify's driver measured 0.002 s and 0.001 s in two `stall` runs, 100 replies in order and no
  unparseable line. In-process, `test_a_host_that_stops_reading_for_3_s_does_not_stall_the_loop`
  (400 replies, about 260 kB) holds a 20 ms sleep under 0.5 s late; at the base it ran 2.914 s
  late.
- **The turn:** it waits while the host is more than 1 MiB behind.
  `test_a_turn_is_held_at_the_mark_and_every_frame_arrives_whole_in_order` streams 80
  `message_update` frames of about 20.5 kB to a host that stopped reading: the turn stops before
  the 80th, and once the host reads, the frames it receives are exactly the frames enqueued, in
  that order.
- **The queue:** at the mark for the host's commands that answer at once, while the host does
  not read and when it reads again. Codex's probe (an in-process mode over a pipe its host
  stopped reading, then 6,000 `get_state` lines and 1,000 malformed ones at once) left 3,975,960
  bytes in 6,515 frames at the previous head; the round 2 head held it about 55 kB over the mark
  while the host did not read and reached 3,708,712 and 3,713,350 bytes just after it read. On
  this head, with the queue read at every enqueue (the round 2 probes sampled it every 2 ms), the
  peak was 588 and 589 bytes over the mark while the host did not read and under the mark after
  it read, in two runs on macOS, and 415 and 413 bytes over, then 593 and 415, in two runs in a
  Linux container; all 6,000 replies arrived in order and all 1,000 parse errors arrived. With
  20,000 `get_state` lines at once (749 kB of input, about 13 MB of replies) the peak was 591
  bytes over the mark during the stall and under the mark after it on macOS, 413 and then 646 in
  Linux; with a host that then read 4 KiB every 5 ms, 651 bytes over on macOS and 652 in Linux,
  through the 21 s and 20 s the replies took to arrive (12,778,100 bytes at the round 2 head). The
  round 2 verify's own two probes, which sample every 2 ms, read at most 1,049,228 bytes on
  macOS and in Linux, in the stall and after it. `test_input_is_not_parsed_while_the_host_is_behind`
  holds the peak under the mark plus 32 KiB at every enqueue, through the stall and the resume
  (at the round 2 head 1,089,551 bytes in the stall and 3,633,296 after it). Commands that take
  longer than 50 ms, and replies larger than 16 KiB, are limit 10; the held lines parsed at once
  when the host closes stdin while behind are limit 2; the producers that cannot wait are limit 1.
- **A host that reads:** no wait. With the round 2 verify's `latency.py` (a real `aelix --mode
  rpc`, the host reading at once, macOS), a `get_state` written 5 ms after a `bash` `sleep 0.6`
  was answered after 0.5 to 4.2 ms in ten runs on this head and after 0.5 to 1.0 ms at the base
  in the same session (44.6 to 47.4 ms at the round 2 head); a `get_state` alone took 0.3 to 0.6
  ms on both, and one in the same write as the `bash` 4.1 to 5.3 ms against the base's 3.8 to
  4.8. A line waits only once 64 commands are unanswered with an empty queue: in a gate-level
  probe the line after 1 and after 63 running commands was parsed 0.1 ms later, and after 70,
  44.6 ms later (macOS; 0.0, 0.0 and 43.0 ms in Linux)
  (`test_a_host_that_reads_pays_no_wait_after_a_slow_command`,
  `test_with_an_empty_queue_64_commands_may_owe_a_reply_and_the_65th_line_waits`).
- **The exit:** the drain waits at most 5 s; the process exits a little later. With the verify's
  driver (a real `aelix --mode rpc --no-session`, 100 `resources/list` requests, a host that never
  read), the exit came 5.14 and 5.16 s after stdin closed, 5.18 s after a SIGTERM and 5.16 and
  5.19 s after a SIGINT on this head (5.10 to 5.16 s at the round 2 head, 5.15 to 5.41 s at the
  previous head; macOS). At the round 2 head, whose writer this round did not change, Codex's
  probe children (a 400-request mode over a pipe never read) exited 5.126 s after stdin closed,
  5.296 s with stderr full too (still alive at 6.603 s at the previous head) and 5.701 s with
  stdout a terminal nobody read, status 0 (status -6 after 6.6 s at the previous head). At the base Codex's pipe child was still
  alive 6.606 s after stdin closed, and a real `--mode rpc` had not exited 20 s after. `test_a_real_process_exits_past_a_host_that_never_reads` (a 1 s bound and 2,000 replies,
  about 1.3 MB) and `test_a_full_stderr_does_not_hold_the_exit` hold it; at the base the first did
  not exit within 90 s, and at the previous head the second did not exit within 30 s.
- **A closed pipe:** a real `aelix --mode rpc` whose host closed stdout exited 141, 0.12 s after
  the close in each of two runs of the verify's driver on this head (141 at the base, 0 at the
  previous head; `test_a_real_aelix_rpc_exits_141_when_its_host_closes_stdout`).

## Known limits

1. **Producers that cannot wait have no bound on their number.** `aelix_child`, `aelix_notice`,
   `aelix_subagent` and an extension's `extension_ui_request` enqueue while the host is behind.
   Each frame is bounded (ADR-0307's guard holds an `aelix_child` frame at ≤ 5 KiB), and their
   count grows with the children's activity and the dialogs and notices that happen while the
   host does not read; the held input bounds only what the host's own commands make. Not
   measured.
2. **The input waits with the output.** A command the host writes while the queue is above the
   mark is read and held; it is parsed at the next drain to the mark (one line per drain, once the
   command let in before it has answered or its 50 ms are up) or when the host reads the queue
   down. That includes `abort`, `extension_ui_response` and a session change. The base read
   nothing while its loop was blocked in a write, so this is no worse than the base; the previous
   head parsed every line at once.
   - *An abort of a held turn* (the lead asked for the fix or a limit; this is the limit). In
     Codex's abort probe run four times on this head (a turn held at the mark, one `abort`, the
     host not reading): three times the abort was not parsed in the 8 s the probe waits; once it
     got in on a drain the probe did not control (the host's last read, or a macOS pipe growing
     its buffer), and the turn was idle when the probe looked, 0.31 s after it wrote the abort.
     In an earlier run of this round it got in
     the same way and was answered while the turn stayed in `turn`, its events waiting at the
     mark, until the host read (as the first verify measured at the previous head, which parsed
     every abort at once: 6.0 s). With a host that reads 4 KiB every 5 ms, so that the turn sits
     at the mark, the abort's reply was enqueued 0.035 s and 0.024 s after the abort was written,
     in two runs (0.006 and 0.024 s at the round 2 head).
   - *A session change with a held turn.* In Codex's session-switch probe run four times on this
     head, `switch_session` was not parsed in the 13 s the host did not read. A session change
     that gets in on a drain while the host is still behind aborts a turn whose terminal events
     wait at the mark until `abort_until_idle`'s 2 s re-abort cuts each wait: in three runs at an
     earlier checkpoint of this round it was not parsed once, got in that way once (answered
     after 4.007 s, every frame in order) and was answered after 0.006 s once (1,046,128 bytes
     queued, under the mark); the round 2 head, in the same session, gave 0.006 s once and was
     not parsed twice.
   - *Order.* Codex's order probe (a turn of 80 deltas of 20 kB held at the mark; `get_state` at
     0.35 s; a malformed line at 0.65 s; the host reads from 1.1 s): the base wrote `agent_end`,
     then the parse error, then the `get_state` reply. In four runs on this head both came
     before `agent_end`, and either may come first (the round 3 verify's eight runs: the parse
     error first in six, the reply first in two). The difference from the base is that held lines get in before the turn ends. Between
     the two, a parse error is written as its line is parsed and a reply when its command's task
     runs: with room for both lines they are parsed in one pass, as at the base, and the error
     comes first; near the mark the second line waits for the first one's reply. With a host that
     kept reading, the order was the base's in three runs (`agent_end`, the reply, the error).
   - *The held input itself.* At most 1 MiB of lines is held; then the input is not read and the
     host's writes wait in its pipe (`test_input_stops_being_read_once_a_mark_of_it_waits`). On
     Windows, where a thread reads stdin for the loop, that thread now stops too: it reads its
     next chunk only once the loop has fed the one before to its reader, and asyncio pauses one that
     holds more than 128 KiB unread, so at most 192 KiB waits between the thread and the held
     lines (`tests/rpc/test_rpc_stdin_win32.py::test_thread_pump_stops_reading_while_nobody_takes_its_input`,
     which CI's Windows legs run; before, the thread read on without a bound). With the Windows
     arm run on macOS and in Linux, the output held and a host writing 6 MiB of input for 3 s,
     1,245,184 and 1,155,072 bytes were taken and 172,032 and 139,264 of them sat in the reader
     (6,291,456 and 5,283,840 at the round 2 head; the POSIX arm: 1,245,184 and 172,032). Not run
     on Windows. At an EOF every held line is parsed at once, so the replies of up to 1 MiB of
     commands can pass the mark before the exit's drain. A host with more than 1 MiB of commands
     held that never reads and closes stdin is not seen to close it, and the process exits on a
     signal only, as at the base. Not measured.
3. **What the exit gives up.** Past the 5 s bound the frames still queued are lost, and the frame
   being written may end without its newline. The stderr line counts the frames that did not
   reach the host whole and their bytes, and a write that has not returned counts in full: on a
   pipe that blocks, the kernel has taken part of that frame and `os.write` does not return, so
   the writer cannot know how much (it cannot be computed there; fix round 3 reworded the claim
   instead). In the verify's `never` run on this head the line said 87 frames and 408,204 bytes,
   and the host found 13 whole lines and 4,550 bytes of the 14th: 403,654 bytes had not reached
   it. A probe with 5,000-byte frames over-counted by 536 bytes on macOS and 4,096 in Linux, the
   part of the cut frame in the pipe. Only a short write that returned is left out
   (`test_the_count_leaves_out_the_bytes_that_reached_the_host`). The writer thread writes
   nothing after that but the rest of the `os.write` it is in, and the interpreter exits from
   under it.
4. **A broken stream ends the mode.** At the previous head and the base, a write error other than
   a closed pipe lost that frame and the mode went on; Codex showed that after a partial write the
   next frame was glued to the cut one (`{"id"{"id":"good"}`). Now the mode ends as for a closed
   pipe and exits 141 (`test_a_broken_stream_is_never_written_again`). On POSIX `ENOSPC` (a full
   disk under a redirected stdout) is such an error.
5. **The injected path is unchanged.** The WebSocket host (`aelix-server`'s `rpc_ws.py`) hands
   `run_rpc_mode` a synchronous sink that puts each frame on an unbounded `asyncio.Queue`, drained
   by an async sender. It never wrote on the loop, so it never stalled the loop; it has no mark and
   no held input, and a WebSocket client that stops reading grows that queue (by reading the code;
   not measured).
6. **Output outside the writer.** On the base and on this change alike, an extension's `print`
   before the redirect, a logger bound to stdout before RPC mode, a write to `sys.__stdout__` and
   an `os.write(1, …)` reach stdout without the writer, and a direct descriptor write can land
   inside a frame: in Codex's probe it landed at byte 160 of a large reply and made two lines
   unparseable. Not changed here.
7. **A second cancel before the disposal.** A cancel that lands in the teardown before the
   disposal (at the client MCP close) skips the drain; the thread still writes what is queued,
   then waits idle until the interpreter exits. By reading; not measured.
8. **Platforms.** The live runs (a real `aelix --mode rpc`) were on macOS only; the in-process
   probes of the queue, the count and the stdin arms also ran in a Linux container. The tests
   carry no platform mark except two that need POSIX (a full stderr set up with
   `os.set_blocking`, and a pseudo-terminal), so CI's Windows legs run the rest, which this lane
   did not. On Windows the console path (a buffered stream's raw console, and the `-u` shape
   where the buffer is the console), the `EINVAL` and `ENOSPC` mappings and the raw console's
   short writes are held by tests that set `sys.platform` and fake the console; the stdin
   thread's pause is held by a test of the Windows arm that runs on every platform; a real
   Windows pipe whose reader closed is held only by `test_a_closed_pipe_still_ends_the_mode` on
   those legs.
9. **Print and json modes** write their stdout as before; a reader that stops reading them still
   blocks them (out of #852's scope).
10. **Commands slower than the input's wait, and replies larger than its reserve.** The input
    counts a command for 50 ms and keeps 16 KiB of room for its reply. A command that answers
    later is not waited for, and a larger reply takes more room than was kept, so such commands
    can carry the queue past the mark. 200 `bash` lines (each `sleep 0.4`, then 30,000 bytes of
    output: a 30,148-byte reply) written at once to a mode whose host did not read left
    3,738,496 bytes queued on macOS, where 127 of the 200 were started before the host read, and
    6,029,890 in a Linux container, where all 200 were; the round 2 head left 5,939,446 on macOS.
    64 went in within 50 ms and the others as the time of those before them ran out. The wait is
    bounded so that a command written behind a slow one (an `abort_bash`) still gets in; pi and
    the base start every command at once.

## pi comparison

| | pi (`origin/main`) | aelix (this ADR) |
|---|---|---|
| Where a frame is written | a promise tail, off the caller | one writer thread |
| A full stdout | `EAGAIN`, `EWOULDBLOCK`, `ENOBUFS` retried after 10 ms | the same, Windows' `ENOSPC` and a write that takes nothing too; `EINTR` at once |
| Any other write error | the process exits 1 | the mode ends, exit 141 |
| Who waits | an awaited agent listener; each command's reply; a parse error's reply, the last two in their line's own handler | the agent listener; each command's reply; the input: held above the mark, and under it a line waits while the replies owed fill the room |
| Until | the whole tail is written | the queue is at or under 1 MiB |
| Input while behind | read and handled | read and held until 1 MiB is held; one line parsed per drain |
| At exit | waits for the tail, no bound (not after SIGTERM) | the drain waits at most 5 s, after any ending |
| Queue bound | none | the mark and one reply for the host's commands that answer within 50 ms (Codex's probe and 20,000 `get_state` lines: under 1 kB over, in the stall and after it); slower commands and replies over 16 KiB are limit 10; the held lines at an EOF while behind are limit 2; none for the producers that cannot wait (limit 1) |

## How it is held

`tests/rpc/test_rpc_stdout_writer_852.py` (62 cases) and, for the Windows stdin thread, one case
of `tests/rpc/test_rpc_stdin_win32.py`. At the base,
`test_a_host_that_stops_reading_for_3_s_does_not_stall_the_loop` fails (2.914 s late),
`test_a_turn_is_held_at_the_mark_and_every_frame_arrives_whole_in_order` fails (the loop 1.748 s
late) and `test_a_real_process_exits_past_a_host_that_never_reads` fails (no exit in 90 s);
`test_a_closed_pipe_still_ends_the_mode` passed there too until fix round 1 made it expect the
`BrokenPipeError` that gives 141. On the previous head 33 of the 50 cases the file then had
failed: the queue at 4,027,296 bytes, a held line parsed, exit 0 instead of 141 and no
`BrokenPipeError`, the frames lost to `EINTR` and `ENOBUFS`, 791,893 zero-byte writes in 100 ms,
the console's frame cut at 16,383 bytes, a late write taken, the threads of refused starts left,
a full stderr holding the exit past 30 s, the -6 exit on a terminal, and the rest on names the
previous head did not have. At the round 2 head 18 of the two files' 69 cases fail: the queue's
peaks (1,089,551 bytes in the stall and 3,633,296 after it), the stdin thread taking 6,291,456
bytes that nobody read, and the others on names that head did not have (the room, the replies
owed and their 50 ms, the line a drain lets in behind an unanswered command, an EOF behind a
waiting line, an injected sink's lines, `run_rpc_mode` telling the input of each command).

Mutants. Fix round 1 and the round 2 verify mutated the writer's lines; the verify killed 63 of
its 75 and judged ten of the twelve survivors equivalent and two not. Those two are held now: a buffered Windows console written
through its buffer, by the `buffered` case of
`test_a_windows_console_is_written_through_its_raw_console_and_short_writes_continue`, and an
injected sink's lines made to wait, by `test_an_injected_sink_parses_every_line_as_it_is_read`.
Fix round 3 ran 47 mutants of the lines it changed against the two files, and all 47 are killed:
nine of the room (its boundary at the mark, the floor of one, the 16 KiB, the 50 ms, the injected
sink's), eight of what a command counts for (its time, its end, its reply, the wake), nineteen
of the pump (the line a drain lets in, the count against the room, the read while a line waits,
the oldest command's time and only its, the parse at an EOF or a failed read, the wait cancelled),
`run_rpc_mode`'s two calls, eight of the stdin thread's flow control and the console line. Three
of them survived the first version of these tests and are held by cases added for them (a wake
by more input taking a command out, a timeout clearing every command, a wait left behind at each
read). The earlier rounds' mutants of lines this round did not change were not run again.
