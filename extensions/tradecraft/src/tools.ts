/**
 * TradeCraft tool schemas — TypeBox parameter definitions for all game actions.
 */
import { Type } from "typebox";

/** Schema for a resource map like {"wood": 2, "stone": 1}. */
const ResourceMap = Type.Record(Type.String(), Type.Number());

export const ViewGameStateSchema = Type.Object({});

export const CollectResourcesSchema = Type.Object({
  resources: ResourceMap,
});

export const PurchaseBuildingSchema = Type.Object({
  building_id: Type.String(),
});

export const ProposeTradeSchema = Type.Object({
  offer: ResourceMap,
  request: ResourceMap,
});

export const ClaimProjectSchema = Type.Object({
  project_id: Type.String(),
});

// Trade negotiation tools (always available, no turn restriction)
export const AcceptTradeSchema = Type.Object({});
export const RejectTradeSchema = Type.Object({});
export const CounterOfferSchema = Type.Object({
  offer: ResourceMap,
  request: ResourceMap,
});
