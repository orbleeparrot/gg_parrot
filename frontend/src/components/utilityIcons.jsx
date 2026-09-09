import { MoonIcon as PhosphorMoon } from "@phosphor-icons/react/dist/csr/Moon";
import { SunIcon as PhosphorSun } from "@phosphor-icons/react/dist/csr/Sun";
import { UserIcon as PhosphorUser } from "@phosphor-icons/react/dist/csr/User";
import { DownloadSimpleIcon } from "@phosphor-icons/react/dist/csr/DownloadSimple";
import { QuestionIcon } from "@phosphor-icons/react/dist/csr/Question";
import { CaretDownIcon } from "@phosphor-icons/react/dist/csr/CaretDown";
import { ListIcon } from "@phosphor-icons/react/dist/csr/List";

// Keep the library's native paths and fill; CSS only sizes and aligns icons.
const iconProps = { size: 24, weight: "regular", "aria-hidden": true, focusable: false };
export function MoonIcon() { return <PhosphorMoon {...iconProps} />; }
export function SunIcon() { return <PhosphorSun {...iconProps} />; }
export function UserIcon() { return <PhosphorUser {...iconProps} />; }
export function DownloadIcon() { return <DownloadSimpleIcon {...iconProps} />; }
export function HelpIcon() { return <QuestionIcon {...iconProps} />; }
export function ChevronDownIcon() { return <CaretDownIcon {...iconProps} />; }
export function MenuIcon() { return <ListIcon {...iconProps} />; }
