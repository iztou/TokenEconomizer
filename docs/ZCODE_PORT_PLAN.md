# ZCode 宿主移植实施计划(2026-11-12 制定)

> 本计划在动手改代码前写定,实现阶段遵守它;计划外的发现先补进本文件再动手。
> 收尾须经过 `docs/ZCODE_PORT_REVIEW.md` 的 dsh 单评审协议(pytest 全绿之后、宣布完成之前)。

## 实施偏离与补记(2026-11-13,第 2 轮评审后回写)

- **W3/W4 全部交付,含 history import。** 第 1 轮曾裁掉 W3 的 `zcode_log_head`/
  `zcode_prompt_turns` 与 W4 的 `keeps_history=True + history()` 且未按本节规则先补记——
  评审(medium)指出后已照计划补齐:`trace.zcode_sessions` 为底座,`Zcode.history()` 列出
  仓库无关的全部顶层会话(`parent_id` 子工作流会话剔除),`log_head`/`prompt_count`/
  `prompt_turns` 对未导出的会话**按需导出**为 Claude 形 JSONL(每行带 `cwd`/`sessionId`/
  ISO `timestamp`,故 `trace.claude_*` 读表原样可用),导出落在状态根 `zcode-sessions/`。
  离线覆盖:`test_history_lists_sessions_and_leaves_subworkflows_out`、
  `test_history_import_learns_facts_and_prices_the_sessions`。
- **W6 结构偏离**:ZCode 的清单是插件根的 npm 形 `package.json`(已装官方插件佐证),不是
  计划设想的 `plugin/.zcode-plugin/plugin.json`(该目录未创建);仓库根的 `plugin/package.json`
  是 Pi 的,故 `build_zcode` 在产物里直接生成 ZCode 专用 `package.json` 并写入
  `hooks/hooks.json`(五事件变体)。功能等价,偏离已在此补记。
- **W2 探针未完成活体触发**(计划内已述):hook 集在会话 bootstrap 一次性解析,本回合无
  新会话;输出形状等 flag 按默认实现(Claude 嵌套形),活体清单在 `ZCODE_VERIFY.md`(仓库根),
  含探针第 6 问(`${ZCODE_PLUGIN_ROOT}` 展开)。
- **第 2 轮评审采纳的其余项**:`session_usage` 与 `complete` 同源(基类签名加
  `session=None`,`fold` 传入会话记录,Zcode 读 `zcode_db` 覆盖);`trace/zcode.py` 版本陈述
  改为 app 3.17.2(注明库内 `session.version` 列为 runtime 的 0.17.1);doctor 的 zcode 探测
  显式关闭连接;`history.HOST_LABEL` 加 zcode。

## 0. 目标与范围

为 TokenEconomizer 新增第 11 个宿主 **ZCode**:micro-skills(含 LLM-free envfacts)、loop guard、
会话成本核算与 history import 可用;**工具裁剪不实现**(按 Pi/Droid 先例标记 not available);
插件自身的模型调用(side call)默认关闭(按 Antigravity 先例进 `NO_SIDE_CALLS`)。

硬约束(全程有效):

- `tokeneconomizer/` 包只允许 Python 标准库(sqlite3 是标准库,可用);
- **不改 microskills/toolprune/loopguard 机制层**(工具名别名映射只放在 `plugin/hosts.py` 的
  Zcode 适配器与 `trace/zcode.py` 读取器;`loopguard/state.py` 的 `_TOOL_CANON` 已覆盖
  `apply_patch→edit` 等,不需改动);
- 代码风格与周边宿主(Droid/Antigravity)一致;每个可见行为都像其他宿主一样失败时放行
  (`hook.py:92-94` 的 fail-open 已有,不重复造);
- 对 `~/.zcode` 的一切修改先备份;除最终保留的插件安装外,用户原有配置内容必须还原;
- 绝不 git commit / git push。

## 1. 事实基础

### 1.1 采信的任务给定事实(不再验证,直接作为设计输入)

1. 支持 7 个 hook 事件,PascalCase:`SessionStart` / `UserPromptSubmit` / `PreToolUse` /
   `PermissionRequest` / `PostToolUse` / `PostToolUseFailure` / `Stop`;**没有 `SessionEnd`**
   → 折叠走 Antigravity 先例:下次 `SessionStart` 时折叠上一会话
   (`hosts.py:802-819` 文档、`controller.py:196` 的 `fold.spawn_fold(... pending_sessions ...)`,
   静默阈值 `fold.STALE_S = 600s`,`fold.py:33`、`fold.py:549-572`)。
2. 插件 `hooks.json` 格式与 Claude Code 相同(`plugin/hooks/hooks.json` 即该格式)。
3. 模板变量支持 `${CLAUDE_PLUGIN_ROOT}` / `${ZCODE_PLUGIN_ROOT}` / `${CLAUDE_SESSION_ID}`。
4. 工具名 Claude 系:`Bash`/`Read`/`Write`/`Edit`;`Task` 与 `Agent` 互认;`Write`/`Edit`
   兼收 `ApplyPatch`。
5. hook 输出是**严格 JSON schema,多余 key 校验失败**;`additionalContext` 可注入对话;
   **顶层 `additionalContext` 还是 Claude 嵌套 `hookSpecificOutput.additionalContext`
   形状以 probe 实测为准**(W2 定夺,两种实现都有先例:Copilot 顶层 `hosts.py:1151-1152`,
   Claude 嵌套 `hosts.py:146-147`)。
6. 配置文件 hook 默认禁用,需 `hooks.enabled: true`(落点是插件清单还是用户配置,W2 探针确认)。
7. 会话数据在 `~/.zcode/v2/db.sqlite` 与 `~/.zcode/cli/exec/sess_*`。
8. hook 执行记录在 `~/.zcode` 的日志里(已在本机日志观察到:`~/.zcode/v2/logs/2026-11-12.log`
   与 `~/.zcode/cli/log/zcode-2026-11-12.jsonl` 各有数百条 `phase:"hooks"` /
   `turn.phase` 记录,以及 `bootstrap.app.startup.plugins.completed` 条目)。
9. ZCode 对 MCP 工具已做懒加载 → 工具裁剪在本宿主不实现,状态标注同 Pi/Droid
   ("nothing (not available)")。

### 1.2 本机只读勘察证据(2026-11-12,本计划制定时实测)

- `~/.zcode/v2/db.sqlite` **为 0 字节**(任务给的路径在本机不承载会话);真正的库是
  `~/.zcode/cli/db/db.sqlite`,含 487 个 session 行。读取器按"两处都探测 + 环境变量覆盖"
  设计(W3),以 `cli/db/db.sqlite` 为缺省主库。
- `cli/db/db.sqlite` 关键表(已用 `sqlite3 ... mode=ro` 读 schema):`session(id, project_id,
  directory, title, version, …)`、`message(session_id, data, sequence)`、
  `part(message_id, data)`、`model_usage(session_id, model_id, input_tokens, output_tokens,
  reasoning_tokens, cache_read_input_tokens, cache_creation_input_tokens, …)`、
  `turn_usage(session_id, turn_id, status, …)`、`tool_usage(session_id, tool_name,
  status, exit_code, …)`。
- `message.data` 为 JSON:`role`、`tokens{input,output,reasoning,cache{read,write}}`、
  `modelId`、`providerId`、`path.cwd`、`anchor.turnId`;`part.data` 含
  `{"type":"tool","callID","tool","state":{"status","input",...}}`。
- 实测工具名分布(Claude 系证实):`Bash`(10145)、`Edit`、`Read`、`WebFetch`、`Write`、
  `TodoWrite`、`ToolSearch`、`AskUserQuestion`、`Skill`、`mcp__*`。
- `~/.zcode/cli/exec/sess_*/call_*-stdout.log`(123 个文件)是每次工具调用的 stdout 日志,
  仅作佐证,读取器不依赖它。

### 1.3 既有宿主的接线点(全部已读,新宿主照此接入)

| 接线点 | 位置 | 新宿主要做的事 |
|---|---|---|
| Host 适配器 | `tokeneconomizer/plugin/hosts.py`(`HOSTS` 表在 1320-1322) | 新增 `Zcode(Host)` 类 |
| 事件分发 | `controller.py:84-88` `handler()`(六事件已映射) | 零改动:没有 SessionEnd 就永远不走 `session_end` |
| 下次启动折叠 | `controller.py:196` + `fold.pending_sessions`(549-572)+ `STALE_S`(33) | 零改动(机制既有) |
| detect/default_host | `hosts.py:1348-1380`、`hosts.py:1325-1345` | 加 payload/env 分支 |
| CLI `--host` | `cli.py:916,921`(`choices=sorted(hosts.HOSTS)`) | 自动生效,零改动 |
| doctor agents 列表 | `cli.py:818-823` | 手工列表里加 `"zcode"` |
| 裁剪支持判断 | `denylist.py:76-77 supports()`(查 `_SETTINGS`,56-57) | 零改动:`"zcode"` 不在表内 → `_fold_toolprune` 直接返回 None(`fold.py:125-126`),加测试钉住 |
| side call 白名单 | `sidecall.py:752 NO_SIDE_CALLS` | 加 `"zcode"`;同时规避 `test_live_core.py:1002-1005`(要求每个有 side call 的宿主有 `<name>_paid_model` 设置) |
| trace 读取器 | `trace/__init__.py`(导入表)+ 新模块 `trace/zcode.py` | 参照 `trace/droid.py`(33-41 工具名映射、232+ usage、299 环境变量覆盖) |
| 插件打包 | `scripts/build_plugin.py`(droid 先例 58-120) | 加 `--zcode`(产物 `dist/tokeneconomizer-zcode`) |
| hook 文件 | `plugin/hooks/hooks.json`(Claude 形状)、`plugin/hooks/droid-hooks.json`(每事件 `${X_PLUGIN_ROOT}` + `--host`) | 新增 `plugin/hooks/zcode-hooks.json` |
| 宿主清单 | `plugin/.factory-plugin/`、`plugin/.antigravity-plugin/` 先例 | 新增 `plugin/.zcode-plugin/plugin.json`(含 `hooks.enabled: true`,W2 确认落点) |

## 2. 设计决策(定案)

- **D1 折叠时点**:不注册 SessionEnd(也没有);会话在 Stop 关 turn,落库后由下一次
  `SessionStart` 在 transcript 静默 `STALE_S`(600s)后折叠。与 Antigravity 完全同构,
  控制器零改动。
- **D2 注册的 hook 事件只有 5 个**:`SessionStart`、`UserPromptSubmit`、`PostToolUse`、
  `PostToolUseFailure`、`Stop`。`PreToolUse` 与 `PermissionRequest` 不注册:插件的两个机制
  都不需要它们(守卫在 PostToolUse/Failure 折叠调用),少注册是严格 schema 下最小暴露面
  (Antigravity 因 PreToolUse 空输出被拒工具的前车之鉴,`hosts.py:808-809`)。
- **D3 输出形状待探针**:默认按 Claude 嵌套 `hookSpecificOutput` 实现;若 W2 实测为顶层
  `additionalContext`,只改 `Zcode.context()`(及 render 若需要),一处方法,不影响机制。
  严格 schema 下只输出白名单 key;`systemMessage` 未经证实前,`Zcode.notice()` 返回 `{}`
  且 `relays_notices = True`(Droid 先例,`hosts.py:1251-1255`),提示语经模型转述。
  不使用 Copilot 的 PROGRESS 前置行通道(严格 JSON 下有整包被弃风险)。
- **D4 turn 身份**:payload 若带 turn/prompt id 则用作 `turn_key`;没有则整轮从 prompt 到
  Stop 保持打开(grok 先例,`hosts.py:992-994`),`UserPromptSubmit` 开 turn、`Stop` 收。
- **D5 工具名归一**(宿主层,不碰机制):`Zcode.tool_call()` 与 `trace/zcode.py` 的
  `_TOOL_NAMES` 把 `ApplyPatch`→按参数形状归入 `Write`/`Edit`、`Agent`→`Task`;
  `Bash/Read/Write/Edit` 原样透传(loopguard 的 `_TOOL_CANON` 再归一遍,链条已存在,
  `state.py:38-50`)。`Task`/`Agent` 均在 `INTERACTIVE_PROTECTED`(`adapter.py:42`)。
- **D6 会话库读取**:只读 URI(`file:...?mode=ro`)打开 sqlite;`~/.zcode/cli/db/db.sqlite`
  为主、`~/.zcode/v2/db.sqlite` 非空则参与,`ZCODE_HOME` / `ZCODE_DB_PATH` 环境变量可覆盖
  (先例 `DROID_SESSIONS_DIR`,`trace/droid.py:299`)。库锁住或不可读 → 该次 fold 记
  "transcript unreadable" 入 ledger(`fold.py:493` 既有路径),不崩、不重试致死。
- **D7 side call 关闭**:`NO_SIDE_CALLS` 含 `"zcode"`(ZCode 无已知的"关掉全部工具跑一个
  prompt"的无会话残留入口;Antigravity 同款决定)。付费提议者与评审者等待,envfacts 与
  规则 backstop 不受影响。
- **D8 裁剪 not available**:`denylist.supports("zcode")` 恒 False(零代码),README 表格
  ZCode 行写 "nothing (MCP tools are lazy-loaded; not available)",同 Pi/Droid 行文。

## 3. 工作项(顺序即执行顺序)

### W1 只读复核会话数据源(动手前快照)
- 内容:实施当天重跑 1.2 的勘察命令(两处 db 路径、表清单、`sess_*` 文件数),把最新事实
  写进 W9 的 `docs/ZCODE.md` "探针记录" 节。
- 产出:`docs/ZCODE.md` 初版(事实一节)。
- 验证:命令输出与 1.2 一致或差异被记录。
- 回退:纯只读,无需回退。

### W2 活体 hook 契约探针(throwaway 插件;`~/.zcode` 备份先行)
- 内容:先 `cp -a ~/.zcode <备份目录>/zcode-backup-<时间戳>`(至少覆盖
  `~/.zcode/cli/config.json`、`~/.zcode/cli/plugins/`、`~/.zcode/v2/config.json`、
  `setting.json`、`acp-config`);然后在仓库外临时目录写一个最小探针插件(hooks.json 为
  Claude 形状,5 个事件各一条命令,把 stdin payload 原样追加到探针日志文件),安装为
  ZCode 插件,跑两个短会话触发各事件;探针回答六问:
  1) payload 键集(`session_id`?`cwd`?`transcript_path`?`hook_event_name`?turn/prompt id?
     子代理标记?);
  2) `additionalContext` 生效形状:顶层还是嵌套 `hookSpecificOutput`;
  3) `systemMessage` 是否对人可见;
  4) `hooks.enabled: true` 的落点(插件清单 vs 用户配置文件)与确切字段路径;
  5) 空输出 `{}` 与未注册事件是否安全;
  6) `${ZCODE_PLUGIN_ROOT}` 在该安装形态下是否被展开。
- 产出:探针日志 + `docs/ZCODE.md` "hook 契约实测" 节(逐问记录证据)。
- 验证:探针日志文件 + `~/.zcode` 日志里的 hook 执行记录互证;W4/W6 按结论定稿。
- 回退:卸载探针插件;用备份还原全部被改的 `~/.zcode` 配置文件(逐文件 diff 校验还原);
  探针产物只留在仓库 docs,不在 `~/.zcode` 留任何东西。
- 备注:探针脚本与临时插件一律放在仓库外新目录(如 `/tmp/te-zcode-probe/`),不进 `~/.zcode`。

### W3 trace 读取器 `tokeneconomizer/trace/zcode.py`
- 内容:`zcode_db_paths(env)`(两库探测 + 环境覆盖)、`zcode_sessions(env)`(按 mtime/最近
  会话列 `{path→db, id, directory, started, mtime, ...}`)、`zcode_log_head`、
  `zcode_safe_session_id`、`zcode_session_to_claude(path, out_path)`(message+part →
  Claude 形 JSONL,工具名按 D5 归一,每行带 `path.cwd`)、`zcode_prompt_count`、
  `zcode_prompt_turns`、`zcode_session_usage`(优先 `model_usage` 聚合,缺则 `message.tokens`
  兜底;模型取众数 `model_id`,未知名交给既有 fallback 定价)。只读 URI,`OperationalError`
  按不可读处理;返回 None/空遵循各宿主读取器既有约定。在 `trace/__init__.py` 登记导出。
- 产出:`trace/zcode.py` + `trace/__init__.py` 导出行。
- 验证:`tests/test_zcode.py` 用 tmp_path 里现造的 sqlite 库(建表语句取自真实 schema 子集)
  覆盖:正常转换、无会话/锁库/坏 JSON 的失败路径、prompt 计数忽略注入文本、usage 聚合与
  兜底、环境变量覆盖。
- 回退:删模块 + 删导出行即可;W4 之前没有任何调用方。

### W4 Host 适配器 `Zcode`(hosts.py)
- 内容:按 1.3 接线表加 `Zcode(Host)`:`session_id`/`transcript`/`cwd`/`turn_key`(D4)、
  `tool_call`(D5 别名)、`context`/`notice`/`relays_notices`(D3,按 W2 结论定稿)、
  `is_subagent`(W2 结论;无标记则默认 False 并记录)、`headless`(`False`,Droid 先例)、
  `mechanism_trace`/`prompt_count`/`session_usage`(转发 W3)、`keeps_history=True` +
  `history()`/`log_head`/`safe_session_id`/`prompt_turns`/`folder_matches`(history import,
  Droid 段先例 `hosts.py:1292-1317`)、`env_session_id`(读 `CLAUDE_SESSION_ID` 等探针确认的
  变量)、`HOSTS["zcode"]` 注册、`default_host` 的 env 标记、`detect()` 的 payload 分支
  (`sess_` 前缀 / `~/.zcode` transcript,放Codex/Droid 判据之后、Claude 兜底之前,顺序理由
  写注释)。同时 `cli.py` doctor 的 agents 列表加 `"zcode"`。
- 产出:`hosts.py` 新类与三处小改;`cli.py` 一行。
- 验证:`tests/test_zcode.py` 补:payload→控制器事件、别名映射、detect/default_host、
  `HOSTS` 注册、`history()` 列表与子会话过滤(如 W2 发现有 parent 标记)、无 SessionEnd 时
  `pending_sessions` 折叠路径(TE_FOLD_SYNC=1 风格,参照 `test_antigravity.py`)。
- 回退:revert `hosts.py`/`cli.py` 的 hunks;`tests/test_zcode.py` 同批删除。

### W5 机制开关与 not-available 标记
- 内容:`sidecall.NO_SIDE_CALLS` 加 `"zcode"`(D7);`tests/test_zcode.py` 钉住
  `denylist.supports("zcode") is False`、`"zcode" in sidecall.NO_SIDE_CALLS`、
  runner_for 返回 None、status/doctor 显示。
- 产出:一行机制配置 + 断言测试。
- 验证:新测试 + 全量 `python -m pytest tests`(W8 前先单跑本文件)。
- 回退:revert 一行;测试同批删。

### W6 插件打包(`plugin/.zcode-plugin/` + zcode-hooks.json + build_plugin.py)
- 内容:`plugin/hooks/zcode-hooks.json`(Claude 形状、5 事件、命令
  `"${ZCODE_PLUGIN_ROOT}/scripts/te-hook" --host zcode`,超时对齐 hooks.json 的
  15/20/60/60/10);`plugin/.zcode-plugin/plugin.json`(按 W2 结论含 `hooks: {enabled: true}`
  及其它必需字段,没有就记待定);`build_plugin.py` 加 `--zcode`(droid 先例:拷
  `plugin/`→`lib/tokeneconomizer`,排除其它宿主清单与 hook 文件,把 zcode-hooks.json 放到
  探针确认的 hooks.json 读取位置)。命令入口按 W2 结论:支持斜杠命令则复用
  `slash-commands/`(替换 `${CLAUDE_PLUGIN_ROOT}`),否则照 agy 只给终端 `te --host zcode`。
- 产出:三个新文件/目录 + `build_plugin.py` 一段。
- 验证:打包测试(参照 `test_droid.py:302 test_droid_plugin_packaging`):构建产物里
  manifest/hooks 存在且 JSON 可解析、无其它宿主清单、lib 包完整;本机手动
  `python scripts/build_plugin.py --zcode` 跑通。
- 回退:revert build 脚本段,删新文件;不影响任何既有宿主的打包路径(--droid 分支不动)。

### W7 安装与活体验证(`~/.zcode`,备份先行)
- 内容:W2 备份仍有效;安装 W6 产物为正式插件,按 W2 结论置 `hooks.enabled: true`(若落在
  用户配置,修改前后逐文件留 diff);在一次性 scratch 仓库跑真实会话验证五点:
  1) SessionStart 注入学到的事实(W2 或预置一条 fact),
  2) PostToolUse/Failure 被守卫折叠、events.jsonl 有记录,
  3) Stop 关 turn、ended-green 喂 envelope,
  4) 结束会话静默 600s 后,同仓下一个会话的 SessionStart 把它折叠(`te --host zcode status`
     可见 ledger 行),
  5) `~/.zcode` 日志出现本插件的 hook 执行记录,`~/.tokeneconomizer` 状态目录只新增预期文件。
- 产出:`docs/ZCODE.md` "活体验证" 节(逐点命令+输出)。
- 验证:上述五点逐一给出可复现命令与观察结果;未通过的如实记为未通过。
- 回退:卸载插件;还原 `hooks.enabled` 等所有被改配置(与备份 diff 校验);scratch 仓库删除。
  (按任务约定,最终保留的插件安装本身可以留下,其余一切还原。)

### W8 测试补全与全量回归
- 内容:`tests/test_zcode.py` 定稿(读者、宿主、开关、打包四组,正常路径+失败路径);
  跑全量 `python -m pytest tests`;确认 `tests/test_plugin_package.py` 边界
  (机制不 import plugin、包内不 import experiments)原样绿;`scripts/dead_code.py` 无新告警
  (如仓库流程要求)。
- 验证:`python -m pytest tests` 全绿(以本命令为准)。
- 回退:测试缺陷随对应实现项一起 revert。

### W9 文档
- 内容:README.md(支持列表、Everyday commands 表、What-it-does 表 ZCode 行
  "nothing (not available)"、Supported versions 行"未实测版本号如实写")、
  plugin/README.md 新增 "ZCode" 一节(照 Antigravity/Droid 节结构:安装、检查、
  works/does-not、无 SessionEnd 的折叠时点、side call 关闭)、docs/PLUGIN.md 增补 ZCode
  验证记录(引 W2/W7 证据)、docs/ZCODE.md 定稿(探针记录+活体验证+回退手册)。
- 验证:文档内链接可达;每个行为描述都有 W2/W7 证据或标注"未实测"。
- 回退:revert 文档提交内容(不 commit,工作区可逆)。

### W10 评审与收尾(按 docs/ZCODE_PORT_REVIEW.md)
- 内容:`python -m pytest tests` 全绿后,按评审协议跑 dsh headless 单评审(scnet 模型、
  只读、20 分钟上限、简报按模板、轮次上限 3);`changes_requested` 按 items 修
  (medium/high 必改,low 记录);`needsHuman=true` 或评审工具失败时如实记录不阻塞收尾;
  最后写 `ZCODE_VERIFY.md`(验证记录+未获批条目+还原核对清单)。
- 验证:评审 JSON verdict 按协议解析;ZCODE_VERIFY.md 落盘。
- 回退:评审无法完成时保留工作区改动、如实交付,绝不伪造 approved。

## 4. 风险与回退汇总

| # | 风险 | 回退 |
|---|---|---|
| R1 | 输出形状/可见通道与实现假设不符(W2 才定) | 形状只收敛在 `Zcode.context()/render()` 一处;默认 Claude 嵌套,实测顶层即切 Copilot 式;`notice` 在证实前返回 `{}` 走模型转述 |
| R2 | 严格 schema 弃整包(多余 key、PROGRESS 行) | Zcode 输出白名单 key、不用 PROGRESS;`hook.py` fail-open 兜底 |
| R3 | 会话库位置与任务描述不符(本机 `v2/db.sqlite` 0 字节,`cli/db/db.sqlite` 才有数据) | 读取器双路径探测 + `ZCODE_DB_PATH` 覆盖;读不到按 "transcript unreadable" 记 ledger,不崩 |
| R4 | sqlite 被 ZCode 持锁/忙 | 只读 URI + 单次读取失败即放弃该次 fold,等下次 SessionStart 重试(既有 pending 机制) |
| R5 | 无 SessionEnd 导致折叠延迟(最长静默 600s + 下次启动) | 接受为宿主特性(Antigravity 同款),文档写明;不做 Stop 后 spawn fold 的计划外优化 |
| R6 | `hooks.enabled:true` 落点不确定 | W2 探针先定落点再动 `~/.zcode`;无论落点都先备份、后 diff 还原 |
| R7 | side call 入口未知,误开有残留风险 | 进 `NO_SIDE_CALLS`(Antigravity 先例);付费层等待,free 层完整 |
| R8 | 新宿主名牵连既有泛化测试(如 paid_model 断言,`test_live_core.py:1002`) | 由 `NO_SIDE_CALLS` 规避;W8 全量回归把关,出问题 revert W4/W5 注册行 |
| R9 | PreToolUse/PermissionRequest 行为未知 | 不注册这两个事件(D2 最小面),文档记录"未使用" |
| R10 | 工具别名映射写进机制层违反约束 | 映射只放 `Zcode.tool_call()` 与 `trace/zcode.py`;机制层 `_TOOL_CANON` 不动,W8 边界测试把关 |
