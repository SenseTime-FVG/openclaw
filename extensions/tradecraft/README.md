# TradeCraft

TradeCraft is a local, deterministic, multi-agent resource game for three or four autonomous
OpenClaw players. The environment combines a Python game engine with an OpenClaw tool plugin and
an optional match runner that creates isolated player profiles, starts one Gateway per player, and
serializes LLM turns.

This plugin is an evaluation and experimentation environment. It does not include an RL training
pipeline, reward model, dataset, rollout service, or model weights.

## Architecture

| Layer               | Implementation                                    | Responsibility                                                                                                                          |
| ------------------- | ------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| Match orchestration | `python/game_runner.py`                           | Creates player profiles, starts processes, schedules agents, delivers messages, retries transient failures, and writes match artifacts. |
| OpenClaw runtime    | One isolated Gateway and agent session per player | Runs the selected model and exposes only the six TradeCraft tools.                                                                      |
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

## Game model

- Three or four players.
- A deterministic 19-hex board with 54 vertices and 72 edges.
- A snake-draft placement phase: `P0 -> ... -> PN -> PN -> ... -> P0`.
- Ten production rounds with two successful actions per player turn.
- Five standard resources (`wood`, `stone`, `iron`, `crystal`, and `silk`) plus `gold`.
- Roads, settlements, two upgrade levels, bilateral trades, public projects, and final scoring.
- Gold can cover missing build resources at a one-for-one rate after explicit gold costs are
  reserved.

The seed controls the map, resource layout, tile numbers, and dice sequence. Reusing a seed makes
engine behavior reproducible, but LLM decisions can still vary.

## Tools

| Tool                         | Turn rule                                                      |   Action cost |
| ---------------------------- | -------------------------------------------------------------- | ------------: |
| `tradecraft_view_game_state` | Available at any time, including after game over.              |             0 |
| `tradecraft_build`           | Placement-gated during setup; current-player-only during play. | 1 during play |
| `tradecraft_propose_trade`   | Current player only.                                           |             1 |
| `tradecraft_respond_trade`   | Available to the pending trade parties.                        |             0 |
| `tradecraft_send_message`    | Current player only; broadcasts to all other players.          |             1 |
| `tradecraft_claim_project`   | Current player only; project must be unclaimed and eligible.   |             1 |

Every trade response includes a non-empty `message` with decision-relevant feedback:

```json
{
  "action": "reject",
  "message": "I need to keep my stone for an upgrade."
}
```

For a counteroffer, include both `offer` and `request`. The engine records the feedback as part of
the response, and the runner delivers it to the other party without consuming a normal turn
action.

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

Mock players complete placement and all ten rounds without calling an LLM. This verifies the map,
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
