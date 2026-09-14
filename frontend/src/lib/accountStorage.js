// Drafts belong to an account (or to the current guest), never to a browser-wide
// token. Credentials and server-owned state are not stored in this namespace.
const PREFIX = "ggp_account:v1:";
const KINDS = ["studio", "paper", "registration", "hero"];
const LEGACY = ["ggp_studio:v1", "ggp_studio_paper:v1", "ggp_registration_draft", "ggp_hero_draft:v1"];

export function accountScope({ token, user } = {}) {
  return token ? (user?.id != null ? `member:${user.id}` : "pending") : "guest";
}

export function accountStorageKey(kind, scope) {
  return `${PREFIX}${scope}:${kind}`;
}

export function discardUnownedDrafts() {
  // Old unscoped data has no reliable owner; do not assign it to the next login.
  try { for (const key of LEGACY) sessionStorage.removeItem(key); } catch { /* optional storage */ }
}

export function transitionAccountStorage(previous, next, { preserveRegistrationDraft = false } = {}) {
  if (previous === next) return;
  try {
    discardUnownedDrafts();
    const retained = previous.startsWith("member:") && preserveRegistrationDraft
      ? accountStorageKey("registration", previous) : null;
    // Remove outgoing/leftover member work even when this tab missed an earlier
    // logout. A short reauthentication handoff may retain only its owned draft.
    const keys = new Set(KINDS.map((kind) => accountStorageKey(kind, previous)));
    for (let index = 0; index < sessionStorage.length; index += 1) {
      const key = sessionStorage.key(index);
      if (key?.startsWith(`${PREFIX}member:`) && !key.startsWith(`${PREFIX}${next}:`)) keys.add(key);
    }
    if (previous !== "guest") {
      for (const key of keys) if (key !== retained) sessionStorage.removeItem(key);
    } else {
      for (const key of keys) {
        if (key.startsWith(`${PREFIX}member:`) && key !== retained) sessionStorage.removeItem(key);
      }
    }
    // Signing in continues work that was explicitly created as a guest. Moving
    // it prevents that now-owned work from reappearing after logout.
    if (previous === "guest" && next.startsWith("member:")) {
      for (const kind of KINDS) {
        const from = accountStorageKey(kind, "guest");
        const to = accountStorageKey(kind, next);
        const value = sessionStorage.getItem(from);
        if (value != null && sessionStorage.getItem(to) == null) sessionStorage.setItem(to, value);
        sessionStorage.removeItem(from);
      }
    }
  } catch { /* Storage may be disabled; account isolation still holds in memory. */ }
}
