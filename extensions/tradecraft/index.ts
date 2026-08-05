/**
 * TradeCraft Plugin — Multi-agent resource trading game for LLM RL training.
 *
 * Registers 8 tools that communicate with the Python game engine via a
 * persistent Unix socket connection.
 *
 * The engine handles turn-blocking transparently: turn-gated tool calls
 * during the opponent's turn are deferred and only return when this
 * player's turn arrives. Universal tools (view, accept, reject, counter)
 * execute immediately regardless of turn.
 *
 * Tools:
 *   - tradecraft_view_game_state  — full game state
 *   - tradecraft_collect_resources — gather resources from public pool
 *   - tradecraft_purchase_building — buy a building card
 *   - tradecraft_propose_trade     — initiate two-step trade negotiation
 *   - tradecraft_accept_trade      — accept pending trade (universal)
 *   - tradecraft_reject_trade      — reject pending trade (universal)
 *   - tradecraft_counter_offer     — counter with new terms (universal)
 *   - tradecraft_claim_project     — claim a public project
 *
 * Architecture:
 *   Agent (TS plugin) ══persistent Unix socket══▶ Python Engine (daemon)
 *
 * Player identity is resolved from plugin config or TRADECRAFT_PLAYER_ID env var.
 */

import { definePluginEntry, type AnyAgentTool } from "openclaw/plugin-sdk/plugin-entry";
import type { AgentToolResult } from "openclaw/plugin-sdk/agent-core";

import {
  TradeCraftClient,
  resolvePlayerId,
  resolveSocketPath,
} from "./src/client.js";
import {
  ViewGameStateSchema,
  CollectResourcesSchema,
  PurchaseBuildingSchema,
  ProposeTradeSchema,
  ClaimProjectSchema,
  AcceptTradeSchema,
  RejectTradeSchema,
  CounterOfferSchema,
} from "./src/tools.js";

// ── Helpers ────────────────────────────────────────────────────────────────

function toolResult(text: string, details?: unknown): AgentToolResult<unknown> {
  return {
    content: [{ type: "text", text }],
    details,
  };
}

function toolError(message: string): AgentToolResult<unknown> {
  return {
    content: [{ type: "text", text: `Error: ${message}` }],
    details: { error: message },
  };
}

// ── Tool Factory ───────────────────────────────────────────────────────────

function createTradeCraftTools(client: TradeCraftClient): AnyAgentTool[] {
  async function callEngine(
    action: string,
    params: Record<string, unknown>,
  ): Promise<AgentToolResult<unknown>> {
    try {
      const resp = await client.request(action, params);
      if (!resp.ok) {
        return toolError(resp.error ?? "Unknown engine error");
      }
      return toolResult(JSON.stringify(resp.result, null, 2), resp.result);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      return toolError(msg);
    }
  }

  return [
    {
      label: "TradeCraft: View Game State",
      name: "tradecraft_view_game_state",
      description: [
        "View the current state of the TradeCraft game, including:",
        "- Your resources and buildings",
        "- Opponent's visible resources and buildings",
        "- Available buildings in the market (L1, L2, L3)",
        "- Public projects and claim status",
        "- Current round, whose turn it is, and remaining actions",
        "",
        "Call this at the start of each turn to plan your strategy.",
      ].join("\n"),
      parameters: ViewGameStateSchema,
      execute: (_toolCallId, _params) =>
        callEngine("view_game_state", {}),
    },
    {
      label: "TradeCraft: Collect Resources",
      name: "tradecraft_collect_resources",
      description: [
        "Collect resources from the public pool.",
        "You may either:",
        "- Collect 2 of the SAME resource type, e.g. {\"wood\": 2}",
        "- Collect 1 each of 3 DIFFERENT resource types, e.g. {\"wood\": 1, \"stone\": 1, \"iron\": 1}",
        "",
        "Gold cannot be collected directly — it must be obtained through trade or building income.",
        "Resources: wood, stone, iron, crystal, silk, gold",
        "",
        "Note: if it is not your turn when you call this, the engine will",
        "automatically wait until your turn and then execute the action.",
      ].join("\n"),
      parameters: CollectResourcesSchema,
      execute: (_toolCallId, params) =>
        callEngine("collect_resources", params as Record<string, unknown>),
    },
    {
      label: "TradeCraft: Purchase Building",
      name: "tradecraft_purchase_building",
      description: [
        "Purchase a building card from the market using your resources.",
        "Gold acts as a wildcard — it can substitute for any resource at 1:1.",
        "",
        "Buildings provide points and resource income each round:",
        "- L1: 1 point, small income",
        "- L2: 2 points, medium income",
        "- L3: 3 points, large income",
        "",
        "Use view_game_state first to see available buildings and their costs.",
        "The market is first-come-first-served.",
        "",
        "Note: if it is not your turn, the engine will wait until your turn,",
        "then attempt the purchase with the updated market state.",
      ].join("\n"),
      parameters: PurchaseBuildingSchema,
      execute: (_toolCallId, params) =>
        callEngine("purchase_building", params as Record<string, unknown>),
    },
    {
      label: "TradeCraft: Propose Trade",
      name: "tradecraft_propose_trade",
      description: [
        "Initiate a two-step trade negotiation with your opponent.",
        "Specify what you OFFER and what you REQUEST.",
        "",
        "Example: offer {\"wood\": 1}, request {\"stone\": 1}",
        "This proposes trading 1 wood for 1 stone.",
        "",
        "The opponent will see this proposal and can respond with:",
        "- accept_trade: agree to the terms",
        "- reject_trade: decline the proposal",
        "- counter_offer: propose different terms",
        "",
        "Both players must have the offered/requested resources at the time of acceptance.",
        "Completed trades count toward the Trade Master project.",
        "",
        "This consumes one action. Negotiation response tools (accept/reject/counter)",
        "do NOT consume actions and can be called during either player's turn.",
      ].join("\n"),
      parameters: ProposeTradeSchema,
      execute: (_toolCallId, params) =>
        callEngine("propose_trade", params as Record<string, unknown>),
    },
    {
      label: "TradeCraft: Accept Trade",
      name: "tradecraft_accept_trade",
      description: [
        "Accept the current pending trade proposal.",
        "",
        "This executes the trade: resources are exchanged, and both players'",
        "trade counts are incremented.",
        "",
        "Can be called at any time — even during the opponent's turn.",
        "Does NOT consume an action.",
      ].join("\n"),
      parameters: AcceptTradeSchema,
      execute: (_toolCallId, _params) =>
        callEngine("accept_trade", {}),
    },
    {
      label: "TradeCraft: Reject Trade",
      name: "tradecraft_reject_trade",
      description: [
        "Reject the current pending trade proposal.",
        "",
        "Clears the pending trade. No resources change hands.",
        "",
        "Can be called at any time — even during the opponent's turn.",
        "Does NOT consume an action.",
      ].join("\n"),
      parameters: RejectTradeSchema,
      execute: (_toolCallId, _params) =>
        callEngine("reject_trade", {}),
    },
    {
      label: "TradeCraft: Counter Offer",
      name: "tradecraft_counter_offer",
      description: [
        "Reject the current trade proposal and make a new counter-offer.",
        "",
        "Specify what you OFFER and what you REQUEST (same format as propose_trade).",
        "The opponent then becomes the responder and can accept, reject, or counter.",
        "",
        "Can be called at any time — even during the opponent's turn.",
        "Does NOT consume an action.",
      ].join("\n"),
      parameters: CounterOfferSchema,
      execute: (_toolCallId, params) =>
        callEngine("counter_offer", params as Record<string, unknown>),
    },
    {
      label: "TradeCraft: Claim Project",
      name: "tradecraft_claim_project",
      description: [
        "Claim a public project if you meet its conditions.",
        "Projects are first-come-first-served — only one player can claim each.",
        "",
        "Available projects:",
        "- trade_master (3pts): Complete 5+ trades",
        "- build_master (4pts): Own 6+ buildings",
        "- tech_pioneer (6pts): Own 3 L3 buildings",
        "- resource_tycoon (3pts): Hold 15+ resources",
        "- wonder_builder (5pts): Own buildings of 5+ different resource types",
        "",
        "Use view_game_state to check project conditions and claim status.",
        "",
        "Note: if it is not your turn, the engine will wait until your turn",
        "before checking conditions and claiming.",
      ].join("\n"),
      parameters: ClaimProjectSchema,
      execute: (_toolCallId, params) =>
        callEngine("claim_project", params as Record<string, unknown>),
    },
  ];
}

// ── Plugin Entry ───────────────────────────────────────────────────────────

export default definePluginEntry({
  id: "tradecraft",
  name: "TradeCraft",
  description:
    "Multi-agent resource trading game for RL training — 6 resources, building investment, bilateral trading, long-term planning.",
  register(api) {
    const pluginConfig = (api.pluginConfig as Record<string, unknown>) ?? {};
    const socketPath = resolveSocketPath(pluginConfig);
    const playerId = resolvePlayerId(pluginConfig);

    api.logger.info(
      `[tradecraft] Connecting to engine at ${socketPath} as player ${playerId}…`,
    );

    const client = new TradeCraftClient(socketPath);

    const tools = createTradeCraftTools(client);
    for (const tool of tools) {
      api.registerTool(tool);
    }

    api.logger.info(
      `[tradecraft] Registered ${tools.length} tools (player ${playerId}).`,
    );
  },
});
