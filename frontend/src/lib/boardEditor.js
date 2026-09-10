// 글쓰기 본문 편집기의 직렬화 — contenteditable 안의 글자·줄바꿈·사진(<img data-key>)을
// 저장 형식(글자 + `[사진n]` 자리 표시)으로 바꾼다. 화면에는 사진이 그대로 보이고, 자리 표시는 저장에만 쓴다.
// DOM 에 기대지 않고 노드 모양(nodeType·nodeName·childNodes·data·getAttribute)만 읽어서 node 테스트가 된다.

import { imageMark } from "./boardText.js";

const BLOCKS = new Set(["DIV", "P", "LI", "BLOCKQUOTE", "PRE", "H1", "H2", "H3", "H4", "SECTION", "ARTICLE"]);

/**
 * @param root 편집기 루트 노드
 * @param indexOfKey (key) => 1부터 시작하는 사진 번호. 모르는 키면 null → 그 사진은 뺀다.
 */
export function serializeEditor(root, indexOfKey) {
  const out = [];
  const endsWithBreak = () => out.length === 0 || /\n$/.test(out[out.length - 1]);
  const walk = (node) => {
    for (const child of Array.from(node.childNodes || [])) {
      if (child.nodeType === 3) {
        const text = String(child.data ?? child.textContent ?? "").replace(/ /g, " ");
        if (text) out.push(text);
        continue;
      }
      if (child.nodeType !== 1) continue;
      const name = String(child.nodeName || "").toUpperCase();
      if (name === "IMG") {
        const index = indexOfKey(child.getAttribute("data-key"));
        if (index == null) continue;
        if (!endsWithBreak()) out.push("\n");
        out.push(imageMark(index), "\n");
        continue;
      }
      if (name === "BR") { out.push("\n"); continue; }
      if (BLOCKS.has(name)) {
        if (!endsWithBreak()) out.push("\n");
        walk(child);
        if (!endsWithBreak()) out.push("\n");
        continue;
      }
      walk(child);
    }
  };
  walk(root);
  return out.join("").replace(/\n{3,}/g, "\n\n").replace(/^\n+|\n+$/g, "");
}

/** 편집기 안 사진 키를 나오는 순서대로. */
export function editorImageKeys(root) {
  const keys = [];
  const walk = (node) => {
    for (const child of Array.from(node.childNodes || [])) {
      if (child.nodeType !== 1) continue;
      if (String(child.nodeName || "").toUpperCase() === "IMG") {
        const key = child.getAttribute("data-key");
        if (key) keys.push(key);
      } else {
        walk(child);
      }
    }
  };
  walk(root);
  return keys;
}

/**
 * 저장 순서에 맞춘 번호표 — 서버는 남긴 기존 사진(원래 순서)을 앞에, 새 사진(올린 순서)을 뒤에 둔다.
 * keys: 편집기 순서의 키 목록. existingOrder: 처음 불러온 기존 사진 키 순서.
 * 반환 { indexOf: key → 1부터, keepIds: 남긴 기존 id 순서, newKeys: 새 사진 키(편집기 순서) }
 */
export function planImageOrder(keys, existingOrder = []) {
  const present = new Set(keys);
  const kept = existingOrder.filter((key) => present.has(key));
  const fresh = keys.filter((key) => key.startsWith("new:"));
  const map = new Map();
  kept.forEach((key, i) => map.set(key, i + 1));
  fresh.forEach((key, i) => map.set(key, kept.length + i + 1));
  return {
    indexOf: (key) => map.get(key) ?? null,
    keepIds: kept.map((key) => Number(key.slice("existing:".length))),
    newKeys: fresh,
  };
}
