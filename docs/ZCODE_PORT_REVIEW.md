# ZCode 移植审核协议(用户 2026-11-12 指定)

> **用户 2026-11-12 追加指令(最高优先级)**:执行期间任何问题都不许问用户,以"ZCode 能用"为目标自主推进。遇到歧义做最稳妥的决定并在交付报告记录;下文所有"等用户/等你决策"一律改为"如实记录后继续完成其余收尾"——但不许因此伪造 approved 或跳过如实汇报。
>
> **用户 2026-11-12 深夜再追加**:**评审不通过就继续修改再评,直到通过为止**。轮次不再停在 3;12 轮是防死循环的安全上限(触顶说明评审与实现陷入矛盾,如实上报人工仲裁)。needsHuman 只记录不阻塞;dsh 工具单次失败同轮重试,连续 3 次失败才认输。
>
> **用户 2026-11-13 凌晨指令**:dsh 额度不足(429/配额类错误)时**不再等待**——用 GLM-5.3-Flash 子代理代行同一评审(同一简报、同一 ReviewVerdict 契约、同样只读纪律),并在轮次表与交付报告里如实标注「Flash 代审」;dsh 可用时仍优先 dsh。

移植工作(pytest 全绿之后、宣布完成之前)必须经过本协议的外部评审。评审只有 **1 个评审者:dsh headless 走 scnet 模型**(复用 RShell `m1-dev-review.dwf.ts` 的机制,双视角裁成单视角;Kimi/Codex 不参与)。

## 评审命令(已金丝雀验证可用,2026-11-12)

```sh
# cwd 必须是本仓库根目录(评审者要读仓库文件与 git diff)
dsh headless \
  --patch /Users/long/.dsh/patches/scnet-review.yml \
  --permission-mode read-only \
  --approval-policy never \
  "<评审简报>"
```

- 模型由补丁固定为 scnet 订阅的 `DeepSeek-V4.1-Flash-1017-Event`,不要另行指定。
- 只读沙箱 + 永不审批,评审者不得改文件;超时上限 20 分钟。
- 输出解析两次机会:第一次没解析出 JSON,在简报末尾追加
  `\n\n(注意:你上一轮输出末尾没有可解析的 JSON 围栏块,本次必须以符合上述类型的 \`\`\`json 围栏块结尾重发。)`
  再跑一次;两次都失败视为评审失败,按下面"失败处理"办。

## 评审简报模板(按 TokenEconomizer 约束改写自 RShell)

```
你是 TokenEconomizer 项目的代码评审员。你的视角是正确性与安全,单评审。
评审依据是仓库 README.md、docs/ARCHITECTURE.md、plugin/README.md 的约束,以及 docs/ZCODE_PORT_PLAN.md(实施计划,本轮实现应与之一致)。
任务背景:对"ZCode 宿主支持"的当前工作区实现做第 <N> 轮评审。本轮开发者报告:<DevReport JSON>。
固定检查(python -m pytest tests)本轮已全部通过。
步骤:先读 docs/ZCODE_PORT_PLAN.md 与 docs/ZCODE_PORT_REVIEW.md,再用 git diff --stat 看改动范围并读相关源码与测试,然后找会出问题的地方。
重点:tokeneconomizer 包必须标准库 only;microskills/toolprune/loopguard 机制层不得被改动;新增的 ZCode Host 与 trace reader 行为须与 probe 实测的 ZCode hook 契约一致;不得破坏既有 10 个宿主的行为与测试;对 ~/.zcode 的一切修改须有备份且用户原有配置被还原;新测试覆盖正常路径与失败路径。
纪律:这是只读评审——不要编辑、创建或删除任何文件,不要 git commit/push,不要运行完整测试套件(流程已统一运行);可以自由读文件、git diff/log、grep。
判断标准:批准要有证据并写进 basis;宁可要求修改也不要放行存疑代码;low 级风格意见放进 suggestions,不要据此给 changes_requested。
只有项目所有者能拍板的事(重开 ZCode 会话验证、真实 hook 触发确认等)把 needsHuman 置 true 并在 humanNote 写清。
输出要求:正文两三句写你实际读过的东西,最后必须以一个 ```json 围栏块结尾,内容严格符合如下 TypeScript 类型(字段名一字不差):
{ verdict: "approved" | "changes_requested"; items: Array<{ where: string; problem: string; reason: string; severity: "low"|"medium"|"high" }>; suggestions: string[]; basis: string; needsHuman: boolean; humanNote: string }
approved 时 items 为空数组;reason 引用具体约束或运行行为。
```

## 结论解析(RShell 踩过的坑,原样照搬)

取**最后一个**可解析的 ```` ```json ```` 围栏块,围栏标记必须锚定行首(评审者正文里会提到"```json 围栏块"字样,不锚定行首会误吞)。正则:

```js
/(?:^|\n)[ \t]*```json[^\n]*\n([\s\S]*?)\n[ \t]*```/g
```

校验 `verdict ∈ {"approved","changes_requested"}` 且 `items` 是数组,否则尝试更早的围栏块。

## 循环规则(与 RShell 一致,裁为单评审)

- 每轮顺序:实现/按意见修改 → `python -m pytest tests` 全绿 → dsh 评审。
- `approved`:评审通过,可进入收尾(写 ZCODE_VERIFY.md、文档)。
- `changes_requested`:按 items 修改(medium/high 必须改,low 记入报告)后进入下一轮。
- `needsHuman=true`:只把 humanNote 记入待办,**不阻塞循环**,继续修改再评。
- 轮次策略(用户 2026-11-12 深夜指令):**直到 approved 为止**;12 轮真实评审为防死循环安全上限,触顶如实上报(评审与实现陷入矛盾,需人工仲裁),不许伪造 approved。
- 评审命令本身失败(非 changes_requested)不占评审轮次,同轮重试;连续 3 次失败才认定"评审未能完成",如实记录——不许因评审工具故障而伪造 approved。

## 失败处理

scnet 补丁与命令已于 2026-11-12 实测(pong 金丝雀,exit 0)。若半夜出现凭据/配额类错误:不要换模型、不要换评审者,如实记录并继续完成其余收尾,交付时写明原因。

## 评审结果补记(2026-11-13)

- **最终结论:approved(第 2 轮,2026-11-13)**。pytest 941 passed / 16 skipped。
- 评审者:GLM-5.3-Flash 代审 —— dsh+scnet 在整个评审窗口内均不可用(2026-11-12 23:56 起 429 配额,后转 403「The current model does not support Token Plan」,05:21–05:22 三次金丝雀复测仍 403),按上方追加指令切换代审并如实标注。
- 第 1 轮 changes_requested(1 medium + 2 low):history import 未按计划 W3/W4 实现(已补齐:hosts.py keeps_history/history/log_head/prompt_turns,trace/zcode.py zcode_session_directory/_iso,新增 3 项测试);session_usage 签名双库覆盖不对称(已统一);版本陈述失实(已改为 app 3.17.2 / runtime 0.17.1,sqlite3 -readonly 实测佐证)。
- 第 2 轮 approved,依据详见交付报告与 ZCODE_VERIFY.md「评审状态」节;3 条 low 级建议(未独立复核)记录于交付报告 findings。
