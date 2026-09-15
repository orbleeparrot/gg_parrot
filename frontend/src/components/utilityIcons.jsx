import { MoonIcon as PhosphorMoon } from "@phosphor-icons/react/dist/csr/Moon";
import { SunIcon as PhosphorSun } from "@phosphor-icons/react/dist/csr/Sun";
import { UserIcon as PhosphorUser } from "@phosphor-icons/react/dist/csr/User";
import { DownloadSimpleIcon } from "@phosphor-icons/react/dist/csr/DownloadSimple";
import { QuestionIcon } from "@phosphor-icons/react/dist/csr/Question";
import { CaretDownIcon } from "@phosphor-icons/react/dist/csr/CaretDown";
import { ListIcon } from "@phosphor-icons/react/dist/csr/List";
import { KeyIcon as PhosphorKey } from "@phosphor-icons/react/dist/csr/Key";
import { BellIcon as PhosphorBell } from "@phosphor-icons/react/dist/csr/Bell";
import { TrophyIcon as PhosphorTrophy } from "@phosphor-icons/react/dist/csr/Trophy";
import { CoinsIcon as PhosphorCoins } from "@phosphor-icons/react/dist/csr/Coins";
import { RankingIcon as PhosphorRanking } from "@phosphor-icons/react/dist/csr/Ranking";
import { ChatCircleTextIcon as PhosphorChatCircleText } from "@phosphor-icons/react/dist/csr/ChatCircleText";
import { ArrowBendUpLeftIcon as PhosphorArrowBendUpLeft } from "@phosphor-icons/react/dist/csr/ArrowBendUpLeft";
import { RobotIcon as PhosphorRobot } from "@phosphor-icons/react/dist/csr/Robot";
import { EnvelopeSimpleIcon as PhosphorEnvelopeSimple } from "@phosphor-icons/react/dist/csr/EnvelopeSimple";
import { MegaphoneIcon as PhosphorMegaphone } from "@phosphor-icons/react/dist/csr/Megaphone";

// Keep the library's native paths and fill; CSS only sizes and aligns icons.
const iconProps = { size: 24, weight: "regular", "aria-hidden": true, focusable: false };
// 해·달·사람은 채움(fill) — 작은 크기의 선 아이콘은 글자 옆에서 흐릿하게 뜬다. 행동 아이콘(내려받기·물음표)은 선 그대로.
const glyphProps = { ...iconProps, weight: "fill" };
export function MoonIcon() { return <PhosphorMoon {...glyphProps} />; }
export function SunIcon() { return <PhosphorSun {...glyphProps} />; }
export function UserIcon() { return <PhosphorUser {...glyphProps} />; }
export function DownloadIcon() { return <DownloadSimpleIcon {...iconProps} />; }
export function HelpIcon() { return <QuestionIcon {...iconProps} />; }
export function ChevronDownIcon() { return <CaretDownIcon {...iconProps} />; }
export function MenuIcon() { return <ListIcon {...iconProps} />; }
export function KeyIcon() { return <PhosphorKey {...iconProps} />; }
// 알림 종과 알림 종류 표시(NotificationBell) — 모두 regular 원본, 크기는 CSS 가 정한다.
export function BellIcon() { return <PhosphorBell {...iconProps} />; }
export function TrophyIcon() { return <PhosphorTrophy {...iconProps} />; }
export function CoinsIcon() { return <PhosphorCoins {...iconProps} />; }
export function RankingIcon() { return <PhosphorRanking {...iconProps} />; }
export function ChatCircleTextIcon() { return <PhosphorChatCircleText {...iconProps} />; }
export function ArrowBendUpLeftIcon() { return <PhosphorArrowBendUpLeft {...iconProps} />; }
export function RobotIcon() { return <PhosphorRobot {...iconProps} />; }
export function EnvelopeSimpleIcon() { return <PhosphorEnvelopeSimple {...iconProps} />; }
export function MegaphoneIcon() { return <PhosphorMegaphone {...iconProps} />; }
