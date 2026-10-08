import { useMemo } from "react";
import { qrModules } from "../lib/connectionGuideQr.js";

// QR modules are encoded data, not themed UI: fixed black/white contrast and a
// white quiet zone are required in both themes for camera decoding.
/* oxlint-disable shadcn/no-raw-colors */

export default function ConnectionGuideQr({ address }) {
  const modules = useMemo(() => qrModules(address), [address]);
  const path = modules.flatMap((row, y) => row.flatMap((dark, x) => dark ? [`M${x},${y}h1v1h-1z`] : [])).join("");
  return (
    <svg className="exchange-connect-qr" viewBox={`0 0 ${modules.length} ${modules.length}`} role="img" aria-label="현재 단계의 공개 연결 안내 QR" shapeRendering="crispEdges">
      <rect width="100%" height="100%" fill="white" />
      <path d={path} fill="black" />
    </svg>
  );
}
