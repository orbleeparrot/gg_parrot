// Optional start-guide persistence.
//
// Presentation state (started/dismissed) only changes which CTA the home page
// emphasizes. The durable outcome is still `completed_at`, written after a real
// registration. The live step is derived from real guide state, so storage can
// never pretend a backtest, paper session, or registration happened.
const KEY = "ggp_journey";
const REGISTRATION_DRAFT_KEY = "ggp_registration_draft";
const HERO_DRAFT_KEY = "ggp_hero_draft:v1";
const VERSION = 1;

function read() {
  try {
    const value = JSON.parse(localStorage.getItem(KEY) || "null");
    if (!value || value.version !== VERSION) return null;
    return value;
  } catch (_) {
    return null;
  }
}

function write(value) {
  try {
    localStorage.setItem(KEY, JSON.stringify({ version: VERSION, ...value }));
  } catch (_) {
    // Persistence only changes how prominently the optional guide is shown.
  }
}

export function readJourneyState() {
  const value = read();
  return {
    started_at: value?.started_at || null,
    dismissed_at: value?.dismissed_at || null,
    completed_at: value?.completed_at || null,
    last_tour: typeof value?.last_tour === "string" ? value.last_tour : "",
  };
}

export function isJourneyComplete() {
  return !!read()?.completed_at;
}

export function beginJourney() {
  const value = readJourneyState();
  if (value.started_at) return;
  write({ ...value, started_at: new Date().toISOString() });
}

export function dismissJourney() {
  const value = readJourneyState();
  write({
    ...value,
    started_at: value.started_at || new Date().toISOString(),
    dismissed_at: new Date().toISOString(),
  });
}

export function saveJourneyStep(step) {
  if (typeof step !== "string" || !step) return;
  const value = readJourneyState();
  write({
    ...value,
    started_at: value.started_at || new Date().toISOString(),
    last_tour: step,
  });
}

export function completeJourney() {
  const value = readJourneyState();
  write({
    ...value,
    started_at: value.started_at || new Date().toISOString(),
    completed_at: new Date().toISOString(),
  });
}

export function saveRegistrationDraft(macro, context = {}) {
  try {
    sessionStorage.setItem(
      REGISTRATION_DRAFT_KEY,
      JSON.stringify({
        version: VERSION,
        macro,
        context: {
          origin: context.origin === "hero" ? "hero" : "builder",
          mode: context.mode === "replay" ? "replay" : "live",
          return_to: typeof context.returnTo === "string" ? context.returnTo : "",
        },
        saved_at: new Date().toISOString(),
      })
    );
  } catch (_) {
    // The user can still return to the builder and run the test again.
  }
}

function readRegistrationDraft(consume) {
  try {
    const raw = sessionStorage.getItem(REGISTRATION_DRAFT_KEY);
    const value = JSON.parse(raw || "null");
    const macro = value?.macro;
    const recent = Date.now() - Date.parse(value?.saved_at || "") <= 30 * 60 * 1000;
    const structurallyValid =
      macro &&
      typeof macro === "object" &&
      typeof macro.symbol === "string" &&
      typeof macro.rule_type === "string" &&
      /^[A-K]$/.test(macro.rule_type) &&
      macro.params &&
      typeof macro.params === "object";
    const valid = value?.version === VERSION && recent && structurallyValid;
    if (consume || !valid) sessionStorage.removeItem(REGISTRATION_DRAFT_KEY);
    return valid ? value : null;
  } catch (_) {
    try {
      sessionStorage.removeItem(REGISTRATION_DRAFT_KEY);
    } catch (_) {}
    return null;
  }
}

export function peekRegistrationDraft() {
  return readRegistrationDraft(false);
}

export function takeRegistrationDraft() {
  return readRegistrationDraft(true);
}

// The full-screen guide keeps only the macro being assembled. This lets browser
// history and a refresh preserve the chosen A~K settings without storing UI or
// account state. Session storage disappears with the tab.
export function saveHeroDraft(macro) {
  try {
    sessionStorage.setItem(
      HERO_DRAFT_KEY,
      JSON.stringify({ version: VERSION, macro, saved_at: Date.now() })
    );
  } catch (_) {
    // The guide still works for the current render when storage is unavailable.
  }
}

export function readHeroDraft() {
  try {
    const value = JSON.parse(sessionStorage.getItem(HERO_DRAFT_KEY) || "null");
    const validAge = Date.now() - Number(value?.saved_at || 0) <= 2 * 60 * 60 * 1000;
    const macro = value?.macro;
    const validEnums =
      ["long", "short"].includes(macro?.position_side) &&
      ["1m", "5m", "15m", "1h", "4h", "1d"].includes(macro?.candle_interval) &&
      ["auto", "spot", "futures"].includes(macro?.market || "auto") &&
      ["1y", "6m", "3m", "1m", "1w", "1d", "custom"].includes(macro?.period?.preset || "1y");
    const validMacro =
      macro &&
      typeof macro === "object" &&
      typeof macro.symbol === "string" &&
      typeof macro.rule_type === "string" &&
      /^[A-K]$/.test(macro.rule_type) &&
      macro.params &&
      typeof macro.params === "object" &&
      validEnums;
    if (value?.version !== VERSION || !validAge || !validMacro) {
      sessionStorage.removeItem(HERO_DRAFT_KEY);
      return null;
    }
    return macro;
  } catch (_) {
    try {
      sessionStorage.removeItem(HERO_DRAFT_KEY);
    } catch (_) {}
    return null;
  }
}
