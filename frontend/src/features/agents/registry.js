import { executionModule } from "./modules/execution.js";
import { leaderModule } from "./modules/leader.js";
import { positionNewsModule } from "./positionNews/index.js";
import { riskModule } from "./modules/risk.js";
import { strategyModule } from "./modules/strategy.js";

// New agent capabilities are added as one isolated module and one registry
// entry. The shared activity stream remains only a renderer.
export const AGENT_MODULES = [
  executionModule,
  positionNewsModule,
  riskModule,
  strategyModule,
  leaderModule,
];

export const AGENT_MODULE_MAP = new Map(AGENT_MODULES.map((module) => [module.key, module]));

export function accessFor(module, entitlements) {
  if (!module || module.availability === "planned") return "planned";
  // Until the entitlement API is connected, implemented modules stay usable.
  if (!entitlements) return "enabled";
  return entitlements?.features?.[module.entitlement]?.allowed ? "enabled" : "locked";
}

export function buildAgentEvents(context) {
  return AGENT_MODULES.flatMap((module) => module.buildEvents?.(context) || []);
}
