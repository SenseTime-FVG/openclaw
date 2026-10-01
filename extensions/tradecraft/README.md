# TradeCraft

TradeCraft is a local, deterministic, multi-agent resource game for three or four autonomous
OpenClaw players. The environment combines a Python game engine with an OpenClaw tool plugin and
an optional match runner that creates isolated player profiles, starts one Gateway per player, and
serializes LLM turns.

This plugin is an evaluation and experimentation environment. The RL training integration
(reward model, dataset, rollout) lives in a separate repository (`verl-tradecraft`), not in this
plugin; this plugin ships no model weights.

## Architecture

| Layer               | Implementation                                    | Responsibility                                                                                                                          |
| ------------------- | ------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| Match orchestration | `python/game_runner.py`                           | Creates player profiles, starts processes, schedules agents, delivers messages, retries transient failures, and writes match artifacts. |
| OpenClaw runtime    | One isolated Gateway and agent session per player | Runs the selected model and exposes only the four TradeCraft tools.                                                                    |
| Tool plugin         | `index.ts` and `src/tools.ts`                     | Registers tools, validates model-facing parameters with TypeBox, and converts engine responses to `AgentToolResult`.                    |
| Protocol adapter    | `src/client.ts`                                   | Maintains one registered Unix-domain-socket connection per player and serializes NDJSON requests.                                       |
| Game engine         | `python/engine.py`                                | Owns the canonical state machine, validates every action, resolves trades, produces resources, and scores the match.                    |
| Board generator     | `python/map_generator.py`                         | Builds deterministic hex, vertex, and edge topology from a seed.                                                                        |

The request path is:

```text
LLM player
  -> TradeCraft tool
  -> TypeScript plugin
  -> registered Unix socket connection
  -> Python engine
  -> state snapshot and tool result
  -> LLM player
```

The runner observes the engine through atomic `state.json` snapshots. Player messages are stored
by the engine and then injected into recipient OpenClaw sessions by the runner. The socket protocol
does not send unsolicited responses because it has no request IDs and relies on FIFO ordering.

Each wake injects the player's dynamic state plus the list of state-affecting events (builds,
trade settlements, project claims, and production rolls) that occurred since its previous turn,
newest last. The player's own actions and chat messages are excluded. The static map topology
lives in the player's workspace context rather than being resent each wake.

## Game model

- Three or four players.
- A deterministic 19-hex board with 54 vertices and 72 edges.
- A snake-draft placement phase: `P0 -> ... -> PN -> PN -> ... -> P0`.
- Twelve production rounds. Each round resolves two `2d6` production rolls; a player who gains
  nothing from either roll receives one gold. Each player is scheduled twice per round, one action
  per wake; a player with no productive action ends its reply without a tool call (there is no
  pass tool).
- Five standard resources (`wood`, `stone`, `iron`, `crystal`, and `silk`) plus `gold`.
- Roads, settlements, two upgrade levels, bilateral trades, public projects, and final scoring.
- Gold can cover missing build resources at a one-for-one rate after explicit gold costs are
  reserved.

The seed controls the dice sequence (and is the fallback map seed); an optional independent
`map_seed` controls the resource layout and tile numbers. Map generation always applies balance
constraints (no adjacent same-resource hexes; the red numbers 6 and 8 are never adjacent). Reusing
a seed makes engine behavior reproducible, but LLM decisions can still vary.

### Tools

| Tool                       | Turn rule                                                     | Action cost |
| -------------------------- | ------------------------------------------------------------- | ----------: |
| `tradecraft_build`         | Placement-gated during setup; play-phase otherwise.           |           1 |
| `tradecraft_propose_trade`  | Play-phase only; one pending trade at a time.                  |           1 |
| `tradecraft_send_message`  | Play-phase only; broadcasts to all other players.              |           1 |
| `tradecraft_claim_project` | Play-phase only; project must be unclaimed and eligible.       |           1 |

These four tools are the only model-facing tools. The engine also implements `view_game_state`,
but the runner calls it and injects the resulting state into each wake message; players have no
tool to request it themselves. (An earlier revision exposed a larger tool set — including `pass`
and `respond_trade` — which no longer exists.)

`tradecraft_send_message` is a normal negotiation action: a player can broadcast concrete
resource needs, acceptable exchange terms, or a request for future offers. Because it consumes an
action, vague or repetitive messages should be avoided. To end a turn, a player stops calling
tools entirely — there is no pass tool; ending the reply without a tool call hands control back
to the runner.

Trade response is not a tool. When a pending trade names a player as responder, the runner wakes
it and the responder expresses its decision as a single JSON object in its final message text:

```json
{
  "decision": "accept",
  "reason": "Crystal and iron are surplus to me."
}
```

`decision` is `accept` or `reject` (there is no counteroffer). The runner parses this JSON and
submits it to the engine, which delivers `reason` to the proposer. A missing or malformed decision
is treated as a rejection with a system-supplied reason.

## Action limits and scheduling

Turn pacing is owned by the match runner, not the engine. The engine exposes a configurable
`limit` (default 3, `--limit` on the runner / engine) as a reserved knob, and tracks per-player
action health — `total_actions`, `illegal_actions`, and `forfeits` — which is surfaced in the
settled state under `legality` for action-legality rewards. The engine does not itself count
consecutive failures, warn, or forfeit turns.

The one pacing rule the engine enforces is **one action per wake**: once a player commits its
scheduled action, any further tool call in the same wake is rejected (`acted_this_wake`). A player
ends a turn by not calling any tool. The runner drives the snake-draft placement order and, during
play, schedules each player twice per round; it forfeits a player whose agent fails, stalls, or
returns no reply, so a silent player never blocks the match.

## Scheduling contract

The runner schedules one player invocation at a time:

1. If `pending_trade` exists, schedule its `responder`.
2. Otherwise, schedule `current_player`.
3. Stop when `game_over` is true.

An agent invocation is not preempted midway through a tool sequence. The player prompt therefore
defines two important yield points:

- After a successful trade proposal, the proposer stops immediately so the runner can schedule the
  responder. If the proposer still has an unused action after the trade resolves, the runner
  schedules it again.
- A player scheduled as the trade responder responds exactly once, includes feedback, and stops.
  It does not continue into normal turn actions in the same invocation.

These rules live in `prompts/player.md` and are repeated in the runner's dynamic wake message.

## Error feedback contract

Every engine action either commits and returns `{"ok": true, "result": …}` or is rejected
with `{"ok": false, "error": "…"}`. A rejected request never mutates game state, never records a
game action, and never advances the turn or round. Rejection is how the engine teaches the model
the rules, so the `error` string must be self-contained and actionable — the player must be able
to correct the request from the message alone, without re-reading the full state:

1. **What** was rejected — the specific id, resource, or condition (e.g. `Invalid vertex: v99`, `Unknown project: hex_lord`).
2. **Why** it failed — the rule violated (e.g. distance, affordability, turn order, ownership).
3. **How to fix it** — the requirement and, when applicable, current value (e.g. `Have: {…}` or the remaining condition).

Canonical examples:

```text
Cannot afford settlement. Cost: {wood:1, stone:1, iron:1, silk:1}, Have: {wood:1, stone:0, …}
Too close to P2's building at v41 (min 2 edges between settlements).
Conditions not met for 'Trade Master': requires complete 8+ trades; you currently have 3 trades completed.
Not your turn. Current: P1.
```

Partial-failure defects — a bare `ok: false` with no explanation, or state mutated before a
validation error is discovered — are treated as correctness bugs, not cosmetic issues.

## Requirements

- macOS, Linux, or WSL with Unix domain socket support.
- Python 3.9 or newer; the engine and runner use only the standard library.
- A repository-supported Node.js version and pnpm for OpenClaw-backed matches.
- An installed OpenClaw workspace dependency tree (`pnpm install`).

Mock matches need no model, API key, or running Gateway.

## Run a mock match

The checked-in example configuration defaults to mock mode:

```bash
python3 extensions/tradecraft/python/game_runner.py \
  --config extensions/tradecraft/game_runner.example.json \
  --mode mock
```

Mock players complete placement and all twelve rounds without calling an LLM. Each round resolves
two production rolls, and a player who gains nothing from either roll receives one gold. This verifies the map,
engine protocol, state transitions, scheduling loop, and terminal scoring.

## Run an OpenClaw match

Copy `game_runner.example.json` outside the repository or create a local JSON configuration. Keep
credentials in environment variables, not in the file:

```json
{
  "mode": "openclaw",
  "players": 3,
  "seed": 42,
  "openclaw": {
    "model": "provider/model-id",
    "apiKeyEnv": "PROVIDER_API_KEY",
    "thinking": "low",
    "agentTimeoutSeconds": 600,
    "agentRetryAttempts": 3,
    "agentRetryBaseSeconds": 5,
    "agentRetryMaxSeconds": 60,
    "rateLimitRetrySeconds": 120,
    "rateLimitMaxWaitSeconds": 3600
  }
}
```

Then run:

```bash
export PROVIDER_API_KEY="..."
python3 extensions/tradecraft/python/game_runner.py --config /path/to/local-config.json
```

`openclaw.model` must use `provider/model` form. `apiKeyEnv` is an environment variable name, not
the credential itself. The selected OpenClaw provider must recognize that variable. Provider
definitions, base URLs, and model catalogs can be added through `openclaw.configOverlay`; the
overlay is merged into every generated player configuration.

To give each player a separate credential, replace `apiKeyEnv` with `apiKeyEnvs`. The array length
must equal `players`, and every variable name must be unique. Per-player keys require a matching
custom provider under `openclaw.configOverlay.models.providers.<provider-id>` so the runner can
write the correct environment reference into each isolated configuration.

Useful preparation modes:

```bash
# Generate isolated profiles without starting a match.
python3 extensions/tradecraft/python/game_runner.py \
  --config /path/to/local-config.json \
  --prepare-only

# Start the engine and Gateways, verify registration and session injection, then stop.
python3 extensions/tradecraft/python/game_runner.py \
  --config /path/to/local-config.json \
  --mode openclaw \
  --check-infrastructure

# Keep the engine and Gateways running without invoking an LLM.
python3 extensions/tradecraft/python/game_runner.py \
  --config /path/to/local-config.json \
  --mode openclaw \
  --serve-infrastructure
```

Generated profiles share configured base ports and profile paths, so one runner configuration is
intended to execute matches serially rather than concurrently.

## Retry and state review

The runner classifies failed OpenClaw agent invocations before deciding whether to retry.

Retryable classes:

- timeout
- rate limit
- provider overload
- transport/network failure
- HTTP 500, 502, 503, or 504

Authentication, quota/billing, model/configuration, and unknown failures stop the match immediately.
Regular retry attempts use capped exponential backoff with equal jitter. If regular retries end on
a rate-limit error, the runner can enter a longer rate-limit wait loop controlled by
`rateLimitRetrySeconds` and `rateLimitMaxWaitSeconds`.

After every failed attempt and wait, the runner rereads the engine snapshot. If the timed-out agent
actually completed a game action, the runner records `agent_failure_after_progress` and continues
from the new state instead of replaying the invocation. A final observation window performs the
same check before the match is aborted.

## Artifacts

The default runtime root is `runs/tradecraft/`:

```text
runs/tradecraft/
├── profiles/player-N/
│   ├── openclaw.json
│   ├── state/
│   └── workspace/
└── matches/<game-id>/
    ├── resolved-config.json
    ├── state.json
    ├── engine-events.jsonl
    ├── runner-events.jsonl
    ├── engine.log
    ├── gateway-pN.log
    └── agent-pN.jsonl
```

`runs/` is ignored by Git. Generated profiles contain local Gateway tokens and can reference API
key environment variable names, so treat the entire runtime directory as local experiment data.

## Validation

Run the focused Python suite from the plugin's Python directory:

```bash
cd extensions/tradecraft/python
python3 -m unittest test_engine.py test_game_runner.py
```

Run the plugin TypeScript check from the repository root:

```bash
pnpm tsgo:extensions
```

The Python tests cover deterministic topology, three- and four-player placement, build and trade
validation, targeted trade feedback, retry classification and backoff, message delivery, profile
isolation, and complete mock matches.

## Current limitations

- The engine is a trusted local single-match process; it has no authentication, encryption,
  reconnect protocol, or mid-match recovery.
- The NDJSON protocol has no request IDs and supports only serialized requests per player.
- The runner uses fixed local ports and profile directories and does not coordinate concurrent
  matches.
- Player messages and model transcripts are experiment artifacts, not a durable game database.
- No training, ELO pool, replay UI, or distributed rollout layer is included.
