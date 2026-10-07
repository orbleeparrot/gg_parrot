// @shadcn/lint 의 no-unknown-classes 는 Tailwind 가 만드는 클래스와 index.css(테마)가 불러오는
// CSS 의 클래스만 안다. 화면별 CSS(Builder.css 등)는 컴포넌트가 직접 import 하므로 린터가 못 본다.
// 그 CSS 들에 실제로 정의된 클래스 이름을 정확히 허용 목록에 넣어, 접두사로 통째 허용할 때와 달리
// 'lb-fact-tickr' 같은 오타는 계속 잡히게 한다.
//
//   node scripts/lint-css-classes.mjs --check   허용 목록이 CSS 와 같은지 확인(npm run lint 가 먼저 돈다)
//   node scripts/lint-css-classes.mjs --write   CSS 를 고친 뒤 허용 목록을 다시 만든다(npm run lint:classes)
import { readFileSync, writeFileSync, readdirSync, statSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const CONFIG = join(ROOT, ".oxlintrc.json");
const RULE = "shadcn/no-unknown-classes";

// CSS 가 없는 이름표 클래스 — 화면 구조를 읽기 쉽게 하거나 테스트·스크립트가 요소를 찾는 데 쓴다.
// 스타일이 없어도 동작에는 문제가 없다. 새로 넣을 때는 정말 CSS 가 필요 없는지 먼저 확인한다.
export const HOOK_CLASSES = [
  "adm-hover", "bd-sum", "candle-ohlc-time", "header-bell-kind", "header-resource-label",
  "is-devnote", "is-minimal", "is-runner", "is-waiting", "news-carousel-time", "news-map-news-time",
  "profile-settings-member-key", "report-dialog", "rooms-new", "runner-session-board",
  "sd-ai-body", "sd-card-spark-area", "sd-card-spark-base", "sd-card-spark-line",
  "sd-opt-grid", "sd-outcomes", "sd-side",
];

// Tailwind 가 만들지 않지만 디자인 값이 아닌 것 — 아이콘 기준선 미세 조정, 가상 요소 내용, 아이폰 하단 안전 영역.
export const ARBITRARY_EXCEPTIONS = ["align-[-0.15em]", "content-['']", "pb-[env(safe-area-inset-bottom)]"];

function cssFiles(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return cssFiles(path);
    return name.endsWith(".css") ? [path] : [];
  });
}

export function definedClasses(srcDir = join(ROOT, "src")) {
  const names = new Set();
  for (const file of cssFiles(srcDir)) {
    const css = readFileSync(file, "utf8").replace(/\/\*[\s\S]*?\*\//g, "").replace(/url\([^)]*\)/g, "");
    for (const match of css.matchAll(/\.(-?[_a-zA-Z][\w-]*)/g)) names.add(match[1]);
  }
  return names;
}

export function expectedAllow() {
  return [...new Set([...definedClasses(), ...HOOK_CLASSES])].sort();
}

function readConfig() {
  return JSON.parse(readFileSync(CONFIG, "utf8"));
}

export function currentAllow(config = readConfig()) {
  const rule = config.rules?.[RULE];
  return Array.isArray(rule) ? [...(rule[1]?.allow || [])].sort() : [];
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const config = readConfig();
  const want = expectedAllow();
  if (process.argv.includes("--write")) {
    const severity = Array.isArray(config.rules[RULE]) ? config.rules[RULE][0] : config.rules[RULE];
    config.rules[RULE] = [severity, { allow: want }];
    writeFileSync(CONFIG, `${JSON.stringify(config, null, 2)}\n`);
    console.log(`${RULE}: 허용 목록 ${want.length}개로 갱신`);
  } else {
    const have = currentAllow(config);
    const missing = want.filter((name) => !have.includes(name));
    const extra = have.filter((name) => !want.includes(name));
    if (missing.length || extra.length) {
      console.error(`${RULE} 허용 목록이 CSS 와 다릅니다 — npm run lint:classes 로 다시 만드세요.`);
      if (missing.length) console.error(`  CSS 에 새로 생긴 클래스: ${missing.slice(0, 20).join(", ")}`);
      if (extra.length) console.error(`  CSS 에서 사라진 클래스: ${extra.slice(0, 20).join(", ")}`);
      process.exit(1);
    }
  }
}
