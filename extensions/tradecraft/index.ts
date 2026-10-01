/**
 * TradeCraft plugin entry.
 *
 * The plugin exposes the four actions implemented by the Python engine and
 * translates OpenClaw tool calls into newline-delimited JSON over a Unix
 * domain socket. Exiting a turn is signalled by not calling any tool.
 */

import type { AgentToolResult } from "openclaw/plugin-sdk/agent-core";
import { definePluginEntry, type AnyAgentTool } from "openclaw/plugin-sdk/plugin-entry";
import {
  getSharedTradeCraftClient,
  resolvePlayerId,
  resolveSocketPath,
  type TradeCraftClient,
} from "./src/client.js";
import {
  BuildSchema,
  ClaimProjectSchema,
  ProposeTradeSchema,
  SendMessageSchema,
} from "./src/tools.js";

function toolResult(text: string, details?: unknown): AgentToolResult<unknown> {
  return { content: [{ type: "text", text }], details };
}

function toolError(message: string): AgentToolResult<unknown> {
  return {
    content: [{ type: "text", text: `Error: ${message}` }],
    details: { error: message },
  };
}

function createTradeCraftTools(client: TradeCraftClient): AnyAgentTool[] {
  async function callEngine(
    action: string,
    params: Record<string, unknown>,
  ): Promise<AgentToolResult<unknown>> {
    try {
      const response = await client.request(action, params);
      if (!response.ok) {
        return toolError(response.error ?? "Unknown engine error");
      }
      return toolResult(JSON.stringify(response.result), response.result);
    } catch (error: unknown) {
      return toolError(error instanceof Error ? error.message : String(error));
    }
  }

  return [
    {
      label: "TradeCraft: Build",
      name: "tradecraft_build",
      description: [
        "Build a road or settlement, or upgrade one of your buildings.",
        "Use type=road with edge_id, type=settlement with vertex_id, or type=upgrade with vertex_id and to_level.",
        "During initial placement, place a settlement and then an adjacent road.",
        "During play this is turn-gated and consumes one action when successful.",
      ].join("\n"),
      parameters: BuildSchema,
      execute: (_toolCallId, params) => callEngine("build", params as Record<string, unknown>),
    },
    {
      label: "TradeCraft: Propose Trade",
      name: "tradecraft_propose_trade",
      description: [
        "Offer resources in exchange for resources held by another player.",
        "Set target to a player id (0-3); if omitted, the next player is selected.",
        "This is turn-gated and consumes one action when successful.",
      ].join("\n"),
      parameters: ProposeTradeSchema,
      execute: (_toolCallId, params) =>
        callEngine("propose_trade", params as Record<string, unknown>),
    },
    {
      label: "TradeCraft: Send Message",
      name: "tradecraft_send_message",
      description: [
        "Broadcast a negotiation message to every other player's OpenClaw session.",
        "Use it to state resource needs, communicate acceptable terms, solicit offers, or coordinate future trades.",
        "This is turn-gated and consumes one action when successful.",
      ].join("\n"),
      parameters: SendMessageSchema,
      execute: (_toolCallId, params) =>
        callEngine("send_message", params as Record<string, unknown>),
    },
    {
      label: "TradeCraft: Claim Project",
      name: "tradecraft_claim_project",
      description:
        "Claim a first-come-first-served public project after its engine-side condition is met. This is turn-gated and consumes one action when successful.",
      parameters: ClaimProjectSchema,
      execute: (_toolCallId, params) =>
        callEngine("claim_project", params as Record<string, unknown>),
    },
  ];
}

export default definePluginEntry({
  id: "tradecraft",
  name: "TradeCraft",
  description:
    "Multi-agent hex-map resource game for spatial planning, building, and bilateral trading.",
  register(api) {
    const pluginConfig = (api.pluginConfig as Record<string, unknown>) ?? {};
    const socketPath = resolveSocketPath(pluginConfig);
    const playerId = resolvePlayerId(pluginConfig);

    api.logger.info(`[tradecraft] Connecting to engine at ${socketPath} as player ${playerId}…`);

    const client = getSharedTradeCraftClient(socketPath, playerId);
    const tools = createTradeCraftTools(client);
    for (const tool of tools) {
      api.registerTool(tool);
    }

    void client.start().catch((error: unknown) => {
      const message = error instanceof Error ? error.message : String(error);
      api.logger.warn(`[tradecraft] Initial engine connection failed: ${message}`);
    });

    api.logger.info(`[tradecraft] Registered ${tools.length} tools (player ${playerId}).`);
  },
});
