// Bundled separately with React's development build. This deliberately does
// not mount the product router or contact a backend: the browser script mocks
// every API request and supplies an isolated localStorage auth fixture.
import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { useAuth } from "../src/lib/auth.js";
import { useNewsReceipt } from "../src/features/agents/useNewsReceipt.js";

const SESSION = { session_id: 41, status: "running" };

function ReceiptProbe() {
  const auth = useAuth();
  const [receipt, setReceipt] = useState({ receipt_key: "strict-initial", receipt_token: "fixture-initial" });
  const [revision, setRevision] = useState(0);
  const { state } = useNewsReceipt(SESSION, receipt);

  useEffect(() => {
    window.__strictReceiptEffects.setups += 1;
    return () => { window.__strictReceiptEffects.cleanups += 1; };
  }, []);

  return <section aria-label="StrictMode receipt hook probe">
    <p data-auth-user>{String(auth.user?.id ?? "anonymous")}</p>
    <p data-receipt-state>{receipt.receipt_key}: {state?.status || "idle"}</p>
    <p data-render-revision>{revision}</p>
    <button type="button" onClick={() => {
      setRevision((value) => value + 1);
      setReceipt((value) => ({ ...value, receipt_token: `${value.receipt_token}-renewed` }));
    }}>Rerender same key</button>
    <button type="button" onClick={() => setReceipt({ receipt_key: "strict-changed", receipt_token: "fixture-changed" })}>
      Change receipt key
    </button>
    <button type="button" onClick={() => setReceipt({ receipt_key: "strict-held", receipt_token: "fixture-held" })}>
      Begin held receipt
    </button>
  </section>;
}

function ProbeHarness() {
  const [mounted, setMounted] = useState(true);
  return <main>
    <h1>Development StrictMode receipt lifecycle</h1>
    <button type="button" disabled={!mounted} onClick={() => setMounted(false)}>Unmount receipt hook</button>
    {mounted ? <ReceiptProbe /> : <p data-unmounted>Receipt hook unmounted</p>}
  </main>;
}

window.__strictReceiptEffects = { setups: 0, cleanups: 0 };
createRoot(document.getElementById("root")).render(<StrictMode><ProbeHarness /></StrictMode>);
