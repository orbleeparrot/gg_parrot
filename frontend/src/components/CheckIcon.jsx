// 체크 표시 — 공용 선 아이콘(components/icons.jsx, Lucide `check`)의 얇은 포장.
import { Icon } from "./icons.jsx";

export default function CheckIcon({ size = 18, strokeWidth = 2.25, className = "" }) {
  return <Icon name="check" size={size} strokeWidth={strokeWidth} className={className} />;
}
