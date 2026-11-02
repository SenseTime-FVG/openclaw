/**
 * TradeCraft plugin entry.
 *
 * The plugin exposes the four actions implemented by the Python engine and
 * translates OpenClaw tool calls into newline-delimited JSON over a Unix
 * domain socket. Exiting a turn is signalled by not calling any tool.
 *
 * A single OpenClaw gateway hosts one agent per player (P0..P3). Tools are
 * therefore registered as factories: each factory receives the active agent
 * runtime context, resolves the player id from `ctx.agentId`/`ctx.sessionKey`,
 * and routes the call to that player's engine connection.
 */

import type { AgentToolResult } from "openclaw/plugin-sdk/agent-core";
import {
  definePluginEntry,
  type AnyAgentTool,
  type OpenClawPluginToolContext,
  type OpenClawPluginToolFactory,
} from "openclaw/plugin-sdk/plugin-entry";
import {
  getSharedTradeCraftClient,
  resolveConfiguredPlayerIds,
  resolvePlayerIdFromContext,
  resolveSocketPath,
  type TradeCraftClient,
} from "./src/client.js";
import {
  BuildSchema,
  ClaimProjectSchema,
  ProposeTradeSchema,
  SendMessageSchema,
} from "./src/tools.js";
import { ui, type ToolStrings } from "./src/i18n.js";

function toolResult(text: string, details?: unknown): AgentToolResult<unknown> {
  return { content: [{ type: "text", text }], details };
}

function toolError(message: string): AgentToolResult<unknown> {
  return {
    content: [{ type: "text", text: `Error: ${message}` }],
    details: { error: message },
  };
}

interface ToolSpec {
  name: string;
  label: string;
  description: string;
  parameters: AnyAgentTool["parameters"];
  action: string;
}

/** Build one engine-backed tool bound to a specific player's client. */
function createTool(spec: ToolSpec, client: TradeCraftClient): AnyAgentTool {
  async function callEngine(params: Record<string, unknown>): Promise<AgentToolResult<unknown>> {
    try {
      const response = await client.request(spec.action, params);
      if (!response.ok) {
        if (response.turn_over) {
          // Not an error — the turn is already complete. Render the message
          // as a neutral note (no "Error:" prefix) so the model settles its
          // turn instead of retrying on the rejection.
          return toolResult(response.error ?? "");
        }
        return toolError(response.error ?? "Unknown engine error");
      }
      return toolResult(JSON.stringify(response.result), response.result);
    } catch (error: unknown) {
      return toolError(error instanceof Error ? error.message : String(error));
    }
  }

  return {
    label: spec.label,
    name: spec.name,
    description: spec.description,
    parameters: spec.parameters,
    execute: (_toolCallId, params) => callEngine(params as Record<string, unknown>),
  };
}

function toolSpecs(strings: ToolStrings): ToolSpec[] {
  return [
    {
      name: "tradecraft_build",
      label: strings.buildLabel,
      description: strings.buildDescription.join("\n"),
      parameters: BuildSchema,
      action: "build",
    },
    {
      name: "tradecraft_propose_trade",
      label: strings.proposeTradeLabel,
      description: strings.proposeTradeDescription.join("\n"),
      parameters: ProposeTradeSchema,
      action: "propose_trade",
    },
    {
      name: "tradecraft_send_message",
      label: strings.sendMessageLabel,
      description: strings.sendMessageDescription.join("\n"),
      parameters: SendMessageSchema,
      action: "send_message",
    },
    {
      name: "tradecraft_claim_project",
      label: strings.claimProjectLabel,
      description: strings.claimProjectDescription,
      parameters: ClaimProjectSchema,
      action: "claim_project",
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
    const strings = ui(pluginConfig.language as string | undefined);
    api.logger.info(
      `[tradecraft] i18n: language=${pluginConfig.language ?? "en"} build.label="${strings.buildLabel}"`,
    );
    // Localize the one parameter description the agent actually reads.
    (
      SendMessageSchema.properties.content as unknown as { description?: string }
    ).description = strings.sendMessageContentDescription;

    const playerIds = resolveConfiguredPlayerIds(pluginConfig);
    api.logger.info(
      `[tradecraft] Single-gateway mode; engine at ${socketPath}; ` +
        `eager player connections: [${playerIds.join(", ")}]`,
    );

    // Open one engine connection per player up front. The scheduler waits for
    // every player id to register, so this must happen at gateway startup — not
    // lazily on the first tool call.
    for (const playerId of playerIds) {
      const client = getSharedTradeCraftClient(socketPath, playerId);
      void client.start().catch((error: unknown) => {
        const message = error instanceof Error ? error.message : String(error);
        api.logger.warn(
          `[tradecraft] Initial engine connection for player ${playerId} failed: ${message}`,
        );
      });
    }

    const specs = toolSpecs(strings);
    for (const spec of specs) {
      const factory: OpenClawPluginToolFactory = (ctx: OpenClawPluginToolContext) => {
        const playerId = resolvePlayerIdFromContext(ctx, pluginConfig);
        const client = getSharedTradeCraftClient(socketPath, playerId);
        void client.start().catch((error: unknown) => {
          const message = error instanceof Error ? error.message : String(error);
          api.logger.warn(
            `[tradecraft] Engine connection for player ${playerId} failed: ${message}`,
          );
        });
        return createTool(spec, client);
      };
      api.registerTool(factory, { name: spec.name });
    }

    api.logger.info(
      `[tradecraft] Registered ${specs.length} tools (single gateway, ` +
        `players [${playerIds.join(", ")}]).`,
    );
  },
});