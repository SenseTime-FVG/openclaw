/**
 * Unix socket client for the TradeCraft Python game engine.
 *
 * Maintains a persistent connection to the engine. Each tool call sends a
 * request and waits for the response on the same socket. The engine rejects
 * turn-gated actions immediately when they are called out of turn.
 *
 * Protocol: newline-delimited JSON over Unix domain socket.
 */

import { createConnection } from "node:net";
import type { Socket } from "node:net";

export interface EngineRequest {
  action: string;
  params: Record<string, unknown>;
}

export interface EngineResponse {
  ok: boolean;
  result?: unknown;
  error?: string;
  turn_over?: boolean;
}

/**
 * Persistent socket client for the TradeCraft engine.
 *
 * Opens one long-lived connection. Each call to `request()` sends a message
 * and waits for the matching response. Calls are serialized because the
 * protocol has no request id.
 */
export class TradeCraftClient {
  private socket: Socket | null = null;
  private connectPromise: Promise<Socket> | null = null;
  private requestTail: Promise<void> = Promise.resolve();
  private responseQueue: Array<{
    resolve: (value: EngineResponse) => void;
    reject: (reason: Error) => void;
  }> = [];
  private recvBuf = "";

  constructor(
    private readonly socketPath: string,
    private readonly playerId: number,
  ) {}

  /** Establish the player registration eagerly during plugin startup. */
  async start(): Promise<void> {
    await this.ensureConnected();
  }

  /** Ensure the socket is connected. */
  private async ensureConnected(): Promise<Socket> {
    if (this.socket && !this.socket.destroyed) {
      return this.socket;
    }
    if (!this.connectPromise) {
      this.connectPromise = this.connect().finally(() => {
        this.connectPromise = null;
      });
    }
    return this.connectPromise;
  }

  /** Open a new connection. */
  private connect(): Promise<Socket> {
    return new Promise((resolve, reject) => {
      const socket = createConnection(this.socketPath);
      let registered = false;
      const registration = {
        resolve: (response: EngineResponse) => {
          clearTimeout(timer);
          if (response.ok) {
            registered = true;
            resolve(socket);
          } else {
            socket.destroy();
            reject(new Error(response.error ?? "Player registration failed"));
          }
        },
        reject,
      };
      const timer = setTimeout(() => {
        this.removeQueuedResponse(registration);
        socket.destroy();
        reject(new Error(`TradeCraft engine connection timed out (${this.socketPath})`));
      }, 10000);

      socket.on("connect", () => {
        this.socket = socket;
        this.recvBuf = "";
        // Complete identity registration before the first action is sent. This
        // avoids assigning players by nondeterministic connection order.
        this.responseQueue.push(registration);
        socket.write(
          JSON.stringify({
            action: "register_player",
            params: { player_id: this.playerId },
          }) + "\n",
        );
      });

      socket.on("data", (chunk: Buffer) => {
        this.recvBuf += chunk.toString("utf-8");
        this.drainResponses();
      });

      socket.on("error", (err: Error & { code?: string }) => {
        clearTimeout(timer);
        const isCurrentSocket = this.socket === socket;
        if (isCurrentSocket) {
          this.socket = null;
        }
        if (err.code === "ENOENT" || err.code === "ECONNREFUSED") {
          reject(new Error(`TradeCraft engine not running at ${this.socketPath}: ${err.message}`));
        } else {
          reject(err);
        }
        if (isCurrentSocket) {
          this.flushQueue(err);
        }
      });

      socket.on("close", () => {
        clearTimeout(timer);
        const isCurrentSocket = this.socket === socket;
        if (isCurrentSocket) {
          this.socket = null;
          this.flushQueue(new Error("TradeCraft engine closed the socket"));
        }
        if (!registered) {
          reject(new Error("TradeCraft engine closed before player registration completed"));
        }
      });
    });
  }

  /** Parse complete messages from the receive buffer and deliver to waiting callers. */
  private drainResponses(): void {
    while (true) {
      const idx = this.recvBuf.indexOf("\n");
      if (idx < 0) break;
      const line = this.recvBuf.slice(0, idx);
      this.recvBuf = this.recvBuf.slice(idx + 1);

      try {
        const resp = JSON.parse(line) as EngineResponse;
        const pending = this.responseQueue.shift();
        if (pending) {
          pending.resolve(resp);
        }
      } catch (e) {
        const pending = this.responseQueue.shift();
        if (pending) {
          pending.reject(
            new Error(
              `Failed to parse engine response: ${e instanceof Error ? e.message : String(e)}`,
            ),
          );
        }
      }
    }
  }

  private flushQueue(err: Error): void {
    while (this.responseQueue.length > 0) {
      const pending = this.responseQueue.shift()!;
      pending.reject(err);
    }
  }

  private removeQueuedResponse(target: (typeof this.responseQueue)[number]): void {
    const index = this.responseQueue.indexOf(target);
    if (index >= 0) {
      this.responseQueue.splice(index, 1);
    }
  }

  /**
   * Send a request to the engine and wait for the response.
   * Requests are serialized because the wire protocol has no request ids and
   * matches responses to calls by FIFO order.
   */
  async request(action: string, params: Record<string, unknown>): Promise<EngineResponse> {
    const run = async (): Promise<EngineResponse> => {
      const socket = await this.ensureConnected();
      return new Promise((resolve, reject) => {
        this.responseQueue.push({ resolve, reject });
        socket.write(JSON.stringify({ action, params }) + "\n");
      });
    };
    const pending = this.requestTail.then(run, run);
    this.requestTail = pending.then(
      () => undefined,
      () => undefined,
    );
    return pending;
  }

  /** Close the connection. */
  close(): void {
    if (this.socket) {
      this.socket.end();
      this.socket = null;
    }
  }
}

const SHARED_CLIENTS_KEY = "__openclawTradeCraftClients";

type TradeCraftGlobal = typeof globalThis & {
  [SHARED_CLIENTS_KEY]?: Map<string, TradeCraftClient>;
};

/**
 * Reuse the engine connection when Gateway startup and agent-runtime prewarm
 * load the plugin in separate registries within the same process.
 */
export function getSharedTradeCraftClient(socketPath: string, playerId: number): TradeCraftClient {
  const host = globalThis as TradeCraftGlobal;
  const clients = host[SHARED_CLIENTS_KEY] ?? new Map<string, TradeCraftClient>();
  host[SHARED_CLIENTS_KEY] = clients;

  const key = `${socketPath}\u0000${playerId}`;
  const existing = clients.get(key);
  if (existing) {
    return existing;
  }

  const client = new TradeCraftClient(socketPath, playerId);
  clients.set(key, client);
  return client;
}

/**
 * Determine player identity from config or environment.
 */
export function resolvePlayerId(pluginConfig?: Record<string, unknown>): number {
  if (
    pluginConfig &&
    typeof pluginConfig.playerId === "number" &&
    Number.isInteger(pluginConfig.playerId) &&
    pluginConfig.playerId >= 0 &&
    pluginConfig.playerId <= 3
  ) {
    return pluginConfig.playerId;
  }
  const envVal = process.env.TRADECRAFT_PLAYER_ID;
  if (envVal !== undefined) {
    const parsed = parseInt(envVal, 10);
    if (!isNaN(parsed) && parsed >= 0 && parsed <= 3) {
      return parsed;
    }
  }
  return 0;
}

/**
 * Resolve the socket path from config or fall back to default.
 */
export function resolveSocketPath(pluginConfig?: Record<string, unknown>): string {
  if (pluginConfig && typeof pluginConfig.socketPath === "string") {
    return pluginConfig.socketPath as string;
  }
  return process.env.TRADECRAFT_SOCKET ?? "/tmp/tradecraft.sock";
}

// ── Player identity from the runtime agent/session context ────────────
//
// A single OpenClaw gateway now hosts one agent per player, so player
// identity is no longer baked into a per-gateway config. The plugin tool
// factory receives the active agent/session in its context; we map that to
// the TradeCraft player id (0-3) at request time.

const AGENT_PLAYER_RE = /^p?(\d{1,2})$/i;
const SESSION_AGENT_RE = /^agent:([^:]+)/i;

/** Parse a player id (0-3) from an agent id such as "p0" / "P1". */
export function playerIdFromAgentId(agentId?: string | null): number | null {
  if (typeof agentId !== "string") {
    return null;
  }
  const match = AGENT_PLAYER_RE.exec(agentId.trim());
  if (!match) {
    return null;
  }
  const id = parseInt(match[1], 10);
  return Number.isInteger(id) && id >= 0 && id <= 3 ? id : null;
}

/** Parse a player id from a session key such as "agent:p0:tradecraft-...". */
export function playerIdFromSessionKey(sessionKey?: string | null): number | null {
  if (typeof sessionKey !== "string") {
    return null;
  }
  const match = SESSION_AGENT_RE.exec(sessionKey.trim());
  if (!match) {
    return null;
  }
  return playerIdFromAgentId(match[1]);
}

/**
 * Resolve which player the current tool call belongs to.
 *
 * Prefers the runtime agent id (``ctx.agentId``), then the session key
 * (``ctx.sessionKey``), and finally falls back to the static
 * ``playerId``/``TRADECRAFT_PLAYER_ID`` config for single-agent setups.
 */
export function resolvePlayerIdFromContext(
  ctx: { agentId?: string | null; sessionKey?: string | null } | undefined,
  pluginConfig?: Record<string, unknown>,
): number {
  const fromAgent = playerIdFromAgentId(ctx?.agentId);
  if (fromAgent !== null) {
    return fromAgent;
  }
  const fromSession = playerIdFromSessionKey(ctx?.sessionKey);
  if (fromSession !== null) {
    return fromSession;
  }
  return resolvePlayerId(pluginConfig);
}

/**
 * Player ids whose engine connections this gateway should open eagerly at
 * plugin load, so the scheduler's "all players registered" gate is met before
 * any agent turn runs. Falls back to the static ``playerId``.
 */
export function resolveConfiguredPlayerIds(pluginConfig?: Record<string, unknown>): number[] {
  const raw = pluginConfig?.playerIds;
  if (Array.isArray(raw)) {
    const ids = raw
      .map((value) => (typeof value === "number" ? value : parseInt(String(value), 10)))
      .filter((value) => Number.isInteger(value) && value >= 0 && value <= 3);
    const unique = Array.from(new Set(ids)).sort((a, b) => a - b);
    if (unique.length > 0) {
      return unique;
    }
  }
  return [resolvePlayerId(pluginConfig)];
}
