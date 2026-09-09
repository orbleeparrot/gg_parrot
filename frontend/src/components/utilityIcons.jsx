import { MoonIcon as PhosphorMoon } from "@phosphor-icons/react/dist/csr/Moon";
import { SunIcon as PhosphorSun } from "@phosphor-icons/react/dist/csr/Sun";
import { UserIcon as PhosphorUser } from "@phosphor-icons/react/dist/csr/User";
import { DownloadSimpleIcon } from "@phosphor-icons/react/dist/csr/DownloadSimple";
import { QuestionIcon } from "@phosphor-icons/react/dist/csr/Question";
import { CaretDownIcon } from "@phosphor-icons/react/dist/csr/CaretDown";
import { ListIcon } from "@phosphor-icons/react/dist/csr/List";

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
