// 빌더에 그려진 칸 이름이 서로 같으면 안 된다 — 진입 필터 칸이 규칙 자체의 칸과 이름이 겹쳐
// 화면에서 어느 쪽인지 구분할 수 없던 문제가 다시 생기지 않게 한다. 소스가 아니라 실제로 그려진 <label> 을 본다.
import assert from "node:assert/strict";
import { test } from "node:test";
import { renderComponent, textOf } from "./renderHelper.js";
import { FILTERABLE_RULE_TYPES, FILTER_KINDS, RULE_TYPES, defaultForm, withTypeDefaults } from "../src/lib/macro.js";

const formFor = (rt, extra = {}) => ({ ...withTypeDefaults({ ...defaultForm(), symbol: "BTCUSDT" }, rt), ...extra });
const render = (rt, extra = {}, variant = "default") => renderComponent(
  "src/components/Builder.jsx",
  { form: formFor(rt, extra), setForm: () => {}, variant },
);

// 보이는 칸 이름: 입력 칸의 <label>, 그리고 묶음 칸(span id=...-label). 안쪽 태그는 걷어 낸다.
export function visibleLabels(html) {
  const out = [];
  for (const m of html.matchAll(/<label\b[^>]*>([\s\S]*?)<\/label>|<span id="[^"]*-label">([\s\S]*?)<\/span>/g)) {
    const text = textOf(m[1] ?? m[2]);
    if (text) out.push(text);
  }
  return out;
}

function duplicates(labels) {
  const count = new Map();
  for (const l of labels) count.set(l, (count.get(l) || 0) + 1);
  return [...count].filter(([, n]) => n > 1);
}

// 서버 렌더는 작은따옴표를 &#x27; 로 쓴다 — 화면에 보이는 글 그대로 비교하려고 되돌린다.
const shown = (html) => textOf(html).replace(/&#x27;/g, "'");

const KINDS = FILTER_KINDS.map((k) => k.value);

test("규칙마다 보이는 칸 이름이 겹치지 않는다(필터는 쓸 수 있는 규칙에서만 켠다)", async () => {
  for (const variant of ["default", "dense"]) {
    for (const rt of Object.keys(RULE_TYPES)) {
      const filterable = FILTERABLE_RULE_TYPES.includes(rt);
      // 필터를 쓸 수 있는 규칙은 네 종류를 모두 켜 보고, 못 쓰는 규칙은 켜 달라는 값이 들어와도 그리지 않는다.
      const cases = filterable
        ? [{ use_entry_filter: false }, ...KINDS.map((filter_kind) => ({ use_entry_filter: true, filter_kind }))]
        : [{ use_entry_filter: false }];
      for (const extra of cases) {
        const labels = visibleLabels(await render(rt, extra, variant));
        const where = `규칙 ${rt} / ${variant} / ${extra.use_entry_filter ? `진입 조건 ${extra.filter_kind}` : "진입 조건 끔"}`;
        assert.ok(labels.length > 10, `${where}: 칸 이름을 읽지 못했다(${labels.length}개) — 시험이 아무것도 못 본다`);
        if (extra.use_entry_filter) assert.ok(labels.includes("진입 조건 종류"), `${where}: 필터가 안 켜졌다`);
        const dup = duplicates(labels);
        assert.deepEqual(dup, [], `${where}: 같은 칸 이름이 둘 이상 보인다 -> ${dup.map(([l, n]) => `"${l}" x${n}`).join(", ")}`);
      }
    }
  }
});

test("변동성 돌파(I)에서는 이 전략의 이동평균 필터와 둘 다 맞아야 진입한다고 알려 준다", async () => {
  for (const variant of ["default", "dense"]) {
    for (const filter_kind of KINDS) {
      const text = shown(await render("I", { use_entry_filter: true, filter_kind }, variant));
      assert.match(text, /이 전략에는 따로 '이동평균 필터 기간' 칸이 있어요\. 거기에 값을 넣었다면 둘 다 맞아야 진입해요/, `${variant}/${filter_kind}`);
    }
    const ma = shown(await render("I", { use_entry_filter: true, filter_kind: "ma" }, variant));
    assert.match(ma, /별개예요\. 둘 다 맞아야 진입해요/, variant);
  }
});

test("그 안내는 변동성 돌파가 아닌 규칙에는 붙지 않는다", async () => {
  for (const rt of FILTERABLE_RULE_TYPES.filter((r) => r !== "I")) {
    const text = textOf(await render(rt, { use_entry_filter: true, filter_kind: "ma" }));
    assert.doesNotMatch(text, /이동평균 필터 기간/, rt);
    assert.doesNotMatch(text, /둘 다 맞아야 진입/, rt);
  }
});

test("규칙 자체의 기존 칸 이름은 그대로다", async () => {
  const i = visibleLabels(await render("I", { use_entry_filter: true, filter_kind: "ma" }));
  assert.ok(i.includes("이동평균 필터 기간"));
  const j = visibleLabels(await render("J", { use_entry_filter: true, filter_kind: "ma" }));
  assert.ok(j.includes("이동평균 종류"));
  assert.ok(j.includes("조건 이동평균 종류"));
});
