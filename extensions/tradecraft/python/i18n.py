"""TradeCraft i18n: agent-facing message catalogs for the engine + scheduler.

Targets six UN languages: en, zh, fr, es, ru, ar. A language without a full
catalog falls back to English. Protocol values (tool names, enum values,
resource keys, project ids, error codes) stay English everywhere — only
human-readable descriptions / prompts / feedback are translated.
"""

LANGS = ("en", "zh", "fr", "es", "ru", "ar")


def _catalog(en: dict, zh: dict) -> dict:
    return {"en": en, "zh": zh}


CATALOGS = _catalog(
    # ── English (source of truth) ──────────────────────────────────────────
    en={
        "game_over": "Game is already over.",
        "placement_title": "Placement for P{pid}",
        "placement_intro": "You are in the placement phase. Right now the rules require you to {action}",
        "placement_need_settlement": "place a settlement now: call tradecraft_build with type=\"settlement\" and a vertex_id from the recommended list below. Do NOT place a road first, and do NOT call tradecraft_build with type=\"upgrade\" — that is rejected during placement.",
        "placement_need_road": "place the road for the settlement you just placed: call tradecraft_build with type=\"road\" and an edge_id from the recommended list below. Do NOT start another settlement.",
        "rec_title": "Recommended moves for P{pid} (legal, best first, max {n})",
        "rec_settlement": "settlement {vid}  ({resources})",
        "rec_road": "road {eid}  ({v1}-{v2})",
        "rec_upgrade": "upgrade {vid} -> L{level}  ({cost})",
        "rec_footer": "{total} legal build targets in total; the full list is `expansion_targets` in the state.",
        "trade_pending_title": "Trade pending",
        "trade_proposer": "You proposed a trade and are waiting for the responder's decision. Do not call any tool; end your reply now so the runner continues.",
        "trade_responder": "You are the responder to the pending trade. Do NOT call any tool to respond. End with a single JSON decision in a fenced ```json block using the keys decision (accept or reject) and reason. A short reason is required. The runner parses this JSON; a missing or malformed decision is treated as a rejection.",
        "invalid_player": "Invalid player: P{pid}.",
        "robber_needs_hex": "Moving the robber requires a hex id.",
        "invalid_hex": "Invalid hex: {hex_id}. Use a hex id from the map (h0-h18).",
        "robber_same_hex": "The robber is already on {hex_id}. Choose a different hex.",
        "bank_offer_single": "A bank trade takes one resource type at a time: offer exactly {rate} of a single resource.",
        "bank_offer_rate": "Your bank rate is {rate}:1 — offer exactly {rate} {r}, not {got}.",
        "bank_request_single": "A bank trade yields one resource type at a time: request exactly 1 of a single resource.",
        "bank_request_one": "A bank trade yields exactly 1 resource, not {got}.",
        "bank_same_resource": "Cannot exchange {r} for {r}.",
        "dice_notice_round": "Production rolls this round: {rolls}. Every roll settles for the whole table, so different players gain different resources; per-player changes are in the production events.",
        "dice_roll_entry": "P{pid} rolled {roll}",
        "dice_roll_entry_seven": "P{pid} rolled 7 (no production; P{pid} holds the robber move)",
        "dice_none_yet": "No production roll has happened yet this round.",
        "dice_only_in_play": "Production rolls only happen in the play phase (phase={phase}).",
        "already_rolled": "P{pid} has already rolled this round.",
        "robber_notice": "The robber sits on {hex} ({resource}), which produces nothing while it stays there.",
        "robber_move_notice": "You hold this round's robber move. End your end-of-turn summary with one separate line in exactly this form:\n`ROBBER_MOVE: <hex id>` (for example `ROBBER_MOVE: h12`). Do not call a tool for it; the runner parses that line and moves the robber. The robber blocks production on that hex.",
        "await_settlement": "Game is over; awaiting settlement. Do not act further.",
        "unknown_action": "Unknown action: {action}. Use build, propose_trade, send_message, claim_project, or view_game_state.",
        "placement_only_build": "During placement the only legal action is build (type=settlement or type=road). If you have already placed all your settlements and their roads, your placement is done: end your reply without calling a tool so the match can advance to the play phase.",
        "settlement_awaiting_road": "You already placed a settlement awaiting its road. Place the road (build type=road) first.",
        "place_settlement_first": "Place settlement first, then road. Start with build type=settlement.",
        "placement_use_build": "During placement, use build type=settlement or type=road.",
        "not_placement_phase": "Not in placement phase (phase={phase}).",
        "road_needs_edge_id": "build type=road requires an edge_id. Pick one from the legal road options in the game state.",
        "invalid_edge": "Invalid edge: {edge_id}. Choose an edge_id from the legal road options.",
        "road_exists_owned": "Road already exists here (owned by P{owner}). Pick a different edge.",
        "max_roads": "Max {n} roads reached. You cannot build more roads.",
        "road_connect_settlement": "Road must connect to your newly placed settlement. Pick an adjacent edge.",
        "road_connect_road": "Road must connect to your existing road or building. Pick an adjacent edge.",
        "cannot_afford_road": "Cannot afford road. Cost: {cost}, Have: {have}. Trade or produce the missing resources first.",
        "settlement_needs_vertex": "build type=settlement requires a vertex_id. Pick one from the legal settlement options.",
        "invalid_vertex": "Invalid vertex: {vertex_id}. Choose a vertex_id from the legal settlement options.",
        "building_exists": "A level-{level} building owned by P{pid} already exists here. Pick a different vertex.",
        "max_settlements": "Max {n} settlements reached. You cannot place another settlement.",
        "settlement_connect_road": "Settlement must connect to one of your roads. Build a road first or pick a connected vertex.",
        "cannot_afford_settlement": "Cannot afford settlement. Cost: {cost}, Have: {have}. Trade or produce the missing resources first.",
        "upgrade_needs_vertex": "build type=upgrade requires a vertex_id. Pick one of your own buildings.",
        "no_building_here": "No building of yours at this vertex: {vertex_id}. You can only upgrade your own buildings.",
        "upgrade_step": "Can only upgrade from {cur} to {nxt}. Pass to_level={nxt}.",
        "max_level_2": "Max level is 2 (city). Buildings cannot be upgraded further.",
        "max_cities": "Max {n} cities reached. Upgrade a different settlement or claim a project instead.",
        "cannot_afford_upgrade": "Cannot afford upgrade. Cost: {cost}, Have: {have}. Trade or produce the missing resources first.",
        "trade_pending": "A trade is already pending. Do not propose another; end your reply and let the pending trade resolve.",
        "insufficient_resource": "Insufficient {r}: have {have}, offering {n}. Lower the offer amount.",
        "trade_target_int": "Trade target must be an integer player id (0-3).",
        "invalid_trade_target": "Invalid trade target: P{pid}. Choose another player (you cannot trade with yourself).",
        "no_pending_trade": "No pending trade to resolve.",
        "unknown_trade_decision": "Unknown trade decision: {decision}. Use accept or reject.",
        "message_required": "Message content required. Provide a non-empty negotiation message.",
        "unknown_project": "Unknown project: {pid}. Use one of trade_master, build_master, tech_pioneer, resource_tycoon, road_king.",
        "project_claimed": "Project already claimed by P{pid}. Claim a different project.",
        "cannot_settle_before_play": "Cannot settle before play (phase={phase}).",
        "settlement_pending_trade": "Settlement requires the pending trade to be resolved first.",
        "invalid_player_id": "Invalid player_id: {pid}",
        "unknown_controller_action": "Unknown controller action: {action}",
        "invalid_registration": "Invalid or duplicate player registration.",
        "controller_connected": "Controller already connected or invalid registration.",
        "internal_error": "Internal engine error: {exc}",
        "too_close": "Too close to P{pid}'s building at {vid} (min {dist} edges between settlements). Pick a vertex farther away.",
        "opponent_insufficient": "Opponent has insufficient {r}: have {have}, requesting {n}.",
        "conditions_not_met": "Conditions not met for '{name}': requires {req}; you currently have {progress}.",
        "game_not_finished": "Game is not finished: round {round_num}/{max_rounds}.",
        "placement_complete": "Your initial placement is complete. END YOUR TURN now: reply with a one-line summary and do NOT call any tool. The match moves to the play phase only after every player has ended their turn, and any further tool call in this turn is rejected.",
        "action_complete": "Your scheduled action for this turn is already complete. Provide a brief summary of what you just did, then end your reply without calling any tool to return control to the scheduler.",
        "action_near_budget": "Your scheduled action for this turn is already complete. You have now used all {budget} extra calls allowed this turn; one more call will be counted as a forfeit. Provide a brief summary of what you just did, then end your reply without calling any tool.",
        "action_over_budget": "Error: your scheduled action for this turn is already complete and you have exceeded the {budget}-call limit for this turn ({used} calls); this wake is counted as a forfeit. Stop now -- provide a brief summary and end your reply without calling any tool.",
        "turn_complete": "Your scheduled action is now complete. Provide a brief summary of what you just did, then end your reply without calling any tool to return control to the scheduler.",
        "trade_quota_exhausted": "You have used all {limit} trade proposals allowed in this wake. Do not propose another trade; use any remaining build or project claim or private message, or end your reply without calling any tool.",
        "chat_quota_exhausted": "You have used all {limit} private messages allowed in this wake. Do not send another message; use any remaining build or project claim or trade proposal, or end your reply without calling any tool.",
        "wake_quota_note": (
            "This wake allows one build or project claim (mutually exclusive), at most "
            "{trade_limit} trade proposals and at most {chat_limit} private messages. Trade "
            "and chat are free steps: they do not consume the build/claim slot and stay "
            "available before or after it."
        ),
        # scheduler / runner strings
        "wake_scheduled": (
            "You are scheduled now as TradeCraft P{pid}. Read the game state and any "
            "step instruction, then use the TradeCraft tools; do not merely explain a move. "
            "After you complete your scheduled action — a successful build, a project "
            "claim, or a placement step — stop immediately and return "
            "control to the runner by ending your reply without calling any further tool. "
            "During normal play, tradecraft_send_message is available for concrete "
            "negotiation such as stating resource needs or acceptable terms; it is a free "
            "step that does not consume your action, so you may message several players and "
            "then still act. If no productive legal action or worthwhile negotiation "
            "remains, do not call any tool — simply end your reply to end the turn. "
            "You must always produce an action (a tool call) or an explicit decision — an "
            "empty or silent reply is never acceptable."
        ),
        "state_header": "Current game state",
        "resources_header": "Your resources (current)",
        "resources_delta_header": "Changed since your last wake:",
        "resources_delta_none": "no change",
        "events_header": "Events since your last turn",
        "tools_instruction": (
            "Use only the four `tradecraft_*` tools exposed by the game plugin. "
            "To end a turn, do not call any tool — simply end your reply."
        ),
        "map_topology_header": "Map topology (fixed for this match; immutable)",
        "player_identity": "You are TradeCraft player P{pid}. Be strategic, concise, and decisive.",
        "operator_note": "The match runner is the operator. Follow its turn prompts.",
    },
    # ── Chinese ─────────────────────────────────────────────────────────────
    zh={
        "game_over": "对局已结束。",
        "placement_title": "P{pid} 的摆子阶段",
        "placement_intro": "你处于摆子阶段。现在规则要求你：{action}",
        "placement_need_settlement": "放置一个定居点：调用 tradecraft_build，type=\"settlement\"，vertex_id 从下方推荐列表中选。不要先修路，也不要调用 tradecraft_build 配 type=\"upgrade\"——摆子期会被拒绝。",
        "placement_need_road": "为你刚放置的定居点配一条道路：调用 tradecraft_build，type=\"road\"，edge_id 从下方推荐列表中选。不要再放定居点。",
        "rec_title": "P{pid} 的推荐动作（合法，按优先级排序，最多 {n} 条）",
        "rec_settlement": "定居点 {vid}（{resources}）",
        "rec_road": "道路 {eid}（{v1}-{v2}）",
        "rec_upgrade": "升级 {vid} → L{level}（{cost}）",
        "rec_footer": "共 {total} 个合法建造目标；完整列表见 state 里的 `expansion_targets`。",
        "trade_pending_title": "交易待响应",
        "trade_proposer": "你发起的交易正在等待响应方决定。不要调用任何工具；立即结束回复，让调度器继续。",
        "trade_responder": "你是待处理交易的响应方。不要调用任何工具来回应。用一个 ```json 代码块给出单个 JSON 决定，包含 decision（accept 或 reject）与 reason 两个键；必须给出简短理由。调度器会解析该 JSON；缺失或格式错误的决定视为拒绝。",
        "invalid_player": "无效玩家：P{pid}。",
        "robber_needs_hex": "移动强盗需要指定地块 id。",
        "invalid_hex": "无效地块：{hex_id}。请使用地图上的地块 id（h0-h18）。",
        "robber_same_hex": "强盗已经在 {hex_id} 上。请换一个地块。",
        "bank_offer_single": "银行交易一次只能给一种资源：请提供恰好 {rate} 个同种资源。",
        "bank_offer_rate": "你的银行汇率是 {rate}:1——请提供恰好 {rate} 个 {r}，而不是 {got} 个。",
        "bank_request_single": "银行交易一次只能要一种资源：请只要求 1 个同种资源。",
        "bank_request_one": "银行交易只能换到 1 个资源，而不是 {got} 个。",
        "bank_same_resource": "不能用 {r} 换 {r}。",
        "dice_notice_round": "本回合的生产掷骰：{rolls}。每次掷骰对全场结算，不同玩家入账的资源不同；每人的变动见 production 事件。",
        "dice_roll_entry": "P{pid} 掷出 {roll}",
        "dice_roll_entry_seven": "P{pid} 掷出 7（本次无产出，强盗移动权归 P{pid}）",
        "dice_none_yet": "本回合还没有人掷骰。",
        "dice_only_in_play": "只有对局期才掷骰（当前阶段 {phase}）。",
        "already_rolled": "P{pid} 本回合已经掷过骰。",
        "robber_notice": "强盗位于 {hex}（{resource}），该地块在强盗离开前不产出资源。",
        "robber_move_notice": "本回合的强盗移动权在你手上。请在结束回合的总结末尾单独用一行、严格按此格式写出目标地块：\n`ROBBER_MOVE: <地块 id>`（例如 `ROBBER_MOVE: h12`）。不要为此调用工具；调度器会解析这一行并移动强盗。强盗所在地块不产出资源。",
        "await_settlement": "对局已结束，正在结算；请不要再行动。",
        "unknown_action": "未知动作：{action}。请使用 build、propose_trade、send_message、claim_project 或 view_game_state。",
        "placement_only_build": "摆子阶段唯一合法的动作是 build（type=settlement 或 type=road）。如果你已经把该摆的定居点和路都摆完了，说明你的摆子已结束：请结束回复、不要再调用工具，让对局进入正式阶段。",
        "settlement_awaiting_road": "你已放置定居点、尚待修路。请先修路（build type=road）。",
        "place_settlement_first": "先放置定居点，再修路。请从 build type=settlement 开始。",
        "placement_use_build": "摆子阶段请使用 build type=settlement 或 type=road。",
        "not_placement_phase": "当前不在摆子阶段（phase={phase}）。",
        "road_needs_edge_id": "build type=road 需要 edge_id，请从游戏状态中的合法道路选项里选一个。",
        "invalid_edge": "无效边：{edge_id}。请从合法道路选项中选择 edge_id。",
        "road_exists_owned": "此处已有道路（属于 P{owner}）。请选其他边。",
        "max_roads": "已达 {n} 条道路上限，无法再修路。",
        "road_connect_settlement": "道路必须连接到新放置的定居点，请选相邻边。",
        "road_connect_road": "道路必须连接你已有的道路或建筑，请选相邻边。",
        "cannot_afford_road": "修路资源不足。所需：{cost}，现有：{have}。请先通过交易或生产补齐资源。",
        "settlement_needs_vertex": "build type=settlement 需要 vertex_id，请从合法定居点选项中选择。",
        "invalid_vertex": "无效顶点：{vertex_id}。请从合法定居点选项中选择 vertex_id。",
        "building_exists": "此处已有 P{pid} 的 {level} 级建筑。请选其他顶点。",
        "max_settlements": "已达 {n} 个定居点上限，无法再放置定居点。",
        "settlement_connect_road": "定居点必须连接你的一条道路。请先修路或选择已连接的顶点。",
        "cannot_afford_settlement": "建造定居点资源不足。所需：{cost}，现有：{have}。请先补齐资源。",
        "upgrade_needs_vertex": "build type=upgrade 需要 vertex_id，请选择你自己的建筑。",
        "no_building_here": "此顶点没有你的建筑：{vertex_id}。你只能升级自己的建筑。",
        "upgrade_step": "只能从 {cur} 级升级到 {nxt} 级，请传 to_level={nxt}。",
        "max_level_2": "最高 2 级（城市），无法继续升级。",
        "max_cities": "已达 {n} 个城市上限。请升级其他定居点或改为申请项目。",
        "cannot_afford_upgrade": "升级资源不足。所需：{cost}，现有：{have}。请先补齐资源。",
        "trade_pending": "已有待处理交易。请勿再发起新交易；结束回复，等待当前交易处理。",
        "insufficient_resource": "{r} 不足：现有 {have}，要提供 {n}。请降低数量。",
        "trade_target_int": "交易对象必须是整数玩家 id（0-3）。",
        "invalid_trade_target": "无效交易对象：P{pid}。请选择其他玩家（不能与自己交易）。",
        "no_pending_trade": "没有待处理交易。",
        "unknown_trade_decision": "未知交易决定：{decision}。请使用 accept 或 reject。",
        "message_required": "消息内容不能为空，请提供非空的谈判消息。",
        "unknown_project": "未知项目：{pid}。请使用 trade_master、build_master、tech_pioneer、resource_tycoon、road_king 之一。",
        "project_claimed": "该项目已被 P{pid} 抢先申请。请申请其他项目。",
        "cannot_settle_before_play": "对战阶段前无法结算（phase={phase}）。",
        "settlement_pending_trade": "结算前需先处理待定交易。",
        "invalid_player_id": "无效 player_id：{pid}",
        "unknown_controller_action": "未知控制器动作：{action}",
        "invalid_registration": "玩家注册无效或重复。",
        "controller_connected": "控制器已连接或注册无效。",
        "internal_error": "引擎内部错误：{exc}",
        "too_close": "距离 P{pid} 位于 {vid} 的建筑太近（定居点之间至少相隔 {dist} 条边）。请选更远的顶点。",
        "opponent_insufficient": "对方 {r} 不足：现有 {have}，你要求 {n}。",
        "conditions_not_met": "不满足项目 '{name}' 的申请条件：需要 {req}；你当前为 {progress}。",
        "game_not_finished": "对局尚未结束：当前第 {round_num}/{max_rounds} 回合。",
        "placement_complete": "你的初始摆子已完成。现在结束本回合：用一句话总结你刚摆的位置，并且不要再调用任何工具。只有所有玩家都结束回合，对局才会进入正式阶段；本回合再调用工具会被拒绝。",
        "action_complete": "你本回合的调度动作已完成。请简要总结你刚才的操作，然后结束回复（不要再调用任何工具），把控制权交还调度器。",
        "action_near_budget": "你本回合的调度动作已完成。本回合 {budget} 次额外调用额度已用尽；再调用一次将被记作一次 forfeit。请简要总结你刚才的操作，然后结束回复（不要再调用任何工具）。",
        "action_over_budget": "错误：你本回合的调度动作已完成，且已超出本回合 {budget} 次调用上限（当前第 {used} 次）；本回合将记作一次 forfeit。请立即停止——简要总结你刚才的操作，然后结束回复（不要再调用任何工具）。",
        "turn_complete": "你本回合的调度动作已完成。请简要总结你刚才的操作，然后结束回复（不要再调用任何工具），把控制权交还调度器。",
        "trade_quota_exhausted": "你本回合允许的 {limit} 次交易提议已用完。不要再次提议交易；请使用剩余的建造/项目声明或私聊额度，或者不再调用任何工具直接结束回复。",
        "chat_quota_exhausted": "你本回合允许的 {limit} 条私聊消息已用完。不要再次发送消息；请使用剩余的建造/项目声明或交易提议额度，或者不再调用任何工具直接结束回复。",
        "wake_quota_note": (
            "本次唤醒允许一次建造或项目声明（二者互斥），最多 {trade_limit} 笔交易提议、"
            "{chat_limit} 条私聊。交易与私聊都是免费步骤：不占用建造/项目声明额度，"
            "且在该动作前后都可以使用。"
        ),
        "wake_scheduled": (
            "现在轮到你（TradeCraft P{pid}）行动。请阅读游戏状态与步骤指令后使用 TradeCraft 工具，"
            "不要只口头说明。完成你的调度动作——成功的建造、项目申请或摆子步骤之后，立即停止，"
            "并通过结束回复（不再调用工具）把控制权交还调度器。正常对局中可使用 tradecraft_send_message "
            "进行具体谈判，例如说明资源需求或可接受条件；它是**不占动作的免费步骤**，因此你可以先依次私聊"
            "多名玩家，再执行你的动作。若已无有效合法动作或值得的谈判，不要调用任何工具——直接结束回复以"
            "结束本回合。你必须始终给出一个动作（工具调用）"
            "或明确决定——空回复或沉默回复绝不可接受。"
        ),
        "state_header": "当前游戏状态",
        "resources_header": "你的当前资源",
        "resources_delta_header": "自上次唤醒以来的变化：",
        "resources_delta_none": "无变化",
        "events_header": "自你上个回合以来的事件",
        "tools_instruction": (
            "仅使用游戏插件提供的四个 `tradecraft_*` 工具。要结束回合，请不要调用任何工具——直接结束回复。"
        ),
        "map_topology_header": "地图拓扑（本局固定，不可变）",
        "player_identity": "你是 TradeCraft 玩家 P{pid}。要讲策略、简洁、果断。",
        "operator_note": "对局调度器是操作者，请遵循它的回合提示。",
    },
)


# Merge the four extra UN languages shipped in ``catalog_extra.py``.
try:
    from catalog_extra import EXTRA

    CATALOGS.update(EXTRA)
except ImportError:  # pragma: no cover - engine runs fine without extras
    pass


def get_catalog(lang: str) -> dict:
    return CATALOGS.get(lang, CATALOGS["en"])


def make_translator(lang: str):
    """Return a ``t(key, default=..., **fmt)`` bound to ``lang``.

    Missing keys resolve to ``default`` (or the key itself), so a partially
    translated catalog always degrades to a safe string instead of crashing.
    """
    cat = get_catalog(lang)

    def t(key: str, default: str | None = None, **fmt):
        msg = cat.get(key)
        if msg is None:
            # Missing in the target language's catalog → fall back to English
            # text (never the raw key name), so partially translated languages
            # stay readable.
            msg = CATALOGS["en"].get(key)
        if msg is None:
            msg = default if default is not None else key
        if fmt:
            return msg.format(**fmt)
        return msg

    return t