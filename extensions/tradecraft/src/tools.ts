/** TradeCraft tool schemas for the Catan-style engine protocol. */
import { Type } from "typebox";

// A zero (or omitted) amount means "this resource is not part of the trade".
// The engine strips zeros, so a two-resource swap is legal without padding all
// five keys; only the engine rejects a side that ends up empty.
const ResourceMap = Type.Object(
  {
    wood: Type.Optional(Type.Integer({ minimum: 0 })),
    brick: Type.Optional(Type.Integer({ minimum: 0 })),
    sheep: Type.Optional(Type.Integer({ minimum: 0 })),
    wheat: Type.Optional(Type.Integer({ minimum: 0 })),
    ore: Type.Optional(Type.Integer({ minimum: 0 })),
  },
  { additionalProperties: false },
);

export const BuildSchema = Type.Object({
  type: Type.String({ enum: ["road", "settlement", "upgrade"] }),
  edge_id: Type.Optional(Type.String()),
  vertex_id: Type.Optional(Type.String()),
  to_level: Type.Optional(Type.Integer({ minimum: 2, maximum: 3 })),
});

export const ProposeTradeSchema = Type.Object({
  offer: ResourceMap,
  request: ResourceMap,
  target: Type.Optional(
    Type.Union(
      [Type.Integer({ minimum: 0, maximum: 3 }), Type.Literal("system")],
      {
        description:
          "Player id (0-3) to trade with, or \"system\" for the bank/port exchange: " +
          "give a fixed ratio of ONE resource for 1 of another. The ratio is 4:1, " +
          "or 3:1 if you own a building on a port vertex. No counterparty has to agree.",
      },
    ),
  ),
});

export const SendMessageSchema = Type.Object({
  target: Type.Integer({
    minimum: 0,
    maximum: 3,
    description: "Player id (0-3) to send this private message to.",
  }),
  content: Type.String({
    minLength: 1,
    description:
      "Private message delivered to the target player, who is woken to reply within the same round.",
  }),
});

export const ClaimProjectSchema = Type.Object({
  project_id: Type.String({
    enum: [
      "trade_master",
      "build_master",
      "tech_pioneer",
      "resource_tycoon",
      "road_king",
    ],
  }),
});
