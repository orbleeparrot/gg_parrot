import { useState } from "react";
import { useAuth } from "../lib/auth.js";
import "./UserAvatar.css";

function AvatarImage({ src, initial }) {
  const [failed, setFailed] = useState(false);
  if (!src || failed) return <span>{initial}</span>;
  return <img src={src} alt="" draggable="false" decoding="async" onError={() => setFailed(true)} />;
}

export default function UserAvatar({ src, name, size = 32, className = "", decorative = true }) {
  const initial = Array.from(String(name || "").trim())[0]?.toUpperCase() || "?";
  return (
    <span className={`user-avatar${className ? ` ${className}` : ""}`} style={{ "--avatar-size": `${size}px` }} aria-hidden={decorative ? true : undefined} role={decorative ? undefined : "img"} aria-label={decorative ? undefined : `${name || "회원"} 프로필 사진`}>
      <AvatarImage key={src || "empty"} src={src} initial={initial} />
    </span>
  );
}

// Only a matching account ID can replace an API author's photo. A shared name
// or a legacy anonymous author must never inherit the signed-in user's image.
export function AuthorAvatar({ userId, src, ...props }) {
  const { token, user } = useAuth();
  const own = token && userId != null && user?.id != null && String(userId) === String(user.id);
  const currentSrc = own && user.avatar_url !== undefined ? user.avatar_url : src;
  return <UserAvatar {...props} src={currentSrc} />;
}
