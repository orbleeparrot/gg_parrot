import { baseOf, quoteOf } from "./format.js";
import { RULE_TYPES } from "./macro.js";

const number = (value) => Number(value).toLocaleString("en-US", { maximumFractionDigits: 8 });
const positive = (value) => value != null && Number.isFinite(Number(value)) && Number(value) > 0;

// Values come from the macro; only remove the server summary's duplicated
// envelope. Its middle dots may also separate stop-loss conditions.
export function leaderboardStrategy(entry) {
  if (entry.locked) return null;
  const macro = entry.macro;
  let description = entry.human_summary?.trim() || "";
  if (!macro) return { description: description || "상세 정보 없음", side: null, capital: null };

  const side = macro.rule_type === "K" && macro.params?.flip_to_short !== false
    ? "switch" : macro.position_side;
  const pieces = description.split(" · ");
  if ([baseOf(entry.symbol), entry.symbol].includes(pieces[0])) {
    pieces.shift();
    if (["롱", "숏"].includes(pieces[0])) pieces.shift();
    description = pieces.filter((part) => !/^자금 [\d,.]+% 투입$/.test(part)).join(" · ");
  }
  const capital = macro.rule_type === "C"
    ? (positive(macro.params?.amount_per_buy)
      ? { value: number(macro.params.amount_per_buy), unit: quoteOf(entry.symbol), label: "회당 자금" } : null)
    : (positive(macro.risk?.invest_ratio)
      ? { value: `${number(Number(macro.risk.invest_ratio) * 100)}%`, unit: "투입", label: "자금" } : null);
  return {
    description: description || RULE_TYPES[macro.rule_type]?.label.replace(/^[A-Z] · /, "") || "상세 정보 없음",
    side,
    capital,
  };
}
