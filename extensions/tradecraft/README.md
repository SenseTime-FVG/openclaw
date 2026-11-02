# TradeCraft

TradeCraft 是一个本地运行、结果确定的多智能体资源游戏，供三名或四名自主的 OpenClaw 玩家对战。整个环境由三部分组成：一个 Python 游戏引擎、一个 OpenClaw 工具插件，以及一个可选的对局运行器（match runner）。运行器负责创建相互隔离的玩家配置、启动一个共享 Gateway（每名玩家一个 agent），并串行调度 LLM 回合。

本插件是一个评测与实验环境。RL 训练集成（reward model、dataset、rollout）位于另一个仓库（`verl-tradecraft`），不在本插件内；本插件不附带任何模型权重。

设计动机、训练目标、reward 规格与开发路线见 [docs/DESIGN.md](docs/DESIGN.md)；本文是实现规格的唯一来源。

## 架构

| 层次            | 实现                                 | 职责                                                                                                                                      |
| --------------- | ------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------- |
| 对局编排        | `python/game_runner.py`              | 创建玩家配置、启动进程、调度 agent、投递消息、重试瞬时失败，并写出对局产物。                                                              |
| OpenClaw 运行时 | 一个共享 Gateway，每名玩家一个 agent | 运行所选模型，并且只暴露四个 TradeCraft 工具。                                                                                            |
| 工具插件        | `index.ts` 与 `src/tools.ts`         | 注册按 agent 生成工具工厂、从 agent/session 上下文解析出调用方玩家、用 TypeBox 校验面向模型的参数，并把引擎响应转换成 `AgentToolResult`。 |
| 协议适配层      | `src/client.ts`                      | 为每名玩家维护一条已注册的 Unix domain socket 连接，并串行化 NDJSON 请求。                                                                |
| 游戏引擎        | `python/engine.py`                   | 持有权威状态机，校验每一个动作，裁决交易，产出资源，并对对局计分。                                                                        |
| 棋盘生成器      | `python/map_generator.py`            | 依据种子构建确定性的 hex、vertex、edge 拓扑。                                                                                             |

请求路径如下：

```text
LLM 玩家
  -> TradeCraft 工具
  -> TypeScript 插件
  -> 已注册的 Unix socket 连接
  -> Python 引擎
  -> 状态快照与工具结果
  -> LLM 玩家
```

所有玩家都由**单个 Gateway 进程**托管。该进程为每个座位运行一个 OpenClaw agent（`P0`..`PN`，各自拥有独立 workspace），并使用形如 `agent:P<id>:tradecraft-<game>` 的按座位 session key。插件把四个工具注册为按 agent 生成的工厂，因此一次工具调用会被路由到调用方座位自己的引擎连接——这个映射从 agent/session 运行时上下文解析，而不是来自 Gateway 级别的静态配置。把所有座位放进同一个 Gateway 托管，可以避免多个 Gateway 共享一个 vCPU 很少的沙箱时出现进程调度饥饿。

运行器通过原子写入的 `state.json` 快照观察引擎。非聊天类的玩家消息（`kind` 为 `system` 或 `trade`）由引擎保存，随后由运行器通过 `chat.inject` 注入到接收方的 OpenClaw session。聊天流量**不会**被注入：收到的 `send_message` 与每一次 `propose_trade` 都在发起方自己的工具调用内部同步完成——引擎唤醒响应方，等待它做出单轮决策，应用该决策并返回结果。因此一笔交易的生命周期不会超过发起它的那次调用：快照中不存在 pending-trade 状态，响应方的任何失败（缺少配置、子进程错误、输出无法解析、超时）都会被作为拒绝上报。引擎仍然并行服务所有玩家连接：阻塞式的响应方等待只占用发起方自己的连接线程，从不占用共享的 I/O 循环。

每次唤醒都会注入玩家的动态状态，以及自该玩家上一次回合以来发生的、会影响状态的各类事件（建造、交易结算、项目认领、产出掷骰），最新的排在最后。玩家自己的动作与聊天消息会被排除。静态地图拓扑存放在玩家 workspace 的上下文中，不会每次唤醒都重发。

## 游戏模型

- 三名或四名玩家。
- 一个确定性的 19 格棋盘，包含 54 个顶点与 72 条边。
- 蛇形轮流摆子阶段：`P0 -> ... -> PN -> PN -> ... -> P0`。
- 十二个产出回合。每名玩家每回合在自己的第一次调度唤醒时掷一次 `2d6`；每次掷骰的产出会记给所有在该骰点格上有建筑的玩家。每名玩家每回合被调度一次，每次唤醒一个动作；没有有效动作可做的玩家直接结束回复、不调用任何工具（不存在 pass 工具）。
- 五种标准资源（`wood`、`brick`、`sheep`、`wheat`、`ore`）。
- 可选的固定队伍：`teams` 配置会把玩家 id 分组为联盟；结算后的状态会在个人分数之外一并报告 `teams`、`team_scores` 与 `team_winner`。
- 道路、定居点、两级升级、双边交易、公共项目，以及最终计分。

种子控制骰子序列（同时也是地图种子的回退值）；可选的独立 `map_seed` 控制资源布局与格子数字。地图生成总会施加平衡约束（相邻格资源不相同；红点数 6 与 8 从不相邻）。复用同一个种子能让引擎行为可复现，但 LLM 的决策仍可能不同。

### 工具

| 工具                       | 回合规则                                                                    | 动作消耗 |
| -------------------------- | --------------------------------------------------------------------------- | -------- |
| `tradecraft_build`         | 布阵阶段受摆子门槛约束；其余阶段为 play 阶段行为。                          | 1        |
| `tradecraft_propose_trade` | 仅 play 阶段；会阻塞直到响应方作答；每次唤醒最多 3 次。                     | 0        |
| `tradecraft_send_message`  | 仅 play 阶段；向指定的一名玩家发送定向私聊（免费步骤）；每次唤醒最多 3 条。 | 0        |
| `tradecraft_claim_project` | 仅 play 阶段；项目必须未被认领且满足条件。                                  | 1        |

这四个工具是唯一面向模型的工具。引擎也实现了 `view_game_state`，但由运行器调用它，并把结果状态注入到每次唤醒消息中；玩家没有可以自行请求状态的工具。（更早的版本曾暴露一套更大的工具集，包含 `pass` 与 `respond_trade`，现已不存在。）

`tradecraft_send_message` 是一个定向（私密）协商步骤，其*机制*与 `tradecraft_propose_trade` 相同：它指定单个 `target`，只把自由格式的 `content` 投递给该玩家，并且它是**阻塞式**的——引擎会一直保持发起方的 `send_message` 调用不返回，把目标玩家作为聊天响应方恰好唤醒一次，并在同一个唤醒内把该回复作为 `send_message` 的工具结果返回给发起方。但与交易提议不同的是，它是一个**免费步骤**：它**不**消耗本次唤醒的动作，因此玩家可以在一次唤醒里询问多个玩家，然后再执行自己那一个动作，也可以在动作完成之后再使用它（每次唤醒最多 3 条，超出会被拒绝）。这正是预期的流程——先协商（免费，可以问多个玩家）以收集信息，再落到一个动作上。由于它仍然要花费一次完整的响应方调用，应当避免含糊或重复的消息。要结束回合，玩家只需完全停止调用工具——不存在 pass 工具；不带工具调用地结束回复，就把控制权交还给运行器。

`target="system"` 表示银行/港口兑换，而不是玩家间交易：它在同一次调用内部按固定比例（`4:1`，若在港口顶点上有建筑则为 `3:1`）完成或拒绝。不会唤醒任何响应方，也不保留任何状态，因此这条路径永远不会触及交易响应方那套机制。

交易应答不是一个工具。`propose_trade` 会同步唤醒响应方并阻塞提议方的调用；响应方用一个 JSON 对象在其最终消息文本中表达决策：

```json
{
  "decision": "accept",
  "reason": "Brick and ore are surplus to me."
}
```

`decision` 取值为 `accept` 或 `reject`（不存在还价）。响应方的最终文本会被解析并提交给引擎，引擎再把 `reason` 投递给提议方。缺失或格式错误的决策会被视为拒绝，并附上系统给出的理由。

聊天应答与交易决策不同：它**没有格式要求，也从不做 JSON 解析**。当 `send_message` 指定了 `target`，引擎会把目标玩家作为聊天响应方唤醒，目标玩家只需用自己的话作答；可见的回复文本会**原样**作为 `send_message` 的工具结果返回给发起方。思考/推理内容永不传输——只有可见回复会离开目标玩家的 session。如果目标玩家失败、超时或返回空内容，回复会是一句简短的方括号系统短语（例如 `[timed out]`、`[no reply]`、`[agent error]`、`[forfeited]`），因此发起方的唤醒永远不会被卡住。被唤醒的聊天响应方只能作答——它不能在同一次调用里自己发起新的交易或聊天，因此阻塞不会嵌套。

## 动作额度与调度

回合节奏由对局运行器掌控，而非引擎。引擎暴露一个可配置的 `limit`（默认 3，运行器/引擎上的 `--limit`）作为预留旋钮，并跟踪每名玩家的动作健康度——`total_actions`、`illegal_actions`、`noop_actions` 与 `forfeits`。动作健康度是一个**调度器/审计侧的旁路通道**：它被写入 `state.json`（以及结算后的状态），供运行器做 forfeit 判定和后续分析，并且刻意**不注入到面向 agent 的状态中**——模型是通过工具报错了解规则违规的，而不是通过计数器。动作健康度**只作为运行器 forfeit 判定的输入**（见下）；reward 从不直接读取它。`noop_actions` 是 `illegal_actions` 的一个子集，指的是在本次唤醒那一个调度动作**已经完成之后**才发出的动作（超额动作 / 在已结束的回合上空转）；它仅用于诊断，永远不会与 `illegal_actions` 相加。引擎本身不统计连续失败，也不对回合判负；它只会在某次唤醒超出预算后升级超额动作提示（见下文*分档的超额动作反馈*）。运行器则限制对“尚未达成当前步骤目标”的玩家重复唤醒的次数（`maxAgentCallsWithoutProgress`，默认 3），超过即判负。

运行器自己的回合健康度旁路通道（`health.json`，以 `health` 之名暴露给 reward）采用**反向记账**，而不是只在唤醒结束时才记一次 forfeit。每一次唤醒**在开始的瞬间**就被**预先记为一次 advance 加一次临时 forfeit**，这笔临时 forfeit 只有在该次唤醒正确结束时才会被释放——所谓正确结束，要么它确实推进了回合（达成了步骤目标，或 game-progress key 发生了变化），要么 agent 返回了一次合法的 `NO_REPLY` pass——**并且**该次唤醒的非法/超额动作增量仍在其预算之内（`maxIllegalActionsPerWake`，默认 3）。运行器在每次唤醒前后各快照一次 `legality.illegal_actions`；增量超过预算则保留这笔预扣，因此在一次唤醒里越过那一个动作之后继续空转，会落到 `forfeit_ratio` 里，而不是免罚。`forfeit_ratio` 因此表示“未能推动回合前进的唤醒所占比例”。这一点很重要，因为一次唤醒就是一个 agent 子进程：一个不停调用工具、从不交还控制的 agent 会一直跑到外层对局超时，永远走不到任何唤醒后的 forfeit 判定点，否则就会因而不被计入扣分。在唤醒开始时预扣（并立即刷写 `health.json`），可以让这种失控唤醒——以及在任何提交前发生的崩溃或被杀——都被计为一次 forfeit。引擎侧的 `forfeit_player` 不受影响，只在真正失败时被调用；预扣完全活在运行器的统计里。

引擎强制执行的唯一节奏规则是**每次唤醒一个动作**：一旦玩家提交了它的调度动作，同一次唤醒里任何后续的**消耗型**动作（`build`、`claim_project`）都会被拒绝（`acted_this_wake`）。`send_message` 与 `propose_trade` 属于豁免项——两者都是免费步骤，既不消耗动作，也不被它阻塞，因此可以在动作之前或之后使用（各自上限 3 次，见下）。玩家通过完全不再调用工具来结束回合。为了让这个交还点既有主动性又不必只靠反应，每个已提交动作的工具结果都会带上一个**主动的回合结束提示**：`result.turn_over = true` 加上 `result.turn_instruction`，一条本地化的“简述你刚做了什么，然后结束回复、不要再调用任何工具”的消息。该提示会立即附加在真正消耗了本次唤醒的结果上（`build`、`claim_project`）。`send_message` 与 `propose_trade` 本身不消耗动作，所以不会因为自身而带上它；但当三类额度（建造/项目声明、交易、私聊）全部用尽时，任何成功结果——包括交易与私聊——都会补上 `turn_over` 与 `turn_instruction`，明确要求玩家结束回复。`view_game_state` 与响应方回合（交易决策或聊天回复，属于额外唤醒、不消耗动作）始终不带。

每次唤醒还带有来自 `map_generator.py` 的固定额度：一次 `build` 或 `claim_project`（两者共用一个槽位，因此互斥），最多 `WAKE_TRADE_LIMIT`（3）次 `propose_trade` 调用，以及最多 `WAKE_CHAT_LIMIT`（3）次 `send_message` 调用。超出额度的调用会被拒绝，返回一条额度耗尽的消息，并按超额动作的方式计入统计；当所有额度都用尽时，回复会带上回合结束提示。一个回合给每名玩家 `ACTIONS_PER_ROUND`（1）次调度唤醒。运行器负责驱动蛇形摆子顺序，并在 play 阶段每回合为每名玩家调度一次。在反向记账下，它还会对“唤醒从未提交”的玩家判负——一次失败的调用，或者一个一直空转到对局超时都不交还控制的 agent——因此一个惰性或失控的座位既不能阻塞对局，也逃不掉 `forfeit_ratio` 的惩罚。

### 分档的超额动作反馈

因为空转绝不能被免罚，引擎还会给模型一个与预算对齐的*局部*停止提示。它用 `illegal_actions - illegal_baseline` 来衡量本次唤醒的用量（`illegal_baseline` 由 `_advance` 在每个唤醒边界重新快照——不额外引入计数器），并把超额动作消息按三档升级：

- `used < budget` → 普通的 `action_complete` / `placement_complete` 提示（现在停下即可释放本次唤醒的临时 forfeit）；
- `used == budget` → `action_near_budget`，警告额外调用额度已用完，再来一次就会被计为 forfeit；
- `used > budget` → `action_over_budget`，报错说明已超出上限，本次唤醒会被计为一次 forfeit。

forfeit 仍然只在 `Δillegal_actions > budget` 时触发（即从第 `budget+1` 次调用起），因此这条警告与这条错误恰好落在“最后允许的一次”和“第一次被罚的一次”上。引擎从运行器接收预算（`--max-illegal-per-wake`，与 `maxIllegalActionsPerWake` 对应），因此措辞永远不会与判定结果相矛盾。消息遵循下文所述的错误反馈契约，并以六种语言发布。`noop_actions` 仍然只是诊断用的子集——分档只看 `illegal_actions`，因为 `noop_actions ⊆ illegal_actions`。

### 注入状态与固定规则

每次唤醒都会注入动态状态（回合、骰子、阶段、每名玩家的资源 / 建筑 / 道路 / 项目、待处理的聊天、`expansion_targets`、分数），以及自该玩家上次回合以来的事件。静态地图拓扑存放在 agent 的 workspace 上下文（`AGENTS.md`）中。另有两条卫生规则：

- **固定规则放在玩家 prompt 里，不放在状态里。** 建造成本表（道路 / 定居点 / L1→L2 / L2→L3）、定居点间距规则，以及每名玩家的上限（定居点 / 道路 / 升级建筑）都写在 `prompts/player.md` 及其五个译本里，运行器会把它们变成 agent 的 `AGENTS.md`。
- **状态不携带调度器计数器。** `legality` 不面向 agent（见上文）。
- **每次唤醒都带一份简短的、排好序的推荐动作列表。** 引擎会在步骤指令后追加 `Recommended moves for P<id>`：最多 8 个规则允许的选项，最好的在前，并在每次唤醒时依据实时状态重新计算，因此始终反映当前棋盘。定居点候选来自 `expansion_targets.settlements`（占用情况与“距*任意*建筑至少 2 条边”的规则已在那里校验）加上玩家自有建筑的升级；道路来自 `expansion_targets.roads`。排序规则为：定居点是 `3 × 资源种类数 + pips/12 + 2 × 自有网络连通度 + 1（当距对手建筑 ≥3 条边时）`，道路是 `2 × 打开的地点数 + pips/24`，L1→L2 / L2→L3 升级分别是 `2` / `3`；同分时按 `(kind, id)` 排序，因此同一棋盘永远给出同一份列表。表格给出**成本，但从不给出“买不买得起”的结论**——判断可负担性是 agent 的职责（这正是要训练的技能），因此没有 `affordable` 标记，评分里也没有可负担性项。措辞来自 i18n 目录，所以这份候选列表使用对局语言。完整的合法动作集合仍保留在 `expansion_targets` 中供规划使用。

## 调度契约

运行器掌管回合顺序与节奏；引擎不保存任何回合/顺序状态。它一次只唤醒一名玩家——摆子阶段按蛇形顺序（每次唤醒完成一组“定居点后接道路”），play 阶段轮转（每名玩家每回合 `ACTIONS_PER_ROUND` 次唤醒）——并在 `game_over` 为真时停止。

引擎不持有 `current_player`；当运行器以 `openclaw` 模式并带 `--responder-config` 启动它时，那两类*响应方*唤醒同样由引擎拥有，并且是阻塞式的：

- `propose_trade` —— 引擎保持提议方的工具调用不返回，唤醒被指定的响应方一次（一次单轮的 `openclaw agent` 调用，且不得调用任何工具），解析它的 `{decision, reason}` JSON，并就地返回裁决结果。
- `send_message` —— 同一套机制，就地返回目标玩家的自由格式回复。

两类响应方唤醒都只阻塞发起方自己的连接，因此引擎的 socket 服务器必须能够并行接受并服务每一条玩家连接。当前单线程的 `select` 循环是就地执行这段阻塞等待的，会在交易/聊天期间冻结其他所有玩家的 I/O——这是一个已知的并发缺陷。修法是：每客户端一个线程，或改为延迟处理、由就绪响应队列排空；另外还要在协议中加入 request ID，以及一个由运行器的单回合超时推导出来、并严格小于该超时的等待预算。

因此运行器自己从不调度聊天响应方，而且只把非聊天类的玩家消息注入到接收方 session（见“架构”）。如果对局结束时仍有交易处于待处理状态，运行器会在结算前将其自动拒绝。

一次 agent 调用不会在工具序列中途被抢占。因此玩家 prompt 定义了三个重要的交还点：

- 交易提议成功之后，提议方的工具调用要等响应方作答后才会返回；随后提议方在同一次唤醒内继续使用剩余额度（最多 3 笔交易、3 条私聊），或直接结束回复。一个回合只有一次调度唤醒，运行器不会在本回合内再次调度它。
- 被唤醒的响应方（交易决策或聊天回复）只作答一次，带上反馈，然后停下。它不会在同一次调用里继续执行常规的回合动作。

这些规则写在 `prompts/player.md` 里，并在运行器的动态唤醒消息中重复一遍。

## 错误反馈契约

每个引擎动作要么提交并返回 `{"ok": true, "result": …}`，要么被拒绝并返回 `{"ok": false, "error": "…"}`。被拒绝的请求永不改动游戏状态、永不记录游戏动作，也永不推进回合或轮次。拒绝是引擎教会模型规则的方式，因此 `error` 字符串必须自洽且可操作——玩家必须仅凭这条消息就能修正请求，而不必回读完整状态：

1. **被拒绝的是什么** —— 具体的 id、资源或条件（例如 `Invalid vertex: v99`、`Unknown project: hex_lord`）。
2. **为什么失败** —— 违反了哪条规则（例如间距、可负担性、回合顺序、归属）。
3. **如何修正** —— 要求是什么，并在适用时给出当前值（例如 `Have: {…}` 或剩余条件）。

典型示例：

```text
Cannot afford settlement. Cost: {wood:1, brick:1, sheep:1, wheat:1}, Have: {wood:1, brick:0, …}
Too close to P2's building at v41 (min 2 edges between settlements).
Conditions not met for 'Trade Master': requires complete 8+ trades; you currently have 3 trades completed.
Project already claimed by P1. Claim a different project.
```

部分失败的缺陷——只返回 `ok: false` 而无任何解释，或者在发现校验错误之前状态已被改动——应被视为正确性 bug，而不是外观问题。

## 运行要求

- macOS、Linux 或 WSL，且支持 Unix domain socket。
- Python 3.9 或更高版本；引擎与运行器只使用标准库。
- 运行以 OpenClaw 为后端的对局需要仓库支持的 Node.js 版本与 pnpm。
- 已安装的 OpenClaw workspace 依赖树（`pnpm install`）。

mock 对局不需要模型、API key，也不需要运行中的 Gateway。

## 跑一局 mock 对局

仓库内的示例配置给出推荐实验参数（3 人、5 回合、`thinking=off`、`agentsSchema=entries`、单个共享 Gateway、玩家侧禁用 exec/write 等工具）。只想验证流程、不调用模型时，显式指定 `--mode mock`：

```bash
python3 extensions/tradecraft/python/game_runner.py \
  --config extensions/tradecraft/game_runner.example.json \
  --mode mock
```

mock 玩家在不调用 LLM 的情况下完成摆子与全部十二个回合。每个回合为每名玩家结算一次产出掷骰。这样可以验证地图、引擎协议、状态转换、调度循环，以及终局计分。

## 跑一局 OpenClaw 对局

把 `game_runner.example.json` 复制到仓库之外，凭据放在环境变量里，不要写进文件。示例已经是推荐实验配置，通常只需要改三处：`openclaw.model`（`<provider-id>/<model-id>`）、`openclaw.apiKeyEnv`（密钥所在的环境变量名）、以及 `openclaw.configOverlay.models.providers.<provider-id>` 里的 `baseUrl` 与 `models[0].id`：

```json
{
  "openclaw": {
    "model": "example/model-id",
    "apiKeyEnv": "PROVIDER_API_KEY",
    "thinking": "off",
    "configOverlay": {
      "models": {
        "mode": "replace",
        "providers": {
          "example": {
            "baseUrl": "https://your-endpoint.example.com/compatible-mode/v1",
            "api": "openai-completions",
            "apiKey": "${PROVIDER_API_KEY}",
            "models": [
              { "id": "model-id", "name": "model-id", "input": ["text"], "contextWindow": 262144 }
            ]
          }
        }
      },
      "plugins": {
        "load": { "paths": ["/path/to/openclaw-26.7.1/extensions/tradecraft"] }
      }
    }
  }
}
```

`configOverlay.plugins.load.paths` 必须指向本插件目录的绝对路径。示例还带上了玩家侧工具白名单（`tools.deny` 屏蔽 exec/write/edit/apply_patch/web/browser/image）和关闭 `memory-core` 的 dreaming，避免训练中的 agent 在后台自启任务。`thinking` 只能填模型实际支持的值；`qwen3.8-27b` 只接受 `off` / `ultra`，推荐 `off`。

然后运行：

```bash
export PROVIDER_API_KEY="..."
python3 extensions/tradecraft/python/game_runner.py --config /path/to/local-config.json
```

`openclaw.model` 必须使用 `provider/model` 形式。`apiKeyEnv` 是环境变量的名字，而不是凭据本身。所选的 OpenClaw provider 必须认得这个变量。provider 定义、base URL 与模型目录可以通过 `openclaw.configOverlay` 添加；该 overlay 会被合并进每一份生成的玩家配置。

若要为每名玩家提供各自独立的凭据，请用 `apiKeyEnvs` 替换 `apiKeyEnv`。数组长度必须等于 `players`，且每个变量名必须唯一。按玩家区分的 key 需要在 `openclaw.configOverlay.models.providers.<provider-id>` 下有一个匹配的自定义 provider，运行器才能把正确的环境变量引用写进每份隔离配置。

常用的准备模式：

```bash
# 只生成隔离的玩家配置，不启动对局。
python3 extensions/tradecraft/python/game_runner.py \
  --config /path/to/local-config.json \
  --prepare-only

# 启动引擎与 Gateway，验证注册与会话注入，然后停止。
python3 extensions/tradecraft/python/game_runner.py \
  --config /path/to/local-config.json \
  --mode openclaw \
  --check-infrastructure

# 保持引擎与 Gateway 运行，但不调用 LLM。
python3 extensions/tradecraft/python/game_runner.py \
  --config /path/to/local-config.json \
  --mode openclaw \
  --serve-infrastructure
```

生成的玩家配置共用配置项里的 base port 与 profile 路径，因此一份运行器配置的预期用法是串行执行多局对局，而不是并发。

## 重试与状态复核

运行器在决定是否重试之前，会先对失败的 OpenClaw agent 调用做分类。

可重试的类别：

- 超时
- 触发限流（rate limit）
- provider 过载
- 传输/网络故障
- HTTP 500、502、503 或 504

认证失败、配额/计费、模型/配置错误，以及未知失败，会立即中止对局。常规重试使用带上限的指数退避与等量抖动。如果常规重试最终仍落在限流错误上，运行器可以进入一个更长的限流等待循环，由 `rateLimitRetrySeconds` 与 `rateLimitMaxWaitSeconds` 控制。

在每一次失败尝试和每一次等待之后，运行器都会重读引擎快照。如果那个超时的 agent 实际上已经完成了一个游戏动作，运行器会记录 `agent_failure_after_progress`，并从新状态继续，而不是重放这次调用。在中止对局之前，还会有一个最终的观察窗口做同样的检查。

## 产物

默认运行时根目录是 `runs/tradecraft/`：

```text
runs/tradecraft/
├── profiles/player-N/
│   ├── openclaw.json
│   ├── state/
│   └── workspace/
└── matches/<game-id>/
    ├── resolved-config.json
    ├── responder-config.json
    ├── state.json
    ├── engine-events.jsonl
    ├── runner-events.jsonl
    ├── engine.log
    ├── gateway.log
    ├── traderespond-pN.txt / chatrespond-pN.txt
    ├── thinking.json
    ├── health.json
    └── agent-pN.jsonl
```

`thinking.json` 与 `health.json` 是单行（紧凑）的旁路快照；仓库外的训练 reward 会读取它们，之所以刻意写成紧凑格式，是为了让一行文本读取（`head -1`）就能解析。`health.json` 会在**每次唤醒开始时**重新刷写（反向记账的预扣），因此一局在唤醒中途被终止的对局，仍会把这次在途唤醒报告为一次 forfeit。

`runs/` 被 Git 忽略。生成的玩家配置包含本地 Gateway token，并可能引用 API key 的环境变量名，因此应把整个运行时目录当作本地实验数据处理。

## 验证

在插件的 Python 目录下运行针对性的 Python 测试套件：

```bash
cd extensions/tradecraft/python
python3 -m unittest test_engine.py test_game_runner.py
```

在仓库根目录下运行插件的 TypeScript 检查：

```bash
pnpm tsgo:extensions
```

Python 测试覆盖确定性拓扑、三人与四人摆子、建造与交易校验、定向交易反馈、重试分类与退避、消息投递、配置隔离，以及完整的 mock 对局。

## 当前限制

- 引擎是一个可信的、本地的、单对局进程；它没有认证、加密、重连协议，也没有对局中途恢复能力。
- NDJSON 协议没有 request ID，每名玩家只支持串行请求。
- 运行器使用固定的本地端口与配置目录，不支持并发对局之间的协调。
- 玩家消息与模型 transcript 属于实验产物，不是持久化的游戏数据库。
- 不包含训练、ELO 池、回放 UI，也没有分布式 rollout 层。
