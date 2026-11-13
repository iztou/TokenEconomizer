# TokenEconomizer on ZCode

Experimental support for the ZCode desktop app / `zcode-cli`, written 2026-11-12 against
ZCode 3.17.2 (`ZCODE_APP_VERSION`) on macOS. Micro-skills, the loop guard, session cost and the
repository's own learned state all work; **tool pruning is not available**, and the plugin's
own model calls (the paid proposer, the loop guard's judge) run through `zcode -p` but **only
when `zcode_sidecall` is turned on**.

## What works, and what does not

- **Micro-skills** (LLM-free `envfacts` facts always; the paid proposer and the loop guard's
  judge once `zcode_sidecall` is on) and **the loop guard** (rule backstop works either way) —
  off/on as on every host.
- **Session cost**: each session is priced from ZCode's own `model_usage` rows (below), so a
  folded session is priced like any other host's; a model no price table names is labelled
  `fallback`.
- **History import**: `te --host zcode history import` (or `history_import=on`, whereupon the
  first session start starts it) reads a repository's earlier sessions from the database and
  folds them once — no model call, no deny list written, as on every host. Sub-workflow
  sessions (a `parent_id`) are left out; each listed session is exported on demand as Claude
  transcript lines under `~/.tokeneconomizer/zcode-sessions/`, and priced from the same
  database it was read from.
- **No `SessionEnd`**: ZCode documents seven hook events (`SessionStart`, `UserPromptSubmit`,
  `PreToolUse`, `PermissionRequest`, `PostToolUse`, `PostToolUseFailure`, `Stop`) and no
  session-end event. The plugin registers five of them (it needs no `PreToolUse` or
  `PermissionRequest`) and a conversation is folded by the next session start in its repository
  once it has been quiet for 10 minutes — the same arrangement as the Antigravity CLI.
- **Tool pruning is not available**: ZCode lazy-loads MCP tools, and the plugin knows no
  persistent per-tool setting on this host (`denylist.supports` says so; nothing is written).
- **Side calls run `zcode -p --json`** (the desktop app's own CLI — `zcode` on PATH, else the
  app bundle's `/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs`, else `zcode_cli`)
  in a throwaway directory with every acting tool removed (`--disallowed-tools`) and
  `TE_SIDECALL=1` keeping this plugin's hooks out of the nested session. Two reasons they are
  **off by default** (`zcode_sidecall=on` or `TE_ZCODE_SIDECALL=1` turns them on): each call
  carries ZCode's whole base prompt with every plugin and skill loaded — ~275k input tokens,
  none cached, on the person's plan (verified 2026-11-13) — and each call writes a session of
  its own into ZCode's database. The CLI publishes no model flag, so a call runs on the
  person's default model. With `microskills=envfacts`, learning and the backstop work without
  any of this.

## Install

```sh
git clone https://github.com/microsoft/TokenEconomizer.git
cd TokenEconomizer
python scripts/build_plugin.py --zcode        # writes dist/tokeneconomizer-zcode/tokeneconomizer
```

Then either let ZCode install the directory (a marketplace that names it), or place it by hand
the way this machine was set up (2026-11-12):

1. Copy `dist/tokeneconomizer-zcode/tokeneconomizer/` to
   `~/.zcode/cli/plugins/cache/local/tokeneconomizer/0.1.0/`.
2. Add a `local` marketplace to `~/.zcode/cli/plugins/known_marketplaces.json` and a
   `tokeneconomizer@local` entry to `~/.zcode/cli/plugins/installed_plugins.json`
   (`installPath` = that cache directory, `scope: "user"`).
3. The plugin directory itself needs a plugin manifest, `.zcode-plugin/plugin.json` (the
   official plugins also mirror it at `.claude-plugin/plugin.json`) — verified 2026-11-13 in
   the app bundle: the app reads the plugin's name/version/description from that file, not
   from `package.json`, and rejects a marketplace plugin source that lacks it ("local
   marketplace plugin source is missing plugin manifest"). `build_plugin.py --zcode` writes
   it since 2026-11-13; for a hand-placed copy, add it beside `package.json`.
4. The marketplace also needs its own manifest or the desktop app's plugin list cannot
   enumerate it (verified 2026-11-13 in the app bundle: `findMarketplaceManifestPath` reads
   `<marketplace root>/marketplace.json` or `<marketplace root>/.claude-plugin/marketplace.json`,
   requires `name` to equal the marketplace id, and enumerates `plugins[]`; a plugin source is
   `{"source": "directory", "path": ...}`). Write
   `~/.zcode/cli/plugins/cache/local/marketplace.json` with `name: "local"` and one
   `tokeneconomizer` entry pointing at the directory above. Without it the registries and
   runtime resolution are fine but the plugin list shows nothing.
5. Enable it: `~/.zcode/cli/config.json` → `plugins.enabledPlugins["tokeneconomizer@local"] =
   true`.
6. Hooks are disabled by default in the configuration file; turn them on: in
   `~/.zcode/cli/config.json`, set `"hooks": {"enabled": true, ...}` beside the event map
   (the plugin ships `hooks/hooks.json`; ZCode reads the plugin's own file). **This last step
   is written from the confirmed contract but has not fired live yet — see the probe record.**

The plugin manifest is `.zcode-plugin/plugin.json` (mirrored at `.claude-plugin/plugin.json`;
`package.json` is not read as one), and the hook file is `hooks/hooks.json` with an outer
`"hooks"` wrapper around the event map — `{ "hooks": { "<Event>": [ { "hooks": [ { "type":
"command", ... } ] } ] } }`. A bare event map (Claude Code's older plugin shape) parses to zero
hooks *silently* — the probe's `hookCount: 0` was this, not the `hooks.enabled` gate
(verified 2026-11-13 against superpowers 5.1.0 on this machine and in the app bundle's schema).
Entries are `type: command` with `timeout` in seconds and `${ZCODE_PLUGIN_ROOT}`
(`${CLAUDE_PLUGIN_ROOT}` works too).

**Check it.** Start a new ZCode session (hooks resolve at session start) and, in a repository,
run `~/.tokeneconomizer/bin/te --host zcode status`. Hook execution appears in `~/.zcode`'s logs
(`~/.zcode/cli/log/zcode-<date>.jsonl`, `~/.zcode/v2/logs/<date>.log`).

## The session database

Every conversation lives as rows of one sqlite database; nothing depends on the hook payload
carrying a transcript path.

- Operative: `~/.zcode/cli/db/db.sqlite` (487 sessions here on 2026-11-12). The older
  `~/.zcode/v2/db.sqlite` stays empty on current installs; both are probed, and
  `ZCODE_DB_PATH` / `ZCODE_HOME` override the locations.
- Tables read: `session` (`id` = `sess_...`, `directory`, `title`, `parent_id`,
  `time_created`/`time_updated` in ms), `message` (`data` JSON: `role`,
  `semantics.kind` — `user_prompt` is a person's prompt, `system_reminder`/`todo_reminder`/
  `background_notification`/`compact_summary`/`fork_notice` are not), `part`
  (`data.type`: `text`/`reasoning`/`step-start`/`step-finish`/`tool`; a `tool` part holds
  `callID`, `tool`, `state.input`, `state.output`, `state.status`),
  `model_usage` (`model_id`, the four token counts, `status='completed'` rows summed).
- The fold exports a session as Claude transcript lines
  (`tokeneconomizer/trace/zcode.py`) under `~/.tokeneconomizer/zcode-sessions/`; the existing
  readers take it from there. Opened read-only (`file:...?mode=ro`), so a busy writer never
  breaks a fold; an unreadable database leaves the session for a later start (retried up to
  7 days, then recorded as unreadable).
- Tool names are Claude's (`Bash`, `Read`, `Write`, `Edit`, `mcp__*` — measured on the live
  database). `Agent` is read as `Task`, `ApplyPatch` as `Edit`.

## Probe record (2026-11-12)

Evidence gathered on this machine while writing the adapter; the probe script and its dump live
outside the repository (`/tmp/te-zcode-probe/`, `events.jsonl`, `backup/`).

Verified, read-only, against the running app:

- **Plugin shape**: the installed official plugins are `package.json` (npm fields) +
  `hooks/hooks.json` in the Claude shape with `${CLAUDE_PLUGIN_ROOT}` commands and a `matcher`
  field; `~/.zcode/cli/plugins/known_marketplaces.json` and `installed_plugins.json` hold the
  registries (`cat` of both; superpowers 5.1.0, ios-simulator 0.1.0).
- **Session storage**: `~/.zcode/cli/db/db.sqlite` holds 487 sessions; table and column shapes
  as above, read with `sqlite3` in read-only URI mode. `~/.zcode/v2/db.sqlite` is 0 bytes.
- **The shell marker**: a tool shell inside ZCode exports `ZCODE_SESSION_ID`,
  `CLAUDE_SESSION_ID` and `CLAUDE_CODE_SESSION_ID` (all `sess_...`), so `te` inside ZCode names
  its host without `--host` (`env | grep SESSION_ID`, run 2026-11-12). It does **not** set
  `CLAUDECODE=1` (`env | grep -c '^CLAUDECODE='` = 0, run 2026-11-13), so the Claude Code
  marker in `default_host` never shadows ZCode there.
- **Hook execution records**: `~/.zcode/cli/log/zcode-2026-11-12.jsonl` logs `turn.phase`
  entries with hook phases, and `~/.zcode/v2/logs/2026-11-12.log` logs
  `"phase":"hooks"` per turn; `bootstrap.app.startup.plugins.completed` resolves plugins once
  per session start and reports `hookCount`.
- **Hooks are off by default**: every resolution on this machine logged `"hookCount":0` although
  an enabled plugin (superpowers) ships a `SessionStart` hook — consistent with the documented
  `hooks.enabled: true` gate. (Superseded 2026-11-13: the real cause of every `hookCount:0`
  was the bare event map in `hooks.json` — see Install — and with the wrapped file the count
  is 5 with `hooks.enabled` unset by the plugin alone; `config.json` now also sets it.)
- **Versions**: app 3.17.2 (`ZCODE_APP_VERSION`); the database's `session.version` column
  reports the runtime's `0.17.1`. Cited as 3.17.2 throughout the docs.

Verified live since (2026-11-13):

- **The hooks fire**: after wrapping `hooks.json`, plugin resolution reports `hookCount: 5`,
  hooks run per turn (the v2 log's `"phase":"hooks"` carries real durations), and the plugin's
  state appears under `~/.tokeneconomizer` from the first session on. `${ZCODE_PLUGIN_ROOT}`
  expands in the installed copy — the hook commands all depend on it.
- **The payload** carries `session_id`, `cwd`, `hook_event_name`, `source` and a
  `transcript_path` that is a TEMP copy for that one hook call (basename `transcript.jsonl`,
  under `$TMPDIR/zcode-hook-*`, deleted soon after). The session body is NOT that file: it
  comes from the database, and `Zcode.prepare` rewrites the record's transcript to the export
  path. Before that rewrite (fixed 2026-11-13), the temp path reached `Zcode.complete`, whose
  `_export` rejects any basename that does not name the session — every fold raised
  `FoldLater`, silently, and no session was ever folded.
- **The output shape** (`glm/zcode.cjs` in the app bundle): hook stdout is parsed as an object
  that accepts BOTH a top-level `additionalContext` (or `additional_context`) and the Claude
  nested `hookSpecificOutput: {hookEventName, additionalContext}` — the nested form must carry
  the exact event name or the run throws "Hook returned wrong event name". `Zcode.context()`
  ships the nested form and needs no change.
- **The fold and its pricing** (2026-11-13): two sessions folded, exported under
  `~/.tokeneconomizer/zcode-sessions/`, and priced from `model_usage`.
- **The headless CLI** (2026-11-13): `zcode.cjs -p "<prompt>"` runs one prompt without the TUI
  (`--mode yolo` is its default there), `--json` prints `{sessionId, response, usage{
  inputTokens, cacheReadTokens, cacheWriteTokens, outputTokens, reasoningTokens}, ...}`, and
  `--disallowed-tools "Bash,Read,..."` removes whole tools from that run. `--cwd` needs a
  value before `-p`, or `-p` swallows the next flag as its text. A `pong` round trip takes
  ~9s; the prompt side is ~275k input tokens with every plugin and skill loaded, ~11k less
  with the acting tools removed. There is no model flag and no way to skip the plugins.
- **A side call through it** (2026-11-13): `sidecall.runner_for("zcode", cfg)` with
  `zcode_sidecall=on` answered a proposer-shaped prompt in 9.3s and priced itself at 275,253
  effective tokens; the call's own session lands in ZCode's database under a throwaway
  directory, which no repository claims, so no fold counts it.
- **The whole learn-then-inject loop, live** (2026-11-13 21:39): four `-p` sessions in the
  repository each tripped the `python -m pytest` trap and self-repaired to `python3 -m pytest`
  (the pipe hides the exit code -- `python -m pytest | tail` reports completed -- but the
  observer classifies `command not found` from the output text, so learning works); the next
  session's start folded each. After the fourth fold the fact was ACTIVE (fire 4/5), and a
  fifth session that was forbidden to run anything answered the manual verbatim. In the
  database that session's first `system_reminder` message is `"SessionStart hook additional
  context:"` + the rendered manual: that is how an `additionalContext` reaches the model on
  this host, beside the git status block. Zero hook schema errors that day; the distiller
  proposer added its own manual through the side call, at 1.5% plugin overhead.
- **A failed call's text lives in `state.error`** (2026-11-13): a `tool` part that failed has
  `state.output = null` and its message in `state.error`; the export now carries it as the
  `tool_result` content (before the fix every failed result exported as an empty string, and
  no mechanism could see what had failed).

Still not verified live: that an injected fact (a rendered envfacts manual, which needs the
fact lifecycle's firing gate — ACTIVE, ≥ 4 exercised tasks, ≥ 40% firing) reaches a model in
a new session. Until the gate clears, a SessionStart answers `{}`, and a model asked "what do
you know about this repository" explores it itself — its answer is not evidence of injection.

## Uninstall and rollback

- Remove the plugin: delete `~/.zcode/cli/plugins/cache/local/tokeneconomizer/`, the
  `tokeneconomizer@local` entry in `installed_plugins.json`, the `local` marketplace in
  `known_marketplaces.json`, and the `plugins.enabledPlugins["tokeneconomizer@local"]` key in
  `config.json`.
- The learned state is `~/.tokeneconomizer` (`hosts/zcode/`, `zcode-sessions/`, `bin/te`);
  delete it to forget everything.
- Nothing under `~/.zcode` is otherwise written: no per-repository settings file (tool pruning
  is not available here), and the session database is only ever read.
