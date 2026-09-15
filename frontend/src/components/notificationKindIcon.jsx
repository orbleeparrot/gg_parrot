// 알림 종류 → 아이콘. 종 패널의 행과 토스트가 같은 그림을 쓴다.
import {
  ArrowBendUpLeftIcon, BellIcon, ChatCircleTextIcon, CoinsIcon, EnvelopeSimpleIcon,
  MegaphoneIcon, RankingIcon, RobotIcon, TrophyIcon,
} from "./utilityIcons.jsx";

const KIND_ICONS = {
  quest: TrophyIcon,
  macro_sold: CoinsIcon,
  macro_registered: RankingIcon,
  comment: ChatCircleTextIcon,
  reply: ArrowBendUpLeftIcon,
  agent: RobotIcon,
  admin: EnvelopeSimpleIcon,
  notice: MegaphoneIcon,
};

export default function NotificationKindIcon({ kind }) {
  const Icon = KIND_ICONS[kind] || BellIcon;
  return <Icon />;
}
