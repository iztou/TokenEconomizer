# ZCode 移植用户验证清单(2026-11-12 写定;2026-11-13 第 2、3 轮更新)

实现已完成并通过离线测试:`python3 -m pytest tests` = **946 passed, 16 skipped, exit 0**
(含 `tests/test_zcode.py` 25 项)。第 2 轮(外部评审意见)新增:ZCode 的 **history import**
已实现并可离线验证(`te --host zcode history import` 从会话库列出并按需导出折叠;
`tests/test_zcode.py::test_history_import_learns_facts_and_prices_the_sessions` 等 3 项覆盖);
`session_usage` 与 `complete` 同源(都吃会话记录里的 `zcode_db` 覆盖,消除评审 low 项的不对称);
CLAUDECODE 问题已实测:ZCode 工具 shell **不设** `CLAUDECODE=1`(2026-11-13
`env | grep -c '^CLAUDECODE='` = 0),`te` 在 ZCode 内免 `--host` 识别为 zcode 成立。

## 活体验证结果(2026-11-13 全天真实使用,自动核对)

hook 活体行为**已经由真实使用证实**,不再是待办:

- **§0 hooks 启用** ✅:`~/.zcode/cli/config.json` 的 `{"hooks": {"enabled": true}}` 形状生效;
  每次会话启动的 `bootstrap.app.startup.plugins.completed` 报 `hookCount: 5`(插件自带的
  5 事件,与探针预测一致)。
- **§1 hook 触发与记录** ✅:SessionStart 写 `~/.tokeneconomizer/sessions/zcode/<sess>.json`;
  turn.phase 日志(`session_start_hooks`、`user_prompt_hooks`)带真实耗时;`te status` 的
  loopguard envelope 持续累加(两仓库合计 57 tool calls / 39 ended-green turns)。
- **§2 工具调用记录** ✅:上午的会话 events.jsonl 有 per-turn 的 tool_calls 计(12、11、6 等),
  编辑与验证计数也在(edits/verifications)。
- **§3 无 SessionEnd 的折叠链路** ✅:13 个会话被折叠导出
  (`~/.tokeneconomizer/zcode-sessions/`),成本全部来自 `model_usage` 表定价
  (TokenEconomizer 仓库 3 会话 15.4M、geovis-fire-product 仓库 10 会话 94.2M effective
  tokens);跨仓库识别(remote 定 key)正常,当前会话跨仓库工作时有 `note` 提示归属。
- **§4 输出形状** ✅(静态):`zcode.cjs` 的 hook 输出解析同时接受顶层 `additionalContext`
  与 Claude 嵌套 `hookSpecificOutput:{hookEventName, additionalContext}`(嵌套形要求事件名
  精确匹配,否则抛 "Hook returned wrong event name");`Zcode.context()` 出厂的嵌套形合法,
  无需改动。活体注入仍待 fact 清 firing gate(见下)。

**第 3 轮新增(2026-11-13 晚):side call 通道落地。** `zcode -p`(桌面 app 自带 CLI,
`/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs`)的无头单 prompt 模式实测可用:
`--json` 返回 `{response, usage{inputTokens, cacheReadTokens, ...}}`,`--disallowed-tools`
可整组移除工具。`sidecall.zcode_call` 已实现并接入 `runner_for`,受
`zcode_sidecall`(默认 off)或 `TE_ZCODE_SIDECALL=1` 控制——每次调用携带 ZCode 完整基础
提示词(实测 ~275k input tokens、无缓存),且会在 ZCode 会话库里留下自己的会话,故默认关。
真实端到端:proposer 形态的提问 9.3s 返回正确回答、275,253 effective tokens 如实入账。
本机已开 `zcode_sidecall=on`(全局)并将两个仓库升到 `microskills=envfacts+distiller`。

## 0. 前置:确认插件与 hooks 已启用

- 插件已装:`~/.zcode/cli/plugins/cache/local/tokeneconomizer/0.1.0/`
  (`package.json` + `hooks/hooks.json` 5 事件 + `lib/tokeneconomizer/` + `scripts/`)。
- 注册表:`installed_plugins.json` 有 `tokeneconomizer@local`;
  `known_marketplaces.json` 有 `local`;`config.json` 的
  `plugins.enabledPlugins["tokeneconomizer@local"] = true`。
- **hooks 总开关**(ZCode 默认禁用,唯一未定形的配置):在 `~/.zcode/cli/config.json` 设
  `"hooks": {"enabled": true, ...}`。探针试过扁平形(`{"enabled": true, <Event>: [...]}`)
  与嵌套形(`{"enabled": true, "hooks": {...}}`)各一次,均未触发(原因见上)。若启动后
  日志仍 `hookCount: 0`,再试另一种形状,或只留 `"hooks": {"enabled": true}` 让插件自带
  `hooks/hooks.json` 生效。**改前备份 `config.json`**(探针的还原核对已做:当前文件与
  原始备份逐叶 diff 仅剩插件启用一项)。

## 1. 会话启动:hook 触发与 SessionStart 注入

- 新开一个 ZCode 会话(任意仓库,建议先在一个 scratch 仓库)。
- 查 hook 执行记录:`~/.zcode/cli/log/zcode-<日期>.jsonl` 里
  `bootstrap.app.startup.plugins.completed` 应有 `pluginCount` 17→18、
  `hookCount` > 0;`~/.zcode/cli/log`/`~/.zcode/v2/logs` 出现本插件 `te-hook` 的执行痕迹
  (hook 超时/失败也会有记录)。
- 查插件运行痕迹:`~/.tokeneconomizer/` 出现 `hosts/zcode/`、`errors.log`(若有错)、
  `bin/te`。
- **facts 注入**:`te --host zcode status` 能看到状态;若该仓库已有 ≥1 次被折叠的会话且
  `microskills=envfacts`(默认 microskills=off,需 `te --host zcode config set
  microskills=envfacts` 打开),下次会话启动的上下文里应出现 "- use python -m pytest" 形态
  的 facts;`te --host zcode microskills` 可对照。

## 2. 一次工具调用:PostToolUse / 守卫

- 在会话里做一次普通 Bash 调用后,`te --host zcode status` 的会话记录应出现
  (`~/.tokeneconomizer/hosts/zcode/<repo>/sessions/<sess_id>.json` 的 `turns`)。
- 故意让同一失败重复出现可测守卫的 backstop 提示(judge 在本宿主关闭,只有规则 backstop)。

## 3. 无 SessionEnd 的折叠链路

- 结束上面那个会话,等 ≥10 分钟(静默阈值 `STALE_S=600s`),在同一仓库再开一个会话:
  `te --host zcode status` 应显示上一会话被折叠(ledger 有 `host=zcode` 的 session 行,
  成本来自 ZCode 的 `model_usage` 表);`~/.tokeneconomizer/zcode-sessions/<sess_id>.jsonl`
  是导出的会话副本。
- 对照测试(离线,已绿):`tests/test_zcode.py::test_a_session_whose_end_was_missed_is_folded_by_the_next_start`。

## 4. 输出形状(实测后回写)

- hook 若接受了 Claude 嵌套形 `hookSpecificOutput.additionalContext`,无需改动;
  若日志出现 schema 校验失败(多余 key/形状不符),把
  `tokeneconomizer/plugin/hosts.py` 的 `Zcode.context()` 翻成 Copilot 式顶层
  `{"additionalContext": text}`(一处方法),并把结论回写 `docs/ZCODE.md` 的 probe record。

## 剩余待验证环节(截至第 3 轮,2026-11-13 晚)

原 8 项活体清单,6 项已由真实使用/静态分析解决:

1. ~~插件 hook 实际触发~~ ✅ payload 字段集见 `docs/ZCODE.md` probe record(`session_id`/
   `cwd`/`hook_event_name`/`source`/临时 `transcript_path`),生产数据在两仓库累积。
2. ~~`additionalContext` 接受形状~~ ✅ 静态证实:顶层与嵌套都收;**`systemMessage` 不是
   "显示给人"的通道——它也被 push 进 additionalContexts(注入对话)**,故 `Zcode.notice()`
   出厂返回 `{}`、由模型转达(relays_notices)是正确设计,不要"修"成 systemMessage。
3. ~~`UserPromptSubmit` 注入是否真的进入对话~~ ✅ **活体注入验证完成(2026-11-13
   21:39,三方铁证)**。方法:4 个 `zcode -p --cwd <repo>` 学习会话(每轮静默 10 分钟
   后由下一轮 SessionStart 折叠;本机 `python` 不在 PATH、`pytest` 也不在,模型先踩
   `python -m pytest` 坑后自修正 `python3 -m pytest`,正是 form_repair 的教科书证据)
   + 第 5 个验证会话(明确禁止运行命令)。
   **证据 ①(TE 侧)**:`form_repair:run_tests:python -m pytest` 达 ACTIVE(fire 4/5,
   rate 0.8),`injection.json` 的 `rendered` 非空,`te microskills` 显示 4 facts
   注入、10 会话学习;distiller(proposer,经 `zcode -p` sidecall)同时生成了
   repo operating manual,plugin overhead 694.7k(1.5%)如实入账。
   **证据 ②(ZCode db 侧)**:验证会话在 `~/.zcode/cli/db/db.sqlite` 的首条
   `system_reminder` 消息就是 `"SessionStart hook additional context:"` + 注入手册
   原文——`additionalContext` 以 system_reminder 形态进入模型上下文,与 gitStatus
   等并列;当日日志 0 个 hook schema 错误(嵌套 `hookSpecificOutput` 形被接受)。
   **证据 ③(行为侧)**:被禁止运行任何命令的验证会话回答 "`python3 -m pytest`
   from the repo root (e.g. `python3 -m pytest tests/test_zcode.py -x -q` for one
   file)."——与注入手册逐字吻合,且不可能来自自行探索。
   复现脚本与全部输出:`/tmp/te-inject-verify/`(run.sh/run.log/verify.sh)。
   注:注入发生在 SessionStart(存续整个会话);UserPromptSubmit 的注入路径同 schema,
   TE 在此宿主 `prompt_context=False`(只在 SessionStart 注入),故无需单独验证。
4. ~~`hooks.enabled` 配置形状~~ ✅ `{"hooks": {"enabled": true}}` 生效(hookCount: 5)。
5. ~~`${ZCODE_PLUGIN_ROOT}` 展开~~ ✅ 已装副本的 5 条命令全部依赖它且在跑。
6. ~~local marketplace 被接受~~ ✅ 插件在列表(enabledPluginCount 13 / pluginCount 19)、
   hook 每次 SessionStart 解析。
7. 桌面 UI 的中断方式(loop guard 提示里 "how to stop" 措辞是 generic)——低优先,待
   第一次真实 nudge 时顺带观察。
8. ~~`te --host zcode history import` 对真实库跑一次~~ ✅(2026-11-13 晚,隔离状态根
   `TOKENECONOMIZER_HOME=/tmp/te-import-test` 实测,生产学习状态零影响):geovis 仓库
   **70 个真实会话 1.9s 导入**,`directory`→仓库匹配、`unsafe_id` 防御(42 个非
   `sess_` 形态 id 排除)、`already_folded` 拒绝条件、loopguard envelope 学习
   (278 个 ended-green turns)全部按设计工作。两个连带发现:
   **(a) 修复了一个真 bug**——db 里失败调用的文本在 `state.error` 而 `state.output`
   为 null,导出把失败文本丢了(每个失败 tool_result 都是空串,学习器看不见失败);
   `trace/zcode.py` 已改为失败时取 `state.error`,新增
   `test_a_failed_call_exports_its_error_text`,全量 947 passed。
   **(b) 实证 0 facts 是设计内行为**——观察器对 70 个真实会话产出 1600+ 条观察
   (7 类 intent),失败仅 18 条且无重复模式;envfacts 只学"反复出现的环境陷阱",
   这周的真实使用里没有。`te status` 报 0 facts 期间不是故障。
   注意:生产根上两个仓库已各有 folded 会话,import 的设计条件(仅冷启动、仓库尚无
   folded 时)不再满足——想"补学历史"需重置该仓库的学习状态后跑,或在新仓库首开时
   自动跑(`history_import=on`)。

## 评审状态(如实记录)

- **最终结论:approved(2026-11-13,第 2 轮)**。评审者按 `docs/ZCODE_PORT_REVIEW.md` 协议
  代审(dsh 不可用时改由 GLM-5.3-Flash 代审;scnet/dsh 命令曾于 2026-11-12 23:56 因配额
  429 失败,按协议的失败处理如实记录、未伪造结论)。
  - 第 1 轮 `changes_requested`,3 项:【medium】history import 未按计划 W3/W4 实现;
    【low】`session_usage` 与 `complete` 对 `zcode_db` 覆盖取用不对称;【low】
    `trace/zcode.py` 版本陈述 0.17.1 与其余文档 3.17.2 矛盾。另有 4 条建议(doctor 连接
    显式关闭、HOST_LABEL、探针第 6 问入清单、CLAUDECODE 实测)。
  - 第 2 轮全部处理(medium 补实现,low/suggestion 采纳),第 2 轮评审逐项核实后
    **approved**:评审依据含只读复测(pragma table_info 证实库列;496 个 session、
    parent_id 非空 186;`python3 -m pytest tests` 本轮未由评审者运行,941 passed/16 skipped
    采信开发者报告,且开发侧已独立复跑同数)。
  - `needsHuman` 事项即上方 8 项活体清单,由项目所有者在新开的 ZCode 会话里逐项确认。

## 回退/卸载

见 `docs/ZCODE.md` 的 "Uninstall and rollback":删插件目录、三个注册表条目、
`config.json` 的 enabledPlugins 键即可;`~/.tokeneconomizer` 是全部学习状态;ZCode 的
会话数据库只读,从未被写入。
