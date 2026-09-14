import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

export function offlineCache() {
  return {
    name: "ggparrot-offline-cache",
    apply: "build",
    generateBundle(_options, bundle) {
      const assets = Object.keys(bundle).filter((file) =>
        /^assets\/[^/]+-[\w-]{8,}\.(js|css|woff2?|ttf|svg|png|webp|jpe?g|gif)$/.test(file)).sort();
      const offline = readFileSync(new URL("../public/offline.html", import.meta.url), "utf8");
      const template = readFileSync(new URL("./serviceWorker.js", import.meta.url), "utf8");
      const version = createHash("sha256").update(JSON.stringify(assets) + offline + template).digest("hex").slice(0, 16);
      this.emitFile({ type: "asset", fileName: "sw.js", source: template
        .replace("__CACHE_VERSION__", version)
        .replace("__BUILD_ASSETS__", JSON.stringify(assets.map((file) => `/${file}`))) });
    },
  };
}
