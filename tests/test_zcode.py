"""ZCode: the session reader over its sqlite database, the host adapter, and the bundle."""
import json
import os
import sqlite3
import time

import pytest

from tokeneconomizer import trace
from tokeneconomizer.plugin import accounting, denylist, fold, hosts, history, sidecall
from tokeneconomizer.plugin.context import LiveContext
from tokeneconomizer.plugin.paths import repo_identity, repo_state_dir
from tokeneconomizer.plugin.store import Store

SCHEMA = """
CREATE TABLE session (id text primary key, directory text, title text, version text,
                      parent_id text, time_created integer, time_updated integer);
CREATE TABLE message (id text primary key, session_id text, time_created integer,
                      time_updated integer, data text, sequence integer);
CREATE TABLE part (id text primary key, message_id text, session_id text,
                   time_created integer, time_updated integer, data text, sequence integer);
CREATE TABLE model_usage (id text primary key, session_id text, model_id text, status text,
                          input_tokens integer, output_tokens integer,
                          cache_read_input_tokens integer, cache_creation_input_tokens integer);
"""

FAIL = "bash: pytest: command not found"
OK = "1 passed in 0.1s"
# The environment trap micro-skills learns: bare `pytest` is missing here, the module form runs.
TRAP_CALLS = [("Edit", {"file_path": "pkg/mod.py", "old_string": "a", "new_string": "b"},
               "ok", "completed"),
              ("Bash", {"command": "pytest -q"}, FAIL, "error"),
              ("Bash", {"command": "python -m pytest -q"}, OK, "completed")]
USAGE = {"input_tokens": 1200, "output_tokens": 400, "cache_read_input_tokens": 9000,
         "cache_creation_input_tokens": 3000}


def _write_db(path, sessions, model="glm-5.3", t0=1767225600000):
    """A synthetic ZCode database: `sessions` is [(sid, cwd, calls, usage_or_None[, say])]
    where calls is [(tool, input, output, status)] run under one prompt; a todo reminder rides
    along, and an optional `say` adds the closing assistant text a finished turn ends with (no
    usage row of its own, so `requests` stays the number of tool calls)."""
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    con = sqlite3.connect(str(path))
    con.executescript(SCHEMA)
    for n, session in enumerate(sessions):
        sid, cwd, calls, usage = session[:4]
        say = session[4] if len(session) > 4 else None
        created = t0 + n * 60000
        con.execute("insert into session values (?,?,?,?,?,?,?)",
                    (sid, cwd, "fix the bug", "0.17.1", None, created, created + 30000))
        seq = 0

        def message(mid, role, extra=None):
            nonlocal seq
            data = {"role": role, "time": {"created": created + seq * 1000}}
            data.update(extra or {})
            con.execute("insert into message values (?,?,?,?,?,?)",
                        (f"{sid}-{mid}", sid, created + seq * 1000,
                         created + seq * 1000, json.dumps(data), seq))
            seq += 1
            return f"{sid}-{mid}"

        def part(mid, pdata):
            con.execute("insert into part values (?,?,?,?,?,?,?)",
                        (f"{mid}-{len(pdata)}", mid, sid, created, created,
                         json.dumps(pdata), 0))

        message("m-reminder", "user", {"semantics": {"kind": "todo_reminder",
                                                     "origin": "agent_runtime"}})
        part(f"{sid}-m-reminder", {"type": "text", "text": "The TodoWrite tool hasn't been "
                                                          "used recently."})
        message("m-prompt", "user", {"semantics": {"kind": "user_prompt", "origin": "real_user"}})
        part(f"{sid}-m-prompt", {"type": "text", "text": "fix bug please"})
        for i, call in enumerate(calls):
            tool, inp, out, status = call[:4]
            err = call[4] if len(call) > 4 else None
            mid = message(f"m-call{i}", "assistant", {"modelId": model})
            part(mid, {"type": "step-start"})
            state = {"status": status, "input": inp, "output": out,
                     "time": {"start": created, "end": created + 100}}
            if err is not None:
                state["error"] = err        # where a live failed call carries its text
            part(mid, {"type": "tool", "callID": f"call_{sid}_{i}", "tool": tool,
                       "state": state})
            part(mid, {"type": "step-finish", "reason": "tool-calls"})
            if usage is not None:
                con.execute("insert into model_usage values (?,?,?,?,?,?,?,?)",
                            (f"{sid}-u{i}", sid, model, "completed", usage["input_tokens"],
                             usage["output_tokens"], usage["cache_read_input_tokens"],
                             usage["cache_creation_input_tokens"]))
        if say is not None:
            mid = message("m-final", "assistant", {"modelId": model})
            part(mid, {"type": "text", "text": say})
    con.commit()
    con.close()
    return str(path)


def _db(tmp_path, sessions, model="glm-5.3"):
    return _write_db(tmp_path / "zcode" / "cli" / "db" / "db.sqlite", sessions, model)


def _env(state, db=None):
    env = {"TOKENECONOMIZER_HOME": str(state), "TE_FOLD_SYNC": "1",
           "TE_MICROSKILLS": "envfacts", "TE_TOOLPRUNE": "off",
           "GIT_CEILING_DIRECTORIES": str(state), "PATH": os.environ.get("PATH", "")}
    if db:
        env["ZCODE_DB_PATH"] = str(db)
    return env


def _ctx(env, repo):
    return LiveContext.build(hosts.Zcode(), str(repo), env)


def _store(env, repo):
    return Store(repo_state_dir(repo_identity(repo, env), "zcode", env))


# ---- the reader ------------------------------------------------------------------------------
def test_reader_exports_a_session_as_claude_lines(tmp_path):
    sid = "sess_11111111-2222-3333-4444-555555555555"
    repo = str(tmp_path / "repo")
    db = _db(tmp_path, [(sid, repo, TRAP_CALLS, USAGE)])
    con = trace.zcode_connect({"ZCODE_DB_PATH": db})
    out = str(tmp_path / "claude.jsonl")
    assert trace.zcode_session_to_claude(con, sid, repo, out) == 3
    con.close()
    calls = trace.load_trajectory(out)
    # ZCode's names are Claude's; ApplyPatch and Agent arrive as Edit and Task.
    assert [c[0] for c in calls] == ["Edit", "Bash", "Bash"]
    assert calls[1][1]["command"] == "pytest -q"
    assert calls[1][3] is False and calls[2][3] is True     # the trap failed, the fix ran
    assert trace.call_cwds(out) == [repo, repo, repo]
    assert trace.claude_prompt_count(out) == 1


def test_reader_maps_zcode_aliases():
    assert trace.zcode_claude_tool_name("Agent") == "Task"
    assert trace.zcode_claude_tool_name("Task") == "Task"
    assert trace.zcode_claude_tool_name("ApplyPatch") == "Edit"
    assert trace.zcode_claude_tool_name("Bash") == "Bash"
    assert trace.zcode_claude_tool_name("mcp__x__y") == "mcp__x__y"


def test_a_failed_call_exports_its_error_text(tmp_path):
    """A failed ZCode call carries its text in `state.error` with a null output (seen in the
    live database 2026-11-13); the export must carry it as the tool_result's content, or every
    failed call reads as an empty string and no mechanism can see what failed."""
    sid = "sess_11111111-2222-3333-4444-555555555555"
    repo = str(tmp_path / "repo")
    calls = [("Bash", {"command": "pytest"}, None, "error",
              "zsh: command not found: pytest"),
             ("Bash", {"command": "python -m pytest -q"}, OK, "completed")]
    db = _db(tmp_path, [(sid, repo, calls, USAGE)])
    con = trace.zcode_connect({"ZCODE_DB_PATH": db})
    out = str(tmp_path / "claude.jsonl")
    trace.zcode_session_to_claude(con, sid, repo, out)
    con.close()
    calls = trace.load_trajectory(out)
    assert calls[0][2] == "zsh: command not found: pytest" and calls[0][3] is False
    assert calls[1][3] is True and OK in calls[1][2]


def test_prompt_count_counts_only_the_persons_prompts(tmp_path):
    sid = "sess_11111111-2222-3333-4444-555555555555"
    db = _db(tmp_path, [(sid, str(tmp_path), [], None)])
    con = sqlite3.connect(db)
    # A reminder from before the semantics field existed (an older install) is not a prompt
    # either; one with no semantics and plain text is.
    mid = f"{sid}-m-old"
    con.execute("insert into message values (?,?,?,?,?,?)",
                (mid, sid, 0, 0, json.dumps({"role": "user"}), 99))
    con.execute("insert into part values (?,?,?,?,?,?,?)",
                (mid + "-p", mid, sid, 0, 0,
                 json.dumps({"type": "text", "text": "<system-reminder>meta"}), 0))
    mid = f"{sid}-m-old2"
    con.execute("insert into message values (?,?,?,?,?,?)",
                (mid, sid, 0, 0, json.dumps({"role": "user"}), 100))
    con.execute("insert into part values (?,?,?,?,?,?,?)",
                (mid + "-p", mid, sid, 0, 0,
                 json.dumps({"type": "text", "text": "a legacy prompt"}), 0))
    con.commit()
    assert trace.zcode_prompt_count(con, sid) == 2         # the fixture's own + the legacy one
    con.close()


def test_usage_aggregates_completed_model_usage(tmp_path):
    sid = "sess_11111111-2222-3333-4444-555555555555"
    db = _db(tmp_path, [(sid, str(tmp_path), TRAP_CALLS, USAGE)], model="claude-sonnet-5")
    con = trace.zcode_connect({"ZCODE_DB_PATH": db})
    usage = trace.zcode_session_usage(con, sid)
    con.close()
    # The fixture writes one model_usage row per call, so the totals are three times one call's.
    assert usage["input_tokens"] == 3 * 1200 and usage["output_tokens"] == 3 * 400
    assert usage["cache_read_input_tokens"] == 3 * 9000
    assert usage["cache_creation_input_tokens"] == 3 * 3000
    assert usage["model"] == "claude-sonnet-5" and usage["requests"] == 3
    effective, priced_by = accounting.effective(usage)
    assert priced_by == "model" and effective > 0


def test_an_unpriced_model_is_labelled_fallback(tmp_path):
    sid = "sess_11111111-2222-3333-4444-555555555555"
    db = _db(tmp_path, [(sid, str(tmp_path), TRAP_CALLS, USAGE)], model="glm-5.3")
    con = trace.zcode_connect({"ZCODE_DB_PATH": db})
    effective, priced_by = accounting.effective(trace.zcode_session_usage(con, sid))
    con.close()
    assert priced_by == "fallback" and effective > 0


def test_usage_without_rows_is_not_guessed(tmp_path):
    sid = "sess_11111111-2222-3333-4444-555555555555"
    db = _db(tmp_path, [(sid, str(tmp_path), TRAP_CALLS, None)])
    con = trace.zcode_connect({"ZCODE_DB_PATH": db})
    usage = trace.zcode_session_usage(con, sid)
    con.close()
    assert usage["input_tokens"] is None
    assert accounting.effective(usage) == (None, "no_usage")


def test_an_unreadable_database_degrades(tmp_path):
    assert trace.zcode_connect({"ZCODE_DB_PATH": str(tmp_path / "nope.sqlite")}) is None
    garbage = tmp_path / "garbage.sqlite"
    garbage.write_bytes(b"this is not a database" * 10)
    assert trace.zcode_connect({"ZCODE_DB_PATH": str(garbage)}) is None
    empty = tmp_path / "empty.sqlite"
    empty.write_bytes(b"")
    assert trace.zcode_connect({"ZCODE_DB_PATH": str(empty)}) is None
    assert trace.zcode_sessions({"ZCODE_DB_PATH": str(garbage)}) == []
    con = sqlite3.connect(str(_db(tmp_path, [("sess_a", str(tmp_path), [], None)])))
    assert trace.zcode_session_to_claude(con, "sess_unknown", "", "/dev/null") is None
    assert trace.zcode_prompt_count(con, "sess_unknown") is None
    con.close()


def test_db_paths_follow_the_home_and_the_override(tmp_path):
    home = tmp_path / "home"
    env = {"ZCODE_HOME": str(home)}
    assert trace.zcode_db_paths(env) == [
        str(home / "cli" / "db" / "db.sqlite"), str(home / "v2" / "db.sqlite")]
    _write_db(home / "v2" / "db.sqlite",
              [("sess_11111111-2222-3333-4444-555555555555", str(tmp_path), [], None)])
    rows = trace.zcode_sessions(env)                       # the empty cli db is skipped
    assert [r["id"] for r in rows] == ["sess_11111111-2222-3333-4444-555555555555"]
    assert rows[0]["directory"] == str(tmp_path) and rows[0]["size"] == 0


def test_safe_session_id():
    assert trace.zcode_safe_session_id("sess_1a2b-3c4d") == "sess_1a2b-3c4d"
    assert trace.zcode_safe_session_id("sess_dwf-dwfrun-x-actor_1_1") == \
        "sess_dwf-dwfrun-x-actor_1_1"
    assert trace.zcode_safe_session_id("../escape") is None
    assert trace.zcode_safe_session_id("") is None


# ---- the host ----------------------------------------------------------------------------------
def test_host_registration_and_aliases():
    host = hosts.HOSTS["zcode"]()
    assert host.name == "zcode"
    assert host.notice("x") == {}                 # strict output schema: no systemMessage
    assert host.relays_notices and not host.prompt_context
    assert host.context("SessionStart", "f") == {
        "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "f"}}
    name, _args, _text, _code = host.tool_call(
        {"tool_name": "Agent", "tool_input": {"prompt": "x"}, "tool_response": "ok"})
    assert name == "Task"
    name, args, _text, _code = host.tool_call(
        {"tool_name": "ApplyPatch",
         "tool_input": {"patch": "*** Update File: a.py\n@@\n"}, "tool_response": "ok"})
    assert name == "edit" and args["path"] == "a.py"
    name, args, text, code = host.tool_call(
        {"tool_name": "Bash", "tool_input": {"command": "ls"},
         "tool_response": {"stdout": "f.py", "exitCode": 0}})
    assert (name, text, code) == ("Bash", "f.py", 0)


def test_detect_and_default_host(tmp_path):
    assert isinstance(hosts.detect({"session_id": "sess_abc123"}, {}), hosts.Zcode)
    assert isinstance(hosts.detect({}, {"ZCODE_SESSION_ID": "sess_abc123"}), hosts.Zcode)
    for k in ("TE_HOST", "CLAUDECODE", "COPILOT_CLI", "CURSOR_AGENT", "FACTORY_SESSION_ID"):
        pass
    assert hosts.default_host({"CLAUDE_SESSION_ID": "sess_x"}) == "zcode"
    assert hosts.default_host({"ZCODE_SESSION_ID": "sess_x"}) == "zcode"
    assert hosts.default_host({}) == "claude"
    # Claude Code's own session id variable is a different name: a Claude session started from
    # inside ZCode is still Claude's.
    assert hosts.default_host({"CLAUDE_CODE_SESSION_ID": "x"}) == "claude"


def test_tool_pruning_is_not_available_but_side_calls_can_be_allowed(tmp_path, monkeypatch):
    assert denylist.supports("zcode") is False
    assert "zcode" not in sidecall.NO_SIDE_CALLS
    # Off until the person allows them, by the setting or the variable -- and only with a CLI.
    assert sidecall.zcode_enabled({}) is False
    assert sidecall.zcode_enabled({"TE_ZCODE_SIDECALL": "1"}) is True
    from tokeneconomizer.plugin.config import load_config
    cfg = load_config(str(tmp_path), env={})
    monkeypatch.delenv("TE_ZCODE_SIDECALL", raising=False)
    monkeypatch.setattr(sidecall, "zcode_cli", lambda cli=None: None)
    monkeypatch.setenv("TE_ZCODE_SIDECALL", "1")
    assert sidecall.runner_for("zcode", cfg) is None      # no CLI to run
    monkeypatch.setattr(sidecall, "zcode_cli", lambda cli=None: cli or ["/usr/local/bin/zcode"])
    assert callable(sidecall.runner_for("zcode", cfg))
    monkeypatch.delenv("TE_ZCODE_SIDECALL")
    assert sidecall.runner_for("zcode", cfg) is None      # back off without the variable


def test_the_zcode_sidecall_setting_turns_calls_on_without_a_variable(tmp_path, monkeypatch):
    """The desktop app cannot set an environment variable for its hooks, so the same switch is
    a setting; `zcode_cli` names the CLI when neither PATH nor the app bundle has it."""
    from tokeneconomizer.plugin.config import LiveConfig, load_config, write_config
    monkeypatch.delenv("TE_ZCODE_SIDECALL", raising=False)
    write_config(str(tmp_path / "config.json"),
                 {"zcode_sidecall": "on", "zcode_cli": "/opt/zcode/bin/zcode"})
    cfg = load_config(str(tmp_path), env={})
    assert cfg.zcode_sidecall is True and cfg.zcode_cli == "/opt/zcode/bin/zcode"
    runner = sidecall.runner_for("zcode", cfg)
    assert callable(runner)
    seen = {}

    def fake_call(prompt, model="", cli=None, timeout=sidecall.TIMEOUT_S):
        seen.update(prompt=prompt, cli=cli)
        return "ok", 1.0

    monkeypatch.setattr(sidecall, "zcode_call", fake_call)
    text, eff = runner("propose facts")
    assert (text, eff) == ("ok", 1.0) and seen["cli"] == ["/opt/zcode/bin/zcode"]
    assert LiveConfig().zcode_sidecall is False


def test_zcode_cli_is_found_on_path_or_in_the_app_bundle(monkeypatch):
    monkeypatch.setattr(sidecall.shutil, "which", lambda name: "/usr/local/bin/zcode")
    monkeypatch.setattr(sidecall.os, "access", lambda p, m: False)
    assert sidecall.zcode_cli() == ["/usr/local/bin/zcode"]
    monkeypatch.setattr(sidecall.shutil, "which", lambda name: None)
    assert sidecall.zcode_cli() is None                      # PATH empty, app bundle absent
    monkeypatch.setattr(sidecall.os, "access", lambda p, m: p == sidecall.ZCODE_APP_CLI)
    assert sidecall.zcode_cli() == [sidecall.ZCODE_APP_CLI]  # ... or the bundle is there
    assert sidecall.zcode_cli(["node", "zcode.cjs"]) == ["node", "zcode.cjs"]


def test_a_zcode_side_call_removes_the_acting_tools_and_prices_its_usage(monkeypatch):
    """The call runs `zcode -p --json` in a throwaway directory with every acting tool
    removed, this plugin's hooks off (`TE_SIDECALL`) and stdin closed; its usage, in provider
    units, is normalized for pricing. With no model flag on the CLI, the model only labels
    the usage."""
    import subprocess
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, **kw)
        out = json.dumps({"response": "- use python -m pytest", "usage": {
            "inputTokens": 275000, "outputTokens": 40, "reasoningTokens": 8,
            "cacheReadTokens": 1000, "cacheWriteTokens": 0}})
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(sidecall.subprocess, "run", fake_run)
    text, eff = sidecall.zcode_call("propose facts", cli=["zcode"])
    assert text == "- use python -m pytest" and eff and eff > 0
    cmd = seen["cmd"]
    assert cmd[0] == "zcode" and cmd[cmd.index("-p") + 1] == "propose facts"
    assert cmd[cmd.index("--json") + 1] != "propose facts"  # --json takes no value
    assert cmd[cmd.index("--disallowed-tools") + 1] == sidecall.ZCODE_TOOLS
    assert "Bash" in sidecall.ZCODE_TOOLS and "Task" in sidecall.ZCODE_TOOLS
    assert seen["env"]["TE_SIDECALL"] == "1" and seen["stdin"] == subprocess.DEVNULL
    assert seen["cwd"] != os.getcwd()                       # a throwaway directory


def test_a_zcode_side_call_without_a_reply_notes_it_and_answers_nothing(monkeypatch, tmp_path):
    import subprocess
    monkeypatch.setenv("TOKENECONOMIZER_HOME", str(tmp_path))
    monkeypatch.setattr(sidecall.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 1, stdout="not json", stderr="Please run /login\n"))
    text, eff = sidecall.zcode_call("propose facts", cli=["zcode"])
    assert (text, eff) == ("", None)
    log = (tmp_path / "errors.log").read_text()
    assert "sidecall zcode: no reply" in log
    assert sidecall.paused("zcode", str(tmp_path)) is not None   # and the sign-out paused them


def test_the_host_reads_the_database_end_to_end(tmp_path, monkeypatch):
    """complete() exports the session's rows as Claude lines; usage and prompts come from the
    database; mechanism_trace hands the copy to the readers as it is."""
    sid = "sess_11111111-2222-3333-4444-555555555555"
    repo = tmp_path / "repo"
    repo.mkdir()
    db = _db(tmp_path, [(sid, str(repo), TRAP_CALLS, USAGE)], model="claude-sonnet-5")
    env = _env(tmp_path / "state", db)
    # A hook or a `te fold` runs in its own process: the database location reaches the host
    # through the process environment, the same way ZCODE_HOME does.
    monkeypatch.setenv("ZCODE_DB_PATH", str(db))
    host = hosts.Zcode()
    path = host.complete(None, sid, str(repo), {"updated": time.time()})
    assert os.path.basename(path) == f"{sid}.jsonl"
    assert host.mechanism_trace(path, str(tmp_path)) == path
    assert host.prompt_count(path) == 1
    usage = host.session_usage(path)
    assert usage["input_tokens"] == 3 * 1200 and usage["requests"] == 3
    effective, priced_by = accounting.effective(usage)
    assert priced_by == "model" and effective > 0


def test_the_temp_transcript_of_a_hook_call_never_blocks_the_fold(tmp_path, monkeypatch):
    """Each hook call carries a temp transcript (basename `transcript.jsonl`, deleted soon
    after) that the first hook stores in the session record: `complete` must export from the
    database regardless, and `prepare` must store the export path in the first place (found
    live 2026-11-13: the temp path made every fold raise FoldLater, silently)."""
    sid = "sess_11111111-2222-3333-4444-555555555555"
    repo = tmp_path / "repo"
    repo.mkdir()
    db = _db(tmp_path, [(sid, str(repo), TRAP_CALLS, USAGE)], model="claude-sonnet-5")
    env = _env(tmp_path / "state", db)
    monkeypatch.setenv("ZCODE_DB_PATH", str(db))
    host = hosts.Zcode()
    temp = str(tmp_path / "zcode-hook-abc123" / "transcript.jsonl")
    assert os.path.basename(host.complete(temp, sid, str(repo),
                                          {"updated": time.time()})) == f"{sid}.jsonl"
    prepared = host.prepare({"session_id": sid, "transcript_path": temp,
                             "hook_event_name": "SessionStart"}, env)
    assert os.path.basename(prepared["transcript_path"]) == f"{sid}.jsonl"


# ---- folding: no SessionEnd, the next session start does it ---------------------------------
def test_a_session_whose_end_was_missed_is_folded_by_the_next_start(tmp_path, monkeypatch):
    """ZCode has no SessionEnd: the session stays pending until it has been quiet, and the next
    session start in the repository folds it (the Antigravity arrangement)."""
    from test_live_hooks import call
    sid = "sess_11111111-2222-3333-4444-555555555555"
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    db = _db(tmp_path, [(sid, repo, TRAP_CALLS, USAGE)], model="claude-sonnet-5")
    env = _env(tmp_path / "state", db)
    monkeypatch.setenv("ZCODE_DB_PATH", str(db))
    call(env, {"hook_event_name": "SessionStart", "session_id": sid, "cwd": repo,
               "source": "startup"}, "zcode")                # ... then the window was closed
    st = _store(env, repo)
    sess = st.read(os.path.join("sessions", f"{sid}.json")) or {}
    sess["updated"] = time.time() - 3600                    # quiet for the stale window
    st.write(os.path.join("sessions", f"{sid}.json"), sess)
    call(env, {"hook_event_name": "SessionStart", "session_id": "sess_99999999-0000-0000-0000-"
               "000000000000", "cwd": repo, "source": "startup"}, "zcode")
    assert sid in (st.read("folded.json") or {})
    rows = [r for r in st.read_lines("ledger.jsonl") if r.get("kind") == "session"]
    assert len(rows) == 1 and rows[0]["host"] == "zcode"
    assert rows[0]["prompts"] == 1 and rows[0]["priced_by"] == "model"


def test_five_learnt_sessions_inject_the_fact_at_the_next_start(tmp_path, monkeypatch):
    """The offline end to end: five finished ZCode sessions are folded, and the next session
    start's own stdout carries the fact in the nested Claude shape."""
    from test_live_hooks import call
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "runtests.py").write_text("print('OK')\n")
    sessions = [(f"sess_aaaaaaaa-0000-0000-0000-00000000000{n}", str(repo), TRAP_CALLS, USAGE)
                for n in range(5)]
    db = _db(tmp_path, sessions)
    env = _env(tmp_path / "state", db)
    monkeypatch.setenv("ZCODE_DB_PATH", str(db))
    ctx = _ctx(env, repo)
    for sid, *_ in sessions:
        assert fold.fold_session(ctx, sid) is not None
    out = call(env, {"hook_event_name": "SessionStart",
                     "session_id": "sess_bbbbbbbb-0000-0000-0000-000000000000",
                     "cwd": str(repo), "source": "startup"}, "zcode")
    assert "`python -m pytest" in out["hookSpecificOutput"]["additionalContext"]


def test_an_unreadable_database_leaves_the_session_unfolded_now(tmp_path):
    """A busy or missing database is not an error: the fold waits for a later session start
    (FoldLater), for at most the retry window."""
    import pytest as _pytest
    from tokeneconomizer.plugin.hosts import FoldLater
    sid = "sess_11111111-2222-3333-4444-555555555555"
    repo = tmp_path / "repo"
    repo.mkdir()
    env = _env(tmp_path / "state", str(tmp_path / "nope.sqlite"))
    ctx = _ctx(env, repo)
    with _pytest.raises(FoldLater):
        hosts.Zcode().complete(None, sid, str(repo), {"updated": time.time()})
    # Past the retry window the fold gives up on the rows and records an unreadable session.
    stale = time.time() - (hosts.Zcode().FOLD_RETRY_S + 100)
    record = fold.fold_locked(ctx, sid, None, span=(stale, stale))
    assert record["error"] == "transcript unreadable"


# ---- the history import -----------------------------------------------------------------------
def test_history_lists_sessions_and_leaves_subworkflows_out(tmp_path):
    repo = str(tmp_path / "repo")
    db = _db(tmp_path, [("sess_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", repo, TRAP_CALLS, USAGE),
                        ("sess_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", repo, TRAP_CALLS, USAGE)])
    con = sqlite3.connect(db)                     # a sub-workflow session, as Droid's are
    con.execute("update session set parent_id='sess_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'"
                " where id='sess_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'")
    con.commit()
    con.close()
    env = {"ZCODE_DB_PATH": db}
    rows = hosts.Zcode().history(env)
    assert [r["session_id"] for r in rows] == ["sess_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]
    assert rows[0]["folder"] == trace.claude_project_folder(repo)
    assert os.path.basename(rows[0]["path"]) == f"{rows[0]['session_id']}.jsonl"


def test_history_import_learns_facts_and_prices_the_sessions(tmp_path, monkeypatch):
    from tokeneconomizer.plugin import history as te_history
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "runtests.py").write_text("print('OK')\n")
    sessions = [(f"sess_cccccccc-0000-0000-0000-00000000000{n}", str(repo), TRAP_CALLS, USAGE,
                 "Done: 1 passed in 0.1s") for n in range(5)]
    db = _db(tmp_path, sessions, model="claude-sonnet-5")
    env = _env(tmp_path / "state", db)
    monkeypatch.setenv("ZCODE_DB_PATH", str(db))
    ctx = _ctx(env, repo)
    plan = te_history.plan(ctx)
    assert len(plan["sessions"]) == 5 and plan["skipped"] == {}
    res = te_history.run(ctx, now=time.time())
    assert res["state"] == "done" and res["imported"] == 5
    assert te_history.describe(te_history.summary(ctx), "zcode") == \
        "read 5 earlier session(s) from ZCode's history"

    injected = ctx.store.read("injection.json")
    assert injected["profile"] == "envfacts" and injected["sessions"] == 5
    assert "`python -m pytest" in injected["rendered"]
    rows = [r for r in ctx.store.read_lines("ledger.jsonl") if r.get("kind") == "session"]
    assert len(rows) == 5
    assert all(r["host"] == "zcode" and r["imported"] and r["prompts"] == 1 for r in rows)
    assert all(r["effective_tokens"] and r["priced_by"] == "model" for r in rows)
    # The loop guard's envelope was seeded from the imported turns (each trap session ends
    # with a green `python -m pytest` run after the red one).
    assert ctx.store.read("envelope.json")


def test_session_usage_takes_the_sessions_database_override(tmp_path, monkeypatch):
    """`complete` exports from the database the session record names; `session_usage` prices
    from the same one -- even when the process environment points somewhere else (the
    `te fold` in a plain shell, against two installs)."""
    sid = "sess_11111111-2222-3333-4444-555555555555"
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    db_a = _db(tmp_path / "a", [(sid, repo, TRAP_CALLS, USAGE)], model="claude-sonnet-5")
    db_b = _db(tmp_path / "b", [(sid, repo, TRAP_CALLS, USAGE)], model="claude-sonnet-5")
    monkeypatch.setenv("ZCODE_DB_PATH", db_b)      # the shell's environment names another db
    host = hosts.Zcode()
    path = host.complete(None, sid, repo, {"zcode_db": db_a, "updated": time.time()})
    assert os.path.basename(path) == f"{sid}.jsonl"
    # ... and prices it from that same database, not the environment's.
    usage = host.session_usage(path, {"zcode_db": db_a})
    assert usage["requests"] == 3 and usage["input_tokens"] == 3 * 1200
    assert host.session_usage(path)["input_tokens"] == 3 * 1200   # the environment's db: same


# ---- packaging ---------------------------------------------------------------------------------
def test_zcode_plugin_packaging(tmp_path):
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    file = json.load(open(os.path.join(repo_root, "plugin", "hooks", "zcode-hooks.json")))
    # ZCode requires the outer "hooks" wrapper (the configuration file's shape minus the
    # events level); a bare event map registers zero hooks, silently.
    assert set(file) == {"hooks"}
    hooks = file["hooks"]
    assert set(hooks) == {"SessionStart", "UserPromptSubmit", "PostToolUse", "PostToolUseFailure",
                          "Stop"}
    command = hooks["SessionStart"][0]["hooks"][0]["command"]
    assert command.endswith('--host zcode') and "${ZCODE_PLUGIN_ROOT}/scripts/te-hook" in command
    assert "SessionEnd" not in hooks                    # ZCode has no such event

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "build_plugin", os.path.join(repo_root, "scripts", "build_plugin.py"))
    build_plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build_plugin)
    out = tmp_path / "bundle"
    build_plugin.build_zcode(str(out))
    plugin = out / "tokeneconomizer"
    manifest = json.load(open(plugin / "package.json"))
    assert manifest["name"] == "@zcode/tokeneconomizer-plugin"
    built = json.load(open(plugin / "hooks" / "hooks.json"))
    assert set(built) == {"hooks"} and set(built["hooks"]) == set(hooks)
    assert (plugin / "lib" / "tokeneconomizer" / "plugin" / "hook.py").exists()
    assert (plugin / "scripts" / "te-hook").exists()
    # The other agents' files, which sit beside ZCode's in the checkout: ZCode reads neither,
    # and the bundle ships only what ZCode loads.
    assert not (plugin / ".factory-plugin").exists()
    assert not (plugin / "hooks.json").exists()
    assert not (plugin / "hooks" / "droid-hooks.json").exists()
