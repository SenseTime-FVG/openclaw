/** TradeCraft tool schemas for the Catan-style engine protocol. */
import { Type } from "typebox";

const ResourceMap = Type.Object(
  {
    wood: Type.Optional(Type.Integer({ minimum: 1 })),
    stone: Type.Optional(Type.Integer({ minimum: 1 })),
    iron: Type.Optional(Type.Integer({ minimum: 1 })),
    crystal: Type.Optional(Type.Integer({ minimum: 1 })),
    silk: Type.Optional(Type.Integer({ minimum: 1 })),
    gold: Type.Optional(Type.Integer({ minimum: 1 })),
  },
  { additionalProperties: false },
);

export const ViewGameStateSchema = Type.Object({});

export const BuildSchema = Type.Object({
  type: Type.String({ enum: ["road", "settlement", "upgrade"] }),
  edge_id: Type.Optional(Type.String()),
  vertex_id: Type.Optional(Type.String()),
  to_level: Type.Optional(Type.Integer({ minimum: 2, maximum: 3 })),
});

export const ProposeTradeSchema = Type.Object({
  offer: ResourceMap,
  request: ResourceMap,
  target: Type.Optional(Type.Integer({ minimum: 0, maximum: 3 })),
});

export const RespondTradeSchema = Type.Object({
  action: Type.String({ enum: ["accept", "reject", "counter"] }),
  message: Type.String({
    minLength: 1,
    description:
      "Concise decision or negotiation feedback delivered to the other party without consuming a turn action.",
  }),
  offer: Type.Optional(ResourceMap),
  request: Type.Optional(ResourceMap),
});

export const SendMessageSchema = Type.Object({
  content: Type.String({
    minLength: 1,
    description: "Message broadcast to every other player in the match.",
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
      "hex_lord",
    ],
  }),
});
