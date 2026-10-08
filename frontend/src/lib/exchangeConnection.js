const STEPS = new Set(["prepare", "permissions", "ip", "keys"]);

export function parseConnectionGuide(params) {
  return {
    exchange: params.get("exchange") === "bithumb" ? "bithumb" : "upbit",
    step: STEPS.has(params.get("step")) ? params.get("step") : "prepare",
  };
}

// Construct from a small allowlist. Never reuse location.search, launch URLs,
// authentication tokens, macro IDs, member keys, or exchange credentials.
export function connectionGuidePath({ exchange, step } = {}) {
  const safe = parseConnectionGuide(new URLSearchParams({ exchange: exchange || "", step: step || "" }));
  return `/exchange-connect?${new URLSearchParams(safe)}`;
}

export function publicConnectionGuideUrl(options) {
  return `https://gg-parrot.vercel.app${connectionGuidePath(options)}`;
}
