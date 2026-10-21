# Compaction

Status: Accepted

How `aelix` keeps a long conversation inside the model's context window: when
it replaces the older part with a summary on its own, the `compaction` settings
that decide when and how much, what happens when a compaction fails, and how
much each request asks the model to write. `/compact` does the same on demand
([inside the TUI](getting-started.md#inside-the-interactive-tui)). Retries,
picking a model and thinking levels are in
[providers-and-models.md](providers-and-models.md).

## Automatic compaction

When a conversation gets close to the model's context window, aelix replaces
its older part with a summary, as `/compact` does, after the turn that brought
it there. It does the same when a provider refuses a request as too long, and
then sends that turn again, once. The `compaction` block of `settings.json`
(`~/.aelix/agent/settings.json`; a trusted project's `.aelix/settings.json`
overrides it key by key) sets when and how much, with pi's keys and defaults:

| Key | Default | Meaning |
| --- | --- | --- |
| `compaction.enabled` | `true` | `false` turns both automatic compactions off; `/compact` still works. |
| `compaction.reserveTokens` | `16384` | Compact once the conversation is larger than `contextWindow - reserveTokens`. |
| `compaction.keepRecentTokens` | `20000` | How much of the latest conversation every compaction (`/compact` too) keeps as it is; the older part is summarized. |

```json
{ "compaction": { "reserveTokens": 60000, "keepRecentTokens": 10000 } }
```

compacts a conversation with a 200000-token model once it passes 140000
tokens, and keeps about its last 10000 tokens. The size is the last answer's
reported usage (after a failed answer, an estimate). The values are read each
time they are needed, so a `/reload` after editing the file applies them to the
next turn. The TUI, `-p`, `--mode json` and `--mode rpc` use them; a delegated
agent's run uses the global file's values, as for
[`retry`](providers-and-models.md#automatic-retry).

A token count must be a whole number from `0` to 2^53 - 1. Anything else - a
string such as `"20000"`, a negative or fractional number, `null` - is ignored
and the default applies, where pi stops with `Invalid compaction.reserveTokens
setting`. `0` is a value: `"keepRecentTokens": 0` keeps only the last message,
and `"reserveTokens": 0` compacts only once the conversation is larger than the
window. An `enabled` that is not `true` or `false`, or a `compaction` that is
not an object, is ignored too. When `keepRecentTokens` covers the whole
conversation there is nothing to summarize: `/compact` answers `Nothing to
compact` and the automatic compaction does nothing.

rpc `set_auto_compaction` sets `compaction.enabled` in the global
`settings.json`, as pi does, so it outlasts the session: later TUI, `-p` and
rpc runs follow it too, until something sets it back. `get_state`'s
`autoCompactionEnabled` reports the value in force. A project
`.aelix/settings.json` that sets `compaction.enabled` itself still wins over
it, in pi as here; the harness's own toggle is set as well, for a project whose
`"compaction"` is not an object. The file is written in the background after
the answer, as for
[`set_auto_retry`](providers-and-models.md#automatic-retry).

Until this release none of these keys was read: auto-compaction waited for
`contextWindow - 16384`, every compaction kept about 20000 tokens, so
`"keepRecentTokens": 20` still answered `Nothing to compact` in a short
session, `"enabled": false` stopped nothing, and `set_auto_compaction` lasted
only as long as its rpc session (#469). The `aelix-server` WebSocket bridge has
no settings file and keeps the defaults. Not read yet: pi's per-model
`compaction.modelOverrides`, and `branchSummary.*`. Until this release, too, a
session compacted automatically only once: after its first compaction every
later answer looked older than it, so neither the threshold nor a request
refused as too long compacted again (#565). The TUI's `/context` still
shows the default `16384` as its `autocompact buffer`.

When an automatic compaction fails - its summary request failed past its
retries (`retry.*`), the model refused it, or the auth for it failed - the turn
that led to it keeps its answer and the conversation stays as it was; the next
turn past the threshold tries again. The `compaction_end` event says why in
pi's words: `error_message` is `Auto-compaction failed: <reason>`, or `Context
overflow recovery failed: <reason>` after a request refused as too long. A
compaction an extension's `session_before_compact` cancelled, or one stopped by
aborting the prompt, ends `aborted: true` with no message. `-p` prints the
answer, exits 0 and writes the `error_message` line to stderr (pi writes
nothing there); `--mode json` and `--mode rpc` carry it on the event only. The
TUI draws it in red under the answer (`✖ Auto-compaction failed: …`, after the
`Retry failed` line of a failure that was retried) and a cancelled or aborted
one as `Auto-compaction cancelled`, as pi does. Until this release the failure ended the turn as an error: `-p`
printed the compaction's error instead of the answer and exited 1, and an
extension that cancels automatic compaction failed every turn past the
threshold (#546).

After a request refused as too long there is no answer to keep: when the
compaction cannot recover it - nothing to compact yet (the session's first
turn), the compaction failed or an extension cancelled it, or the prompt was
aborted during it - the failed answer stays the conversation's last message and
the request is not sent again, and no second compaction is tried in that prompt.
`-p` writes the provider's error to stderr (after the one `Context overflow
recovery failed: <reason>` line when the compaction failed) and exits 1, `--mode json` exits 1, and rpc `get_messages` ends on that
answer. Until this release it was dropped there, though the session file kept
it: `-p` and `--mode json` printed nothing and exited 0, and rpc
`get_last_assistant_text` answered the previous turn's text (#391). pi drops it
too.

## How much a request asks the model to write

A model's `maxTokens` (the catalog's, or yours in `models.json`) is the most
one answer asks for. Each request asks for no more than the context window
still holds: `contextWindow`, less the conversation so far, less 4096 tokens,
and at least 1 - pi's rule. The conversation is counted as the last answer's
reported usage plus an estimate of what came after it (3.5 characters a token,
as pi counts; an image counts as 1372 tokens); after a compaction, until a new answer reports usage, the whole
conversation is estimated. A compaction's or a branch summary's request asks
for what a turn of the same model asks for, cut the same way: until this release
it set a limit of its own (13107 tokens for the summary, 8192 for a split turn's
prefix, 2048 for a `/tree` summary), which a model that reasons spends on its
reasoning too (#506). A summary that stops at the limit (`stop_reason`
`length`) is refused - `Summarization failed: generation hit the token cap and
the summary is incomplete` - so a cut summary never replaces the conversation,
and the compaction leaves the session as it was.

Some endpoints refuse a request whose conversation plus its `maxTokens` is
larger than the window (MiniMax's Anthropic-compatible API, vLLM, many
OpenAI-compatible servers). Until this release aelix sent the whole cap there,
so once a conversation passed `contextWindow - maxTokens` every turn failed -
before auto-compaction, which waits for `contextWindow - 16384` by default
([automatic compaction](#automatic-compaction)), was reached (#563). Near a
full window an answer can now stop at the smaller limit (`stop_reason`
`length`) instead; the compaction after that turn makes room.

Per adapter: Anthropic's Messages API always sends a cap, so the catalog's is
cut, and with a thinking budget the budget fits inside it (under 2048 tokens of
room, budget thinking is off for that request). openai-completions sends its
own cap only while it fits and otherwise leaves it out, so the provider uses
whatever room is left. OpenAI Responses and Gemini send a cap only when the
request sets one (none of aelix's own requests does; Responses never sends less
than 16, which the API refuses), and the Codex adapter sends none. A prompt that alone
overflows the window still goes out, and the provider's error starts a
compaction. The errors endpoints give when the conversation plus the cap is too
large - Anthropic's ``input length and `max_tokens` exceed context limit``,
Hugging Face TGI's `` `inputs` tokens + `max_new_tokens` must be <= `` - count
as a context overflow, like `prompt is too long`.
