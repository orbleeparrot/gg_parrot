// 판매 등급 아이콘(새싹 · 브론즈 · 실버 · 골드 · 다이아) — 내 활동 등급 사다리와 같은 Phosphor duotone 그림.
// 닉네임 옆에 붙일 때는 이름을 title 로 남겨 마우스를 올리면 등급 이름이 보인다.
import { PlantIcon } from "@phosphor-icons/react/dist/csr/Plant";
import { MedalIcon } from "@phosphor-icons/react/dist/csr/Medal";
import { DiamondIcon } from "@phosphor-icons/react/dist/csr/Diamond";
import { CrownSimpleIcon } from "@phosphor-icons/react/dist/csr/CrownSimple";
import "./TierIcon.css";

// 관리자는 다이아 위 — 판매 수와 상관없는 관리자 계정 전용 등급.
const TONES = { 새싹: "seed", 브론즈: "bronze", 실버: "silver", 골드: "gold", 다이아: "diamond", 관리자: "admin" };

export default function TierIcon({ name, size = 24, labelled = false }) {
  const tone = TONES[name];
  if (!tone) return null;
  const Icon = name === "새싹" ? PlantIcon : name === "다이아" ? DiamondIcon : name === "관리자" ? CrownSimpleIcon : MedalIcon;
  const icon = <Icon className={"tier-icon is-" + tone} size={size} weight="duotone" aria-hidden="true" />;
  // 닉네임 옆: 화면 읽기 프로그램에는 '등급 골드' 처럼 읽히고, 마우스를 올리면 이름이 보인다.
  return labelled ? <span className="tier-mark" title={`${name} 등급`} role="img" aria-label={`${name} 등급`}>{icon}</span> : icon;
}
