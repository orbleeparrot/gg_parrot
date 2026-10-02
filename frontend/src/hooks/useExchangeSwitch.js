import { useEffect, useRef, useState } from "react";
import { loadSymbolList } from "./useSymbolList.js";
import { normalizeExchange } from "../lib/exchanges.js";
import { withExchangeDefaults } from "../lib/macro.js";
import { symbolListView } from "../lib/symbolListResource.js";

// Reset incompatible money/price conditions immediately, then retain only
// assets verified by the target catalogue. Retired requests cannot restore them.
export function useExchangeSwitch(form, setForm) {
  const [exchangeNotice, setExchangeNotice] = useState("");
  const generation = useRef(0);
  const pendingAsset = useRef(null);
  useEffect(() => () => { generation.current += 1; }, []);
  const switchExchange = async (value) => {
    const exchange = normalizeExchange(value);
    if (exchange === normalizeExchange(form.exchange)) return;
    const ticket = ++generation.current;
    // A second exchange change can precede the first catalogue response.
    // The temporary blank must not erase the user's original asset intent.
    const origin = !form.symbol && pendingAsset.current?.exchange === normalizeExchange(form.exchange)
      ? { ...form, symbol: pendingAsset.current.symbol } : form;
    pendingAsset.current = { exchange, symbol: origin.symbol };
    setForm((current) => withExchangeDefaults(current, exchange));
    setExchangeNotice("새 거래소의 종목을 확인하고 있어요. 가격·금액 조건은 다시 입력해 주세요. 자동 환산하지 않아요.");
    try {
      // The picker remounts for the new exchange, which can briefly drop the
      // catalogue's last subscriber and abort the request (it then yields null).
      // Ask again — the next call joins the restarted request.
      let data = null;
      for (let attempt = 0; attempt < 3 && !data; attempt += 1) {
        data = await loadSymbolList(false, exchange);
        if (ticket !== generation.current) return;
      }
      const { items } = symbolListView({ data, loading: false, error: "" }, exchange);
      if (!items) throw new Error("expired catalogue");
      const retained = withExchangeDefaults(origin, exchange, items).symbol;
      pendingAsset.current = null;
      setForm((current) => normalizeExchange(current.exchange) === exchange && !current.symbol
        ? { ...current, symbol: retained } : current);
      setExchangeNotice(retained
        ? "새 거래소에서 거래 가능한 종목을 유지했어요. 가격·금액 조건은 다시 입력해 주세요. 자동 환산하지 않아요."
        : "새 거래소에 같은 종목이 없어 비웠어요. 종목과 가격·금액 조건을 다시 입력해 주세요.");
    } catch (_) {
      // Keep pendingAsset: switching back (or again) must still restore the asset.
      if (ticket === generation.current) {
        setExchangeNotice("종목 목록을 확인하지 못해 종목을 비웠어요. 목록을 다시 확인한 뒤 선택해 주세요.");
      }
    }
  };
  return { switchExchange, exchangeNotice };
}
