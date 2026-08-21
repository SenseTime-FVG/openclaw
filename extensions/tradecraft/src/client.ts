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
