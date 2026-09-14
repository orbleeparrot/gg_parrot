// All visible key panels share one memory-only value. A successful rotation must
// replace the old key everywhere, including reads that started before rotation.
export function createRunnerKeyStore({ read, regenerate }) {
  const empty = Object.freeze({ data: null, error: "", regenerating: false });
  let owner = null;
  let snapshot = empty;
  let generation = 0;
  let pendingRead = null;
  let pendingRotation = null;
  const listeners = new Set();
  const emit = () => listeners.forEach((listener) => listener());
  const reset = (nextOwner = null) => {
    owner = nextOwner;
    snapshot = empty;
    generation += 1;
    pendingRead = null;
    pendingRotation = null;
  };
  const enter = (nextOwner) => { if (owner !== nextOwner) reset(nextOwner); };
  return {
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
        // Keys never survive the last panel closing, or enter persistent storage.
        if (!listeners.size) reset();
      };
    },
    getSnapshot(nextOwner) { return owner === nextOwner ? snapshot : empty; },
    load(nextOwner, isCurrent) {
      if (!isCurrent()) return Promise.resolve();
      enter(nextOwner);
      if (pendingRotation) return pendingRotation;
      if (pendingRead) return pendingRead;
      const version = generation;
      const current = () => isCurrent() && owner === nextOwner && version === generation;
      const request = Promise.resolve().then(() => current() ? read(version) : undefined).then((data) => {
        if (current()) { snapshot = { data, error: "", regenerating: false }; emit(); }
      }).catch((error) => {
        if (current()) { snapshot = { ...snapshot, error: String(error.message || error) }; emit(); }
      }).finally(() => { if (pendingRead === request) pendingRead = null; });
      pendingRead = request;
      return request;
    },
    rotate(nextOwner, isCurrent) {
      if (!isCurrent()) return Promise.resolve();
      enter(nextOwner);
      if (pendingRotation) return pendingRotation;
      const version = ++generation;
      pendingRead = null;
      snapshot = { ...snapshot, error: "", regenerating: true };
      emit();
      const current = () => isCurrent() && owner === nextOwner && version === generation;
      const request = Promise.resolve().then(() => current() ? regenerate() : undefined).then((data) => {
        if (!current()) return undefined;
        snapshot = { data, error: "", regenerating: false };
        emit();
        return data;
      }).catch((error) => {
        if (current()) { snapshot = { ...snapshot, error: String(error.message || error), regenerating: false }; emit(); }
        return undefined;
      }).finally(() => { if (pendingRotation === request) pendingRotation = null; });
      pendingRotation = request;
      return request;
    },
  };
}
