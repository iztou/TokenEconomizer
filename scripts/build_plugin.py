#!/usr/bin/env python3
"""Assemble a self-contained TokenEconomizer plugin bundle and a marketplace around it.

    python scripts/build_plugin.py [--out dist/marketplace] [--zip]
    python scripts/build_plugin.py --droid [--out dist/tokeneconomizer-droid]

Why a build step at all. Loaded in place -- `claude --plugin-dir plugin/`, or this repository
added as a local-directory marketplace -- the plugin finds the core package one level up, in the
checkout itself. A marketplace INSTALL is different: Claude Code copies only the plugin directory
into its cache ("files outside the plugin directory aren't copied"), and Codex does the same, so
an installed copy of `plugin/` alone has no `tokeneconomizer` to import. The bundle fixes that by
copying the package (`tokeneconomizer/`, the mechanisms and the plugin runtime) into
`<plugin>/lib/`, which `scripts/te_bootstrap.py` looks in first.

The package is stdlib-only, so copying it is the whole dependency story: the bundle runs on any
Python >= 3.10 with nothing installed. `experiments/`, `scripts/` and the docs are not included.

Layout written to --out (a marketplace root you can `marketplace add`, publish as a branch or a
repository of its own, or zip for `claude --plugin-url`):

    <out>/.claude-plugin/marketplace.json
    <out>/tokeneconomizer/              the plugin, with lib/tokeneconomizer copied in
"""
import argparse
import json
import os
import shutil
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", ".DS_Store")

# The ZCode bundle's own version and description, the one place both manifests and the
# marketplace entry take them from. Bump the version for a release: a marketplace update
# shows only when the version moves.
ZCODE_VERSION = "0.1.0"
ZCODE_DESCRIPTION = (
    "TokenEconomizer for ZCode: learned repository facts injected at session start, "
    "session cost from ZCode's own usage, and a guard for turns stuck in a failing loop.")


def build(out):
    out = os.path.abspath(out)
    plugin_out = os.path.join(out, "tokeneconomizer")
    if os.path.exists(plugin_out):
        shutil.rmtree(plugin_out)
    # plugin/lib holds a symlink to the package for an install from a checkout (see
    # te_bootstrap); the bundle replaces it with a real copy.
    shutil.copytree(os.path.join(REPO, "plugin"), plugin_out,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", "lib"))
    shutil.copytree(os.path.join(REPO, "tokeneconomizer"),
                    os.path.join(plugin_out, "lib", "tokeneconomizer"), ignore=IGNORE)
    with open(os.path.join(REPO, ".claude-plugin", "marketplace.json"), encoding="utf-8") as fh:
        market = json.load(fh)
    for entry in market.get("plugins", []):
        if entry.get("name") == "tokeneconomizer":
            entry["source"] = "./tokeneconomizer"
    os.makedirs(os.path.join(out, ".claude-plugin"), exist_ok=True)
    with open(os.path.join(out, ".claude-plugin", "marketplace.json"), "w",
              encoding="utf-8") as fh:
        json.dump(market, fh, indent=2)
        fh.write("\n")
    return plugin_out


def build_droid(out):
    """The same bundle laid out as a native Factory Droid plugin and marketplace.

    Droid loads `hooks/hooks.json` in its own shape (events at the top level) and needs a
    `.factory-plugin/plugin.json` to treat the directory as a native plugin; its slash commands
    live in `commands/`, not Claude Code's `slash-commands/`. The upstream files stay as they
    are: only the bundle is rewritten."""
    out = os.path.abspath(out)
    plugin_out = os.path.join(out, "tokeneconomizer")
    if os.path.exists(plugin_out):
        shutil.rmtree(plugin_out)
    # Only what Droid loads: the other agents' manifests and payloads would make the same
    # directory look like a Claude, Codex or Pi plugin too, and Droid reads those layouts.
    shutil.copytree(os.path.join(REPO, "plugin"), plugin_out,
                    ignore=shutil.ignore_patterns(
                        "__pycache__", "*.pyc", ".pytest_cache", "lib", ".DS_Store",
                        ".claude-plugin", ".codex-plugin", ".cursor-plugin", ".github",
                        ".antigravity-plugin", "hooks.json",
                        "codex-skills", "hermes", "opencode", "pi", "slash-commands",
                        "package.json", "*.md.bak"))
    shutil.copytree(os.path.join(REPO, "tokeneconomizer"),
                    os.path.join(plugin_out, "lib", "tokeneconomizer"), ignore=IGNORE)
    # Droid's hook file: top-level events, `--host droid`, no PostToolUseFailure (Droid has no
    # such event).
    shutil.copyfile(os.path.join(REPO, "plugin", "hooks", "droid-hooks.json"),
                    os.path.join(plugin_out, "hooks", "hooks.json"))
    for name in ("droid-hooks.json", "codex-hooks.json", "copilot-hooks.json",
                 "cursor-hooks.json"):        # other agents' hook files: Droid reads one
        stale = os.path.join(plugin_out, "hooks", name)
        if os.path.exists(stale):
            os.remove(stale)
    # Slash commands become Droid commands. Droid has an Execute tool, not Claude's Bash, and
    # expands ${DROID_PLUGIN_ROOT}.
    commands_out = os.path.join(plugin_out, "commands")
    os.makedirs(commands_out, exist_ok=True)
    for name in sorted(os.listdir(os.path.join(REPO, "plugin", "slash-commands"))):
        if not name.endswith(".md"):
            continue
        text = open(os.path.join(REPO, "plugin", "slash-commands", name),
                    encoding="utf-8").read()
        text = text.replace("${CLAUDE_PLUGIN_ROOT}", "${DROID_PLUGIN_ROOT}")
        text = text.replace("with the Bash tool", "with the Execute tool")
        # Droid command names come straight from the filename and are not namespaced by plugin,
        # so `status.md` would become `/status` next to Droid's own commands.
        with open(os.path.join(commands_out, f"tokeneconomizer-{name}"), "w",
                  encoding="utf-8") as fh:
            fh.write(text)
    os.makedirs(os.path.join(out, ".factory-plugin"), exist_ok=True)
    with open(os.path.join(out, ".factory-plugin", "marketplace.json"), "w",
              encoding="utf-8") as fh:
        json.dump({
            "name": "tokeneconomizer-droid",
            "description": "TokenEconomizer for Factory Droid: learned repository facts, priced "
                           "sessions, and a failure-loop guard",
            "owner": {"name": "TokenEconomizer authors"},
            "plugins": [{"name": "tokeneconomizer", "source": "./tokeneconomizer",
                         "description": "Micro-skills injected at session start, session cost "
                                        "from Droid's own usage, and a guard for turns stuck in "
                                        "a failing loop.",
                         "category": "productivity"}],
        }, fh, indent=2)
        fh.write("\n")
    return plugin_out


def build_zcode(out):
    """The same bundle laid out as a native ZCode plugin (`plugin/package.json` manifest,
    `hooks/hooks.json` for its Claude-format hook file, `${ZCODE_PLUGIN_ROOT}` commands).

    ZCode loads `hooks/hooks.json` in the Claude shape but has no `SessionEnd`, and its output
    schema is strict, so the bundle ships the five-event `hooks/zcode-hooks.json` in its place;
    the other agents' manifests would make the same directory look like a Claude, Codex, Pi or
    Droid plugin too. The upstream files stay as they are: only the bundle is rewritten.

    Two manifests make it visible (verified 2026-11-13 in the ZCode 3.17.2 bundle): the plugin
    manifest `.zcode-plugin/plugin.json` — the app reads name/version/description from it
    (`package.json` is not a plugin manifest) and refuses a plugin source that lacks it — and
    the marketplace root's `marketplace.json`, which the app's plugin list enumerates. Its
    `name` must equal the marketplace id, which for a GitHub source is the repository name:
    the published channel is `tokeneconomizer-zcode`, so that is the name here too. Version
    comes from the one constant; bump it for a release (a marketplace update only shows when
    the version moves)."""
    out = os.path.abspath(out)
    plugin_out = os.path.join(out, "tokeneconomizer")
    if os.path.exists(plugin_out):
        shutil.rmtree(plugin_out)
    shutil.copytree(os.path.join(REPO, "plugin"), plugin_out,
                    ignore=shutil.ignore_patterns(
                        "__pycache__", "*.pyc", ".pytest_cache", "lib", ".DS_Store",
                        ".claude-plugin", ".codex-plugin", ".cursor-plugin", ".github",
                        ".antigravity-plugin", ".factory-plugin", "hooks.json",
                        "codex-skills", "hermes", "opencode", "pi", "slash-commands",
                        "package.json", "*.md.bak"))
    shutil.copytree(os.path.join(REPO, "tokeneconomizer"),
                    os.path.join(plugin_out, "lib", "tokeneconomizer"), ignore=IGNORE)
    # ZCode's hook file: the five events it runs, `--host zcode`, `${ZCODE_PLUGIN_ROOT}`.
    shutil.copyfile(os.path.join(REPO, "plugin", "hooks", "zcode-hooks.json"),
                    os.path.join(plugin_out, "hooks", "hooks.json"))
    for name in ("droid-hooks.json", "codex-hooks.json", "copilot-hooks.json",
                 "cursor-hooks.json", "zcode-hooks.json"):   # other agents' hook files
        stale = os.path.join(plugin_out, "hooks", name)
        if os.path.exists(stale):
            os.remove(stale)
    with open(os.path.join(plugin_out, "package.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "name": "@zcode/tokeneconomizer-plugin",
            "version": ZCODE_VERSION,
            "private": True,
            "license": "Apache-2.0",
            "description": ZCODE_DESCRIPTION,
            "keywords": ["cost", "tokens", "context", "hooks"],
        }, fh, indent=2)
        fh.write("\n")
    # The plugin manifest the app itself reads (mirrored in the Claude location, as the
    # official plugins do).
    plugin_manifest = {
        "name": "tokeneconomizer",
        "version": ZCODE_VERSION,
        "description": ZCODE_DESCRIPTION,
        "author": {"name": "TokenEconomizer contributors"},
        "license": "Apache-2.0",
        "homepage": "https://github.com/microsoft/TokenEconomizer",
        "repository": "https://github.com/microsoft/TokenEconomizer",
        "keywords": ["cost", "tokens", "context", "hooks"],
    }
    for dirname in (".zcode-plugin", ".claude-plugin"):
        os.makedirs(os.path.join(plugin_out, dirname), exist_ok=True)
        with open(os.path.join(plugin_out, dirname, "plugin.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(plugin_manifest, fh, indent=2)
            fh.write("\n")
    # The marketplace manifest at the bundle root: the app looks for <root>/marketplace.json
    # (or .claude-plugin/marketplace.json) and enumerates its plugins[]. Presentation fields
    # (displayName, i18n, categories) are the marketplace entry's; the icon is omitted rather
    # than fabricated (the spec says to omit absent assets).
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "marketplace.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "name": "tokeneconomizer-zcode",
            "version": 1,
            "description": "TokenEconomizer marketplace for ZCode",
            "owner": {"name": "TokenEconomizer authors"},
            "plugins": [{"name": "tokeneconomizer", "version": ZCODE_VERSION,
                         "description": ZCODE_DESCRIPTION,
                         "displayName": "TokenEconomizer",
                         "displayName_i18n": {"zh-CN": "TokenEconomizer"},
                         "description_i18n": {"zh-CN":
                             "跨会话学习仓库事实并在会话启动时注入;按 ZCode 自身的用"
                             "量表为每个会话记账;失败循环守卫。上游 microsoft/"
                             "TokenEconomizer 的 ZCode 移植,Apache-2.0。"},
                         "categories": ["developer-tools"],
                         "source": "./tokeneconomizer"}],
        }, fh, indent=2)
        fh.write("\n")
    return plugin_out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.path.join(REPO, "dist", "marketplace"))
    ap.add_argument("--droid", action="store_true",
                    help="write a Factory Droid marketplace instead "
                         "(default <out>: dist/tokeneconomizer-droid)")
    ap.add_argument("--zcode", action="store_true",
                    help="write a ZCode plugin bundle instead (default <out>: "
                         "dist/tokeneconomizer-zcode)")
    ap.add_argument("--zip", action="store_true",
                    help="also write <out>/../tokeneconomizer-plugin.zip of the plugin directory")
    args = ap.parse_args(argv)
    if args.droid and args.out == os.path.join(REPO, "dist", "marketplace"):
        args.out = os.path.join(REPO, "dist", "tokeneconomizer-droid")
    if args.zcode and args.out == os.path.join(REPO, "dist", "marketplace"):
        args.out = os.path.join(REPO, "dist", "tokeneconomizer-zcode")
    plugin_out = (build_droid(args.out) if args.droid else
                  build_zcode(args.out) if args.zcode else build(args.out))
    print(f"plugin bundle: {plugin_out}")
    print(f"marketplace:   {os.path.abspath(args.out)}")
    if args.zip:
        base = os.path.join(os.path.dirname(os.path.abspath(args.out)), "tokeneconomizer-plugin")
        path = shutil.make_archive(base, "zip", root_dir=os.path.dirname(plugin_out),
                                   base_dir=os.path.basename(plugin_out))
        print(f"zip:           {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
