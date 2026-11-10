# Measuring what a provider actually sends and receives

Use this when a live check must say what went over the wire: a missing tool
argument, a reasoning level, which host a request reached, or how an upstream
behaved. Each recipe below was needed because a quicker measurement gave a
plausible but wrong answer.

## Tap the raw events, through the real CLI

Write a small wrapper that monkeypatches the adapter's event iterator (or its
request builder) to log each event or body, and then calls the real entry point,
`aelix_coding_agent.cli.entry.main_sync()`. Run it from a scratch cwd, with the
scratch HOME and agent dir from the skill. That way every layer except your tap is
production code: argument parsing, the model registry, the harness and the SDK.

- Decide when the run is finished by its last event in the log, not by a process
  listing. macOS `ps aux` truncates long argv lines, and once reported "all
  finished" while runs were still streaming. Use `ps -axww` if you must list.
- Script the invocation in bash or Python. In zsh, one variable holding several
  flags is passed as one argument: three runs once exited 0 with no output and never
  sent a request.

## Pin one upstream, vary one thing

OpenRouter spreads one model id over several upstreams, and they differ. To compare
two runs, fix the upstream first, then change one thing per run.

- The upstream that served a request is in each streamed chunk's `provider` field,
  and in `GET /api/v1/generation?id=<generation id>` as `provider_name`. aelix
  stores neither.
- To pin it, send OpenRouter's `provider` routing object with an `order` list and
  `allow_fallbacks: false`. In aelix, a `models.json` model entry's `compat`
  carries it as `openRouterRouting`. For openrouter.ai base URLs the completions
  adapter sends it as the request's `provider` field (this is from reading
  `providers/openai_completions.py` and `providers/_openai_compat.py`, not from a
  measurement). `docs/guides/models-json.md` documents `compat`.
- Vary content shape (language, newlines, length) on the pinned upstream first, then
  the tool schema's key order. A grammar-constrained upstream can emit keys only in
  the schema's declared order, so an argument declared before the one the model
  writes first is dropped.

## Prove where a request goes without letting a key leave

To show which host a request reaches (routing, a base-URL override, a provider
lookup), point every egress at local listeners:

1. Set `HTTPS_PROXY=http://127.0.0.1:<port>` to a listener that records each
   `CONNECT host:port` line and answers `403`. Every https call is then recorded by
   host and dies before TLS, so even a real key is never sent. Keep
   `NO_PROXY=127.0.0.1`.
2. Point plain-http base URLs (`OPENROUTER_BASE_URL`, `models.json` `baseUrl`) at a
   second local listener that records method, path and headers.
3. Filter out the update check's CONNECT, or pass `--offline`, which skips it.

## OAuth providers

A real OAuth call may refresh the token and rewrite `auth.json` in the agent dir.
With a scratch `AELIX_CODING_AGENT_DIR` that is harmless. If the check needs the
developer's real login, stamp a file first and run
`find ~/.aelix/agent -newer <stamp>` afterwards, and report any change.

## Reasoning level

`--thinking` beats the session's recorded level, which beats settings
`defaultThinkingLevel`, which beats `off`. Since #286 that order holds in every
mode (`-p`, `--mode json`, `--mode rpc` and the TUI). Pass `--thinking` explicitly
in comparisons anyway, so a settings file in the agent dir cannot differ between
the two runs.
