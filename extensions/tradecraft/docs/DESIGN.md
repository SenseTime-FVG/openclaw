# TradeCraft — 设计文档

> 本文记录 TradeCraft 的**设计动机、训练目标与演进中的方案**。工具定义、动作额度、调度契约、错误反馈、运行方式与产物布局等**实现规格以 [README.md](../README.md) 为准**；两者冲突时以 README 与代码为准，本文不再复述。
>
> 因此本文不重复：资源/成本/计分等具体规则、四个工具的 schema 与校验、每唤醒 1/3/3 额度、回合与响应方的调度契约、错误反馈契约、`state.json` 与注入状态的字段、目录与产物清单。

## 一、概览

### 1.1 定位

TradeCraft 是一个回合制多人地图扩张资源博弈环境，核心机制继承自 Catan 的六边形地图与骰子产出系统。玩家通过建路扩张、建定居点占领顶点、升级城市扩大产出、多边谈判交易互补资源，固定轮数后按积分决胜负。

**训练目标**：让 LLM 在空间约束和不确定环境下学会 **空间几何理解、路径规划、资源管理、概率评估、谈判策略和对手适应**。具体规则见 README「游戏模型」，这里只讨论为什么这样设计。

### 1.2 为什么做

数学/代码 RL 训练的是「问题→答案」单向推理。TradeCraft 训练的是：

|          | 数学 RL           | TradeCraft                             |
| -------- | ----------------- | -------------------------------------- |
| 信息获取 | 一次性全部给定    | 地图状态多轮观察，对手行动逐步揭示意图 |
| 最优解   | 唯一 ground truth | 路径不唯一，取决于地图布局和对手策略   |
| 反馈     | 即时答案验证      | 时序因果（早期扩张决策影响后期资源流） |
| 空间推理 | 无                | 六边形拓扑、道路连通性、距离约束       |
| 概率评估 | 无                | 骰子分布 → 期望产出计算                |
| 对手     | 无                | 地图空间竞争 + 交易谈判                |

这些能力更接近真实 agent 部署场景：物流规划、供应链博弈、多智能体空间协调。

### 1.3 系统形态

```
同一模型权重 → N 个 agent session（单个共享 Gateway，每座位一个 agent）
                       │
                       ▼
            TradeCraft 引擎（Unix socket，确定性状态机）
                       │
                       ▼
                  verl GRPO 训练
```

引擎是权威状态机：所有动作由它校验、裁决与记账，因此对环境而言 rollout 是确定性的；不确定性只来自地图种子和模型采样。进程模型、socket 协议与调度细节见 README「架构」与「调度契约」。

### 1.4 参考的游戏 / 研究

- **[Catan](https://www.catan.com/)**：六边形地图 + 骰子产出 + 道路扩张 + 交易，经历数十年玩家验证
- **[Splendor](https://boardgamegeek.com/boardgame/148228/splendor)**：资源采集→买卡→积分，纯策略无随机
- **[SPIRAL](https://arxiv.org/abs/2506.24119)** (Liu et al., 2025)：LLM Self-Play + PPO 博弈训练
- **[MARSHAL](https://arxiv.org/abs/2510.15414)** (Yuan et al., 2025)：多智能体 RL 训练框架，turn-level advantage

---

## 二、相关研究工作

### 2.1 最直接相关（LLM Self-Play + 博弈 RL）

**[SPIRAL](https://arxiv.org/abs/2506.24119)** — Liu et al., 2025. 🟢 **最接近我们的方案。**

- 三个博弈游戏：TicTacToe、Kuhn Poker、Simple Negotiation
- Self-play + PPO，Role-conditioned Advantage Estimation (RAE)
- 训练模型：Qwen + Llama 系列（1.5B–7B）
- **差异**：无空间地图维度，TradeCraft 增加六边形拓扑推理

**[MARSHAL](https://arxiv.org/abs/2510.15414)** — Yuan et al., 2025. 🟢 **多智能体 RL 训练框架。**

- Turn-level advantage estimator + agent-specific advantage normalization
- 训练后 held-out 游戏 +28.7%

**[RLVR Negotiation](https://arxiv.org/abs/2604.09855)** — Liu et al., 2026. 🟡

- 30B buyer agent，GRPO + RLVR，经济剩余 reward
- **差异**：仅训练买方单边，不涵盖空间维度

### 2.2 博弈论 + LLM 基准

- **[ALYMPICS](https://arxiv.org/abs/2311.03220)** (Mao et al., 2024, COLING 2025)：LLM agent 博弈论实验平台，多轮稀缺资源拍卖
- **[Agent Bazaar](https://arxiv.org/abs/2605.17698)** (2026)：多智能体市场仿真框架，REINFORCE++ RL 训练 9B 模型超越 frontier
- **[Market-Bench v2](https://arxiv.org/abs/2604.05523)** (Zheng et al., 2026)：供应链经济竞争基准，Persona-Gated Attention 机制
- **[Board Game Arena](https://arxiv.org/abs/2508.03368)** (2025)：跨 9 种策略桌游的 LLM 评估基准

### 2.3 开源引擎 / 参考实现

| 项目                                                 | 链接   | 内容                                       |
| ---------------------------------------------------- | ------ | ------------------------------------------ |
| [Catanatron](https://github.com/bcollazo/catanatron) | GitHub | Catan 开源 Python 模拟器，支持快速批量对局 |
| [PyTAG](https://github.com/davidADSP/pytag)          | GitHub | Splendor/Catan 等桌游的统一 RL 框架        |

### 2.4 TradeCraft 的差异点

| 维度                    | 已有工作                                                                                   | TradeCraft                     |
| ----------------------- | ------------------------------------------------------------------------------------------ | ------------------------------ |
| LLM Self-Play           | ✅ [SPIRAL](https://arxiv.org/abs/2506.24119), [MARSHAL](https://arxiv.org/abs/2510.15414) | ✅                             |
| 六边形地图空间推理      | ❌ 无                                                                                      | ✅ 19 六边形 × 54 顶点 × 72 边 |
| 道路连通性 + 路径规划   | ❌ 无                                                                                      | ✅                             |
| 骰子概率 + 资源产出期望 | ❌ 无                                                                                      | ✅ 2d6 分布评估                |
| 玩家间自由交易          | 🟡 SPIRAL（2 资源）                                                                        | ✅ 5 资源双边协商              |
| verl 原生集成           | ❌ 无                                                                                      | ✅                             |
| 多身份同时训练          | 🟡 SPIRAL self-play                                                                        | ✅                             |

**核心空白**：目前没有工作将「六边形地图空间推理 + 道路扩张 + 概率产出评估 + 双边交易谈判」四要素合并到一个 LLM RL 训练环境中。TradeCraft 填补的正是这个交叉点。

---

## 三、训练目标与可观测信号

### 3.1 十项能力

| 能力             | 训练机制                                                                       |
| ---------------- | ------------------------------------------------------------------------------ |
| **空间几何理解** | 六边形拓扑：顶点邻接地块、道路连通性、距离判断                                 |
| **路径规划**     | 道路扩张需要选最优路径到达目标顶点，考虑成本和对手竞争                         |
| **概率评估**     | 骰子分布（2d6）→ 评估每个顶点位的期望产出                                      |
| **资源管理**     | 建筑成本、升级路线、库存分配                                                   |
| **价值评估**     | 动态稀缺性 → 同一资源在不同地图、不同时期价值不同                              |
| **谈判策略**     | 双边交易中的锚定、让步、虚张声势                                               |
| **对手建模**     | 观察对手扩张路径 → 推断意图和偏好                                              |
| **机会成本**     | 每回合只有 1 次消耗型动作，交易与私聊各 3 次 → 建路/建点/升级/交易/聊天/抢项目 |
| **风险回报**     | 高产数字地块（6/8）vs 独占低产地块（2/12）的选择                               |
| **适应性策略**   | 目标顶点被对手抢 → 即时应变                                                    |

### 3.2 观测指标

训练是否真的在练这些能力，看的是**同一套评估种子上的相对变化**，而不是某个绝对分数：

- **固定对手胜率**：对规则 AI / 冻结基线的长期胜负比
- **平均积分**：多个评估种子的均值与方差
- **交易效率**：成交数 / 提议数
- **建筑 ROI**：建筑累计产出 / 建造成本
- **道路利用率**：每条道路连接的建筑数
- **动作分布熵**：观察策略多样性，判断是否塌缩到单一套路

> 不预设「胜率 > X%」这类绝对目标：对手强度、地图种子、轮数上限都会改变分母，固定阈值只会变成误导性的 KPI。判断标准统一放在对照实验里（见 §六）。

---

## 四、与 verl 的集成设计

RL 训练集成（reward、dataset、rollout）位于 `verl-tradecraft`，不在本插件仓库内；本节记录双方的接口约定。

### 4.1 组件对齐

| 组件             | 变动                                                                                                  |
| ---------------- | ----------------------------------------------------------------------------------------------------- |
| Agent (OpenClaw) | 不变                                                                                                  |
| GatewayActor     | 不变（角色前缀走 system prompt 注入，1 role = 1 session）                                             |
| E2BAgentRunner   | 收多 SessionHandle / 多 sandbox_url（路线 2，见 role-graph 稿 §9.1）                                  |
| OpenClawHarness  | 角色复用同一 harness；tradecraft 专属 harness 读 `ctx.extra["game_config"]`                           |
| Plugin 配置      | 新增 `plugins.entries.tradecraft`（4 工具 + socket path + playerId）                                  |
| Reward           | `omni_reward/tradecraft/` 下 `TradeCraftReward`（`_class_name` + 构造参数，短/长轮次分支）            |
| Dataset          | 新增 JSONL + meta JSON；`game_config`（players/seed/maxRounds…）统一透传；`dynamic.env_pool` 路由身份 |
| Skills           | 新增 dir copy 引擎文件 + 地图种子文件                                                                 |

> 详细的多角色（role-graph）集成设计见 `verl-tradecraft/docs/role-graph-rl-design.md`。

### 4.2 Reward 设计

Reward 分支、`forfeit_penalty` 口径与「行动合法性只经 forfeit 生效」的完整约定，
统一维护在 `verl/docs/role-graph-rl-design.md` 第 6 节；本文不再复述，避免两份规格漂移。
实现入口：`recipe_custom/reward_score/omni_reward/tradecraft.py`。

### 4.3 样本 JSONL

```json
{
  "index": 0,
  "prompt": [
    { "role": "system", "content": "{{SYSTEM_PROMPT}}" },
    {
      "role": "user",
      "content": "Game begins. Map seed: 42. You are Player 0. Place your first settlement and road."
    }
  ],
  "agent_name": "openclaw_agent",
  "reward_model": { "reward_fn": "tradecraft" },
  "game_config": { "players": 3, "seed": 42, "maxRounds": 12 }
}
```

### 4.4 Dataset Meta

```json
{
  "tradecraft_train": {
    "annotation": "/data/tradecraft/train.jsonl",
    "agent_name": "openclaw_agent",
    "reward_fn": "tradecraft",
    "skills": [
      {
        "type": "dir",
        "source": "/path/to/tradecraft",
        "sandbox": "/home/user/tradecraft/extensions/tradecraft"
      }
    ],
    "game_config": {
      "players": 3,
      "seed": 42,
      "map_seed": 42,
      "maxRounds": 12
    },
    "dynamic": {
      "env_pool": [
        { "env_name": "player_0", "weight": 1 },
        { "env_name": "player_1", "weight": 1 }
      ]
    }
  }
}
```

### 4.5 地图种子与数据划分

`seed` 控制骰子序列（兼做默认地图种子），`map_seed` 独立控制地块资源排列与数字标记分配；地图生成始终施加均衡约束（同资源不相邻、红数字 6/8 不相邻）。每个 epoch 轮换种子，训练集和评估集使用不相交的种子池，作为开局状态差分（短/长轮次任务靠不同种子区分）。

---

## 五、对手分级

| Phase | 我方        | 对方                         | 目的                    |
| ----- | ----------- | ---------------------------- | ----------------------- |
| P1    | 训练中      | 内置规则 AI（可配置 2-3 个） | 打通训练链路            |
| P2    | 训练中      | 冻结 baseline 模型           | 对抗真实对手            |
| P3    | 训练中 (P0) | 训练中 (P1,P2)               | 多人 self-play 共同进化 |

分级的意义是把「能不能跑通」「打得过固定对手」「能不能共同进化」三个问题分开验证；混在一起时，胜率下降无法归因。

---

## 六、评估策略

TradeCraft 训练的是空间规划、概率评估、对手建模和资源配置——与 AIME/MATH 这类推理 benchmark 存在本质差异，因此**不借用外部推理分数判断能力**，正式指标只来自游戏内部。

### 6.1 游戏内指标

| 指标         | 方式                             | 说明               |
| ------------ | -------------------------------- | ------------------ |
| 固定对手胜率 | 对规则 AI / 冻结基线的长期胜负比 | 最核心的能力检验   |
| 平均积分     | 固定评估种子集合上的均值与方差   | 测策略稳定性和泛化 |
| 交易效率     | 成交数 / 提议数                  | 测谈判意愿和精准度 |
| 建筑 ROI     | 建筑累计产出 / 建造成本          | 测投资效率         |
| 道路利用率   | 每条道路连接的建筑数             | 测路径规划质量     |
| 跨地图泛化   | 对训练中未见过的地图种子的胜率   | 测空间策略迁移     |

### 6.2 评估-训练分离

评估种子池与训练种子池不相交，且评估集在实验期间固定，报告均值与标准差。任何指标只在**同一评估池、同一对手**下的前后对比才有意义。

### 6.3 外部基准

多智能体（协作 / 竞争 / 角色分工）类外部基准，用于观察「tradecraft 联合其它 RL 任务」对通用多智能体能力的迁移收益。这里只列候选；具体口径与实施步骤见 §九。

| 基准                             | 属性                                   | 与 TradeCraft 重叠         | 跟进状态             |
| -------------------------------- | -------------------------------------- | -------------------------- | -------------------- |
| TeamBench                        | Planner/Executor/Verifier 角色分工协作 | 低                         | 计划跟进（ablation） |
| MultiAgentBench / MARBLE         | 6 场景协作 + 竞争                      | 中低（competition 最接近） | 计划跟进（pilot）    |
| Collab-Overcooked                | 纯协作（Overcooked）                   | 低                         | 备选                 |
| CEO-Bench / CFO-Bench / ALYMPICS | 资源决策 / 金融 / 拍卖                 | 中低                       | 暂不跟进             |

---

## 七、开发路线

| Phase | 内容                                                            | 状态                                   | 工时 |
| ----- | --------------------------------------------------------------- | -------------------------------------- | ---- |
| P1    | 地图生成器 + 游戏引擎（六边形拓扑、骰子产出、建造校验、3-4 人） | ✅ 已完成                              | 3 天 |
| P2    | OpenClaw 插件（TypeScript，4 工具）                             | ✅ 插件加载验证通过                    | 1 天 |
| P3    | 评估脚本（胜率/积分/交易/道路/建筑指标）                        | 🟡 进行中（比赛 artifacts 已产出指标） | 1 天 |
| P4    | RL 训练集成（reward + dataset + agent_loop）                    | ✅ 初版跑通（verl-tradecraft）         | 2 天 |
| P5    | Scaling（对手升级、更大规模、规则微调）                         | ⬜ 待开发                              | 3 天 |

尚未收敛的设计问题，按优先级：

1. **对手池**：P2/P3 需要可复现的冻结 checkpoint 集合，否则胜率曲线不可比（§五）。
2. **评估脚本产品化**：目前指标由比赛 artifacts 手工汇总，需要固定输入输出（§6.1）。
3. **长局的时序信用分配**：单局 12 轮、单唤醒可能数百秒，截断与 settlement 的权衡仍在调（§4.2）。

---

## 八、游戏模式：FFA + 2v2

> 在 FFA self-play 之上增加 2v2 组队模式，同时训练博弈与协作。prover-verifier / judge 角色图属于通用多智能体 judge 设计范畴，见 `multiagent-judge-rl-design.md`，本文只标注与本游戏的相关性。
>
> **实现现状**：引擎已支持 `--teams` 显式指定名单、队均分计分与 `team_winner`、`trade_master` 只计跨队成交。**未实现**：随机模式切换、`gift` 单向赠送、队内私聊通道。下文未标注「已实现」的条目均为设计意图。

### 8.1 角色图（本游戏自身）

| 角色            | 类型        | 是否可训     | 观测                   | 动作                      | reward 来源          |
| --------------- | ----------- | ------------ | ---------------------- | ------------------------- | -------------------- |
| Player (P0..P3) | policy 角色 | ✅           | 注入状态（含队伍信息） | 4 工具（队模式暂无 gift） | 见 8.2 模式化 reward |
| Engine          | 环境/裁判   | ❌（确定性） | 全局状态               | 校验/转发                 | —                    |

- 角色 = role-conditioned 会话（system prompt 注入 player_id / mode / team_id / 敌友标记）；复用 GatewayActor session 路由 + env_pool 身份路由。
- **关联说明**：本游戏的 Player（prover）与 Engine（GT/规则提供者）可接入通用 prover-verifier 角色图，由可训 Verifier/judge 角色承接 move 审计与 outcome 预测。该角色图与训练耦合设计见 `multiagent-judge-rl-design.md`（§3.1），双方通过此引用对齐。

### 8.2 游戏模式：FFA + 2v2 随机切换

| 模式             | 人数  | 计分              | 训练目标  |
| ---------------- | ----- | ----------------- | --------- |
| FFA（现有）      | 2/3/4 | 个人分 / max 分   | 竞争/博弈 |
| 2v2 Team（新增） | 4     | 队总分 / max 队分 | 协作/协调 |

- **随机模式切换（设计意图，未实现）**：每 episode 随机 sample `game_mode ∈ {ffa, team_2v2}`（默认 1:1，可配置）。当前实现为 runner config 显式 `teams` 字段 + 引擎 `--teams`，无随机采样。
- dataset meta 以 `game_config` 透传 `teams`（引擎增加 `--teams` 参数）；reward 函数按 `game_config` 分支。

**2v2 模式细节（实现现状）**

- 队伍名单由配置显式指定（`teams: [[0,3],[1,2]]`），随机分配尚未实现。
- 计分：**队均分**（`team_score = mean(member score)`，与个人分量纲一致），`team_scores` / `team_winner` 随 settlement 返回；「队总分 / max 队分」与个人贡献小项未实现。
- 合作通道：队内私聊**未实现**（`send_message` 已是定向私聊，但没有「仅队友」的专属通道）。
- 队内交易：`gift` 单向赠送**未实现**；跨队/队内交易均走同一双边协商，但 `trade_master` 只统计跨队成交。
- Free-riding 缓解：队均分 + 可选个人贡献小信号（后者未实现）。

### 8.3 与 verl 的对齐增量

| 组件         | 增量                                                                         |
| ------------ | ---------------------------------------------------------------------------- |
| GatewayActor | 不变（role-conditioned prefix 走 system prompt / env_pool 路由）             |
| Reward       | `TradeCraftReward` 按 `game_config` 分支 + 队计分（已实现队均分 settlement） |
| Dataset      | `game_config` 透传 `teams`；engine 增加 `--teams`（`--mode` 未实现）         |
| 引擎         | 队名单/队均分/跨队成交计数已实现；随机队分配 + gift + 队内私聊待实现         |
| 工具         | Player 仍为 4 工具（`gift` 待实现）                                          |

### 8.4 风险与缓解

| 风险                           | 缓解                                           |
| ------------------------------ | ---------------------------------------------- |
| 2v2 free-riding（搭便车）      | 队 reward + 个人贡献小信号；随机组队防固定队友 |
| 队模式坍缩为「全交易合作」     | 队内 gift 限额度/限次数；跨队仍竞争            |
| 模式切换干扰 credit assignment | 按 (mode, task, role) 分组，不全局 group       |

### 8.5 计划增量

| Phase | 内容                                           | 工时   | 状态      |
| ----- | ---------------------------------------------- | ------ | --------- |
| P4-1  | engine `--teams` + 队均分 + 跨队成交计数       | 1 天   | ✅ 已实现 |
| P4-2  | 随机模式切换 + gift + 队内私聊 + reward 队分支 | 0.5 天 | ⬜ 待开发 |

> Verifier/judge 角色（outcome 预测 / move 审计）的开发计划见 `multiagent-judge-rl-design.md`。

---

## 九、外部多智能体基准测评计划

### 9.1 目标与口径

- **问法**：tradecraft（社会 / 竞争 / 协作，FFA 单局 + 2v2 队友）联合 math / code / SWE 混训，对通用多智能体基准有没有**增量收益 Δ**。
- **口径**：ablation 对照——对照组 = 相同混训配方但不含 tradecraft；实验组 = 配方 + tradecraft。两组跑同一套基准，比绝对分 + Δ，才能把 tradecraft 的**边际贡献**从其它 RL 任务本来的贡献里剥出来。
- **tradecraft 自身能力**：不靠外部基准判断，靠 in-env「对其它 LLM 对战得分优势」（对手冻结 + external_relative 计分已实现）。

### 9.2 候选基准

**TeamBench**：测 OS 强制角色分离下的 Planner / Executor / Verifier 协作，按 (task, condition) 输出确定性 grader 结果，再聚合成团队归一化指数（TNI）。它提供 oracle / restricted / full / team_no_plan / team_no_verify 五个 ablation 条件，正好对上我们的「去掉某类协作后还剩多少」的问法。接入方式为任意 OpenAI 兼容端点或自定义 ToolCallAdapter；依赖 Docker 做文件系统级角色隔离，属于中偏高的接入成本。

**MultiAgentBench / MARBLE**：6 个交互场景（协作 + 竞争），用 milestone-level KPI 而非只看最终成败，能反映过程中的协作质量。接入只需在 `.env` 指向 vLLM 端点，成本中等，可只跑 competition 子集做 pilot。

### 9.3 实施步骤

1. **模型服务化**：vLLM 把两个 checkpoint（base 轮 vs tradecraft 轮）起成 OpenAI 兼容 `/v1` API。
2. **适配层**：TeamBench 用 `--base-url` 指 vLLM 或写 `ToolCallAdapter`；MARBLE 在 `.env` 配 API key + base URL。
3. **pilot**：TeamBench 先跑少量任务（含 `--model mock` 验证 grader/sandbox 链路）+ MARBLE 跑 1 个 competition 场景，确认 harness 通、看有无正向信号。
4. **铺开**：TeamBench leaderboard 任务集 × 5 条件；MARBLE 6 场景。
5. **分析**：两组 Δ、分场景（协作 vs 竞争）、分层（角色分工 vs 谈判）。

### 9.4 成本与决策

| 项             | 成本                                   |
| -------------- | -------------------------------------- |
| 模型服务化     | 低（已有 vLLM 经验）                   |
| TeamBench 全量 | 高（Docker + 近千实例 + 多轮多 agent） |
| MARBLE 全量    | 中                                     |
| pilot          | 低（几十样本，几小时量级）             |

- 先跑 pilot 验证「tradecraft 对社会 / 多智能体能力有正向迁移」这个前提，再决定是否铺全量。
- 优先 **MultiAgentBench 的 competition 场景**（与 tradecraft 重叠最大、目标最一致）；TeamBench 的角色分工与 tradecraft 目标错位，放低优先级。
