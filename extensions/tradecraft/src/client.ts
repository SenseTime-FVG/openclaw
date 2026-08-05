/**
 * Unix socket client for the TradeCraft Python game engine.
 *
 * Maintains a persistent connection to the engine. Each tool call sends a
 * request and waits for the response on the same socket. The engine handles
 * turn-blocking transparently: if it's not this player's turn, the response
 * is deferred until the turn returns, with accumulated opponent actions.
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
 * and waits for the matching response. The engine may block (defer) the
 * response when it's not this player's turn — the client just waits.
 */
export class TradeCraftClient {
  private socket: Socket | null = null;
  private socketPath: string;
  private responseQueue: Array<{
    resolve: (value: EngineResponse) => void;
    reject: (reason: Error) => void;
  }> = [];
  private recvBuf = "";

  constructor(socketPath: string) {
    this.socketPath = socketPath;
  }

  /** Ensure the socket is connected. */
  private async ensureConnected(): Promise<Socket> {
    if (this.socket && !this.socket.destroyed) {
      return this.socket;
    }
    return this.connect();
  }

  /** Open a new connection. */
  private connect(): Promise<Socket> {
    return new Promise((resolve, reject) => {
      const socket = createConnection(this.socketPath);
      const timer = setTimeout(() => {
        socket.destroy();
        reject(
          new Error(`TradeCraft engine connection timed out (${this.socketPath})`),
        );
      }, 10000);

      socket.on("connect", () => {
        clearTimeout(timer);
        this.socket = socket;
        resolve(socket);
      });

      socket.on("data", (chunk: Buffer) => {
        this.recvBuf += chunk.toString("utf-8");
        this.drainResponses();
      });

      socket.on("error", (err: Error & { code?: string }) => {
        clearTimeout(timer);
        this.socket = null;
        if (err.code === "ENOENT" || err.code === "ECONNREFUSED") {
          reject(
            new Error(
              `TradeCraft engine not running at ${this.socketPath}: ${err.message}`,
            ),
          );
        } else {
          reject(err);
        }
        // Reject any queued requests
        this.flushQueue(err);
      });

      socket.on("close", () => {
        this.socket = null;
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

  /**
   * Send a request to the engine and wait for the response.
   * May block for an extended period if it's not this player's turn —
   * the engine defers the response until the turn returns.
   */
  async request(
    action: string,
    params: Record<string, unknown>,
  ): Promise<EngineResponse> {
    const socket = await this.ensureConnected();
    return new Promise((resolve, reject) => {
      this.responseQueue.push({ resolve, reject });
      socket.write(JSON.stringify({ action, params }) + "\n");
    });
  }

  /** Close the connection. */
  close(): void {
    if (this.socket) {
      this.socket.end();
      this.socket = null;
    }
  }
}

/**
 * Determine player identity from config or environment.
 */
export function resolvePlayerId(
  pluginConfig?: Record<string, unknown>,
): number {
  if (pluginConfig && typeof pluginConfig.playerId === "number") {
    return pluginConfig.playerId as number;
  }
  const envVal = process.env.TRADECRAFT_PLAYER_ID;
  if (envVal !== undefined) {
    const parsed = parseInt(envVal, 10);
    if (!isNaN(parsed) && (parsed === 0 || parsed === 1)) {
      return parsed;
    }
  }
  return 0;
}

/**
 * Resolve the socket path from config or fall back to default.
 */
export function resolveSocketPath(
  pluginConfig?: Record<string, unknown>,
): string {
  if (pluginConfig && typeof pluginConfig.socketPath === "string") {
    return pluginConfig.socketPath as string;
  }
  return process.env.TRADECRAFT_SOCKET ?? "/tmp/tradecraft.sock";
}
