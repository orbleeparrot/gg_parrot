import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";
import { IMAGE_MARK_RE } from "../lib/boardText.js";
import { editorImageKeys, planImageOrder, serializeEditor } from "../lib/boardEditor.js";

// 글 본문 편집기 — contenteditable. 사진이 글자 사이에 그대로 보이고, 커서 자리에 들어간다.
// 저장할 때만 글자 + `[사진n]` 자리 표시로 바꾼다(lib/boardEditor). 굵게·색 같은 서식은 받지 않는다(붙여넣기도 글자만).
const MAX_IMAGE_BYTES = 2 * 1024 * 1024;

function isImageFile(file) {
  return file && ["image/jpeg", "image/png"].includes(file.type);
}

const BoardBodyEditor = forwardRef(function BoardBodyEditor({ initialBody = "", initialImages = [], maxImages = 10, onError, onCountChange }, ref) {
  const root = useRef(null);
  const files = useRef(new Map()); // "new:<url>" → File
  const existingOrder = useRef(initialImages.map((img) => `existing:${img.id}`));
  const [selectedKey, setSelectedKey] = useState(null);
  const [selectedBox, setSelectedBox] = useState(null);

  const countImages = useCallback(() => root.current ? editorImageKeys(root.current).length : 0, []);
  const notifyCount = useCallback(() => onCountChange?.(countImages()), [countImages, onCountChange]);

  // 처음 한 번: 저장 형식(글자 + [사진n]) → DOM. 줄은 <div>, 사진은 <img data-key>.
  useEffect(() => {
    const el = root.current;
    if (!el) return;
    el.innerHTML = "";
    const lines = [];
    let last = 0;
    const text = String(initialBody || "");
    for (const match of text.matchAll(IMAGE_MARK_RE)) {
      const image = initialImages[Number(match[1]) - 1];
      if (!image) continue;
      lines.push({ type: "text", text: text.slice(last, match.index) });
      lines.push({ type: "image", key: `existing:${image.id}`, url: image.url });
      last = match.index + match[0].length;
    }
    lines.push({ type: "text", text: text.slice(last) });
    for (const part of lines) {
      if (part.type === "image") {
        el.appendChild(makeImage(part.key, part.url));
        continue;
      }
      const rows = part.text.replace(/^\n|\n$/g, "").split("\n");
      if (rows.length === 1 && rows[0] === "") continue;
      for (const row of rows) {
        const div = document.createElement("div");
        if (row) div.textContent = row; else div.appendChild(document.createElement("br"));
        el.appendChild(div);
      }
    }
    if (!el.childNodes.length) el.appendChild(document.createElement("br"));
    notifyCount();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => () => { for (const url of files.current.keys()) URL.revokeObjectURL(url.slice(4)); }, []);

  function makeImage(key, url) {
    const img = document.createElement("img");
    img.setAttribute("data-key", key);
    img.src = url;
    img.alt = "";
    img.draggable = false;
    return img;
  }

  function caretRangeInside() {
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) return null;
    const range = sel.getRangeAt(0);
    return root.current && root.current.contains(range.commonAncestorContainer) ? range : null;
  }

  function insertImages(list) {
    const el = root.current;
    if (!el) return;
    const picked = Array.from(list || []).filter(Boolean);
    if (!picked.length) return;
    const room = maxImages - countImages();
    if (room <= 0) return onError?.(`사진은 ${maxImages}장까지 붙일 수 있어요.`);
    for (const f of picked) {
      if (!isImageFile(f)) return onError?.("JPG 또는 PNG 이미지만 올릴 수 있어요.");
      if (f.size > MAX_IMAGE_BYTES) return onError?.(`${f.name} — 사진은 한 장에 2MB 이하만 올릴 수 있어요.`);
    }
    if (picked.length > room) onError?.(`사진은 ${maxImages}장까지 붙일 수 있어요. 앞의 ${room}장만 붙였어요.`);
    const nodes = picked.slice(0, room).map((f) => {
      const url = URL.createObjectURL(f);
      const key = `new:${url}`;
      files.current.set(key, f);
      return makeImage(key, url);
    });
    el.focus({ preventScroll: true });
    const range = caretRangeInside();
    let anchor;
    if (range) {
      range.deleteContents();
      anchor = range;
    } else {
      anchor = document.createRange();
      anchor.selectNodeContents(el);
      anchor.collapse(false);
    }
    // 사진은 한 줄을 통째로 차지한다 — 글자 사이에 끼면 그 줄이 사진 앞뒤로 나뉜다.
    for (const node of nodes) {
      anchor.insertNode(node);
      anchor.setStartAfter(node);
      anchor.collapse(true);
    }
    const lastNode = nodes[nodes.length - 1];
    const line = document.createElement("div");
    line.appendChild(document.createElement("br"));
    lastNode.after(line);
    const sel = window.getSelection();
    const caret = document.createRange();
    caret.setStart(line, 0); caret.collapse(true);
    sel.removeAllRanges(); sel.addRange(caret);
    notifyCount();
  }

  function removeSelected() {
    const el = root.current;
    if (!el || !selectedKey) return;
    const img = el.querySelector(`img[data-key="${CSS.escape(selectedKey)}"]`);
    if (img) img.remove();
    if (selectedKey.startsWith("new:")) { URL.revokeObjectURL(selectedKey.slice(4)); files.current.delete(selectedKey); }
    setSelectedKey(null); setSelectedBox(null);
    notifyCount();
  }

  function select(img) {
    if (!img || !root.current) { setSelectedKey(null); setSelectedBox(null); return; }
    const key = img.getAttribute("data-key");
    const a = img.getBoundingClientRect();
    const b = root.current.getBoundingClientRect();
    setSelectedKey(key);
    setSelectedBox({ top: a.top - b.top + root.current.scrollTop, left: a.left - b.left, width: a.width });
  }

  useImperativeHandle(ref, () => ({
    insertFiles: insertImages,
    collect() {
      const el = root.current;
      const keys = el ? editorImageKeys(el) : [];
      const plan = planImageOrder(keys, existingOrder.current);
      return {
        body: el ? serializeEditor(el, plan.indexOf) : "",
        keepImageIds: plan.keepIds,
        files: plan.newKeys.map((key) => files.current.get(key)).filter(Boolean),
      };
    },
    count: countImages,
  }));

  return (
    <div className="board-editor-wrap">
      <div
        ref={root}
        className="board-editor field"
        contentEditable
        suppressContentEditableWarning
        role="textbox"
        aria-multiline="true"
        aria-label="내용"
        data-placeholder="내용을 적고, 사진은 원하는 자리에 커서를 두고 붙여요."
        onInput={() => { notifyCount(); if (selectedKey && !root.current.querySelector(`img[data-key="${CSS.escape(selectedKey)}"]`)) select(null); }}
        onClick={(e) => select(e.target.tagName === "IMG" ? e.target : null)}
        onKeyDown={(e) => {
          if ((e.key === "Backspace" || e.key === "Delete") && selectedKey) { e.preventDefault(); removeSelected(); }
        }}
        onPaste={(e) => {
          const items = Array.from(e.clipboardData?.items || []);
          const images = items.filter((it) => it.kind === "file" && isImageFile(it.getAsFile())).map((it) => it.getAsFile());
          e.preventDefault();
          if (images.length) return insertImages(images);
          const text = e.clipboardData.getData("text/plain");
          if (text) document.execCommand("insertText", false, text);
        }}
        onDrop={(e) => {
          const dropped = Array.from(e.dataTransfer?.files || []).filter(isImageFile);
          if (dropped.length) { e.preventDefault(); insertImages(dropped); }
        }}
        onBlur={(e) => { if (!e.relatedTarget?.classList?.contains("board-editor-remove")) return; }}
      />
      {selectedKey && selectedBox ? (
        <button
          type="button"
          className="board-editor-remove"
          style={{ top: selectedBox.top - 10, left: selectedBox.left + selectedBox.width - 14 }}
          onMouseDown={(e) => e.preventDefault()}
          onClick={removeSelected}
          aria-label="선택한 사진 빼기"
        >
          ✕
        </button>
      ) : null}
    </div>
  );
});

export default BoardBodyEditor;
