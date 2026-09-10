import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { EditorContent, NodeViewWrapper, ReactNodeViewRenderer, useEditor, useEditorState } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import Image from "@tiptap/extension-image";
import TextAlign from "@tiptap/extension-text-align";
import { TextStyleKit } from "@tiptap/extension-text-style";
import { TextBIcon } from "@phosphor-icons/react/dist/csr/TextB";
import { TextItalicIcon } from "@phosphor-icons/react/dist/csr/TextItalic";
import { TextUnderlineIcon } from "@phosphor-icons/react/dist/csr/TextUnderline";
import { TextStrikethroughIcon } from "@phosphor-icons/react/dist/csr/TextStrikethrough";
import { TextHTwoIcon } from "@phosphor-icons/react/dist/csr/TextHTwo";
import { TextHThreeIcon } from "@phosphor-icons/react/dist/csr/TextHThree";
import { TextAlignLeftIcon } from "@phosphor-icons/react/dist/csr/TextAlignLeft";
import { TextAlignCenterIcon } from "@phosphor-icons/react/dist/csr/TextAlignCenter";
import { TextAlignRightIcon } from "@phosphor-icons/react/dist/csr/TextAlignRight";
import { ListBulletsIcon } from "@phosphor-icons/react/dist/csr/ListBullets";
import { ListNumbersIcon } from "@phosphor-icons/react/dist/csr/ListNumbers";
import { QuotesIcon } from "@phosphor-icons/react/dist/csr/Quotes";
import { LinkSimpleIcon } from "@phosphor-icons/react/dist/csr/LinkSimple";
import { ImageIcon as PhotoIcon } from "@phosphor-icons/react/dist/csr/Image";
import { EraserIcon } from "@phosphor-icons/react/dist/csr/Eraser";
import { ArrowCounterClockwiseIcon } from "@phosphor-icons/react/dist/csr/ArrowCounterClockwise";
import { ArrowClockwiseIcon } from "@phosphor-icons/react/dist/csr/ArrowClockwise";
import { prepareHtmlForSave } from "../lib/boardHtml.js";

// 글 본문 편집기 — TipTap(ProseMirror). 굵게·기울임·밑줄·취소선·제목·글자 크기·글자색·정렬·목록·인용·링크,
// 사진은 커서 자리에 들어가고 끌어서 옮기며 모서리를 끌어 크기를 바꾼다. 저장은 정제된 HTML(서버 nh3).
const MAX_IMAGE_BYTES = 2 * 1024 * 1024;
const FONT_SIZES = [["13px", "작게"], ["", "보통"], ["17px", "조금 크게"], ["20px", "크게"], ["24px", "아주 크게"]];
// 글자색 — 시맨틱 색(상승·하락·경고·링크)과 흐림. 본문 기본색은 '지우기'.
const COLORS = [["#f6465d", "빨강"], ["#0ecb81", "초록"], ["#3b82f6", "파랑"], ["#f59e0b", "주황"], ["#a68000", "금색"], ["#8b95a5", "회색"]];

function isImageFile(file) {
  return file && ["image/jpeg", "image/png"].includes(file.type);
}

// 사진 노드 — 모서리 손잡이로 폭을 바꾸고, 정렬은 data-align. 끌어서 옮기기는 ProseMirror 가 한다(data-drag-handle).
function ImageView({ node, updateAttributes, selected }) {
  const drag = useRef(null);
  function startResize(e) {
    e.preventDefault();
    const img = e.currentTarget.parentElement.querySelector("img");
    const startX = e.clientX;
    const startW = img.getBoundingClientRect().width;
    const maxW = img.parentElement.parentElement.getBoundingClientRect().width;
    const move = (ev) => updateAttributes({ width: Math.round(Math.min(maxW, Math.max(80, startW + ev.clientX - startX))) });
    const stop = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", stop); };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
    drag.current = true;
  }
  return (
    <NodeViewWrapper as="figure" className={`board-img${selected ? " is-selected" : ""}`} data-align={node.attrs["data-align"] || "left"} data-drag-handle>
      <img src={node.attrs.src} alt={node.attrs.alt || ""} width={node.attrs.width || undefined} draggable={false} />
      {selected ? <span className="board-img-handle" onPointerDown={startResize} aria-hidden="true" /> : null}
    </NodeViewWrapper>
  );
}

const BoardImage = Image.extend({
  addAttributes() {
    return {
      ...this.parent?.(),
      "data-align": { default: null, parseHTML: (el) => el.getAttribute("data-align"), renderHTML: (attrs) => (attrs["data-align"] ? { "data-align": attrs["data-align"] } : {}) },
    };
  },
  addNodeView() {
    return ReactNodeViewRenderer(ImageView);
  },
});

function ToolButton({ label, active, disabled, onClick, children }) {
  return (
    <button type="button" className={`board-tool${active ? " is-on" : ""}`} aria-label={label} title={label} aria-pressed={active} disabled={disabled} onMouseDown={(e) => e.preventDefault()} onClick={onClick}>
      {children}
    </button>
  );
}

const BoardBodyEditor = forwardRef(function BoardBodyEditor({ initialHtml = "", maxImages = 10, onError, onCountChange }, ref) {
  const files = useRef(new Map()); // blob url → File
  const fileInput = useRef(null);
  const [colorOpen, setColorOpen] = useState(false);

  const editor = useEditor({
    extensions: [
      StarterKit.configure({ heading: { levels: [2, 3] }, codeBlock: false, code: false, horizontalRule: false, link: { openOnClick: false, autolink: true, defaultProtocol: "https" } }),
      TextStyleKit.configure({ fontFamily: false, lineHeight: false, backgroundColor: false }),
      TextAlign.configure({ types: ["heading", "paragraph"] }),
      BoardImage.configure({ inline: false, allowBase64: false }),
    ],
    content: initialHtml || "",
    editorProps: {
      attributes: { class: "board-editor field", "aria-label": "내용", "data-placeholder": "내용을 적어요. 사진은 원하는 자리에 커서를 두고 붙여요." },
      handlePaste: (_view, event) => {
        const items = Array.from(event.clipboardData?.items || []);
        const images = items.filter((it) => it.kind === "file" && isImageFile(it.getAsFile())).map((it) => it.getAsFile());
        if (!images.length) return false;
        insertImages(images);
        return true;
      },
      handleDrop: (_view, event) => {
        const dropped = Array.from(event.dataTransfer?.files || []).filter(isImageFile);
        if (!dropped.length) return false;
        insertImages(dropped);
        return true;
      },
    },
  });

  const state = useEditorState({
    editor,
    selector: ({ editor: ed }) => ed ? ({
      bold: ed.isActive("bold"), italic: ed.isActive("italic"), underline: ed.isActive("underline"), strike: ed.isActive("strike"),
      h2: ed.isActive("heading", { level: 2 }), h3: ed.isActive("heading", { level: 3 }),
      left: ed.isActive({ textAlign: "left" }), center: ed.isActive({ textAlign: "center" }), right: ed.isActive({ textAlign: "right" }),
      bullet: ed.isActive("bulletList"), ordered: ed.isActive("orderedList"), quote: ed.isActive("blockquote"), link: ed.isActive("link"),
      fontSize: ed.getAttributes("textStyle").fontSize || "", color: ed.getAttributes("textStyle").color || "",
      image: ed.isActive("image"), canUndo: ed.can().undo(), canRedo: ed.can().redo(),
      images: ed.state.doc.content ? countImages(ed) : 0,
    }) : null,
  });

  function countImages(ed) {
    let n = 0;
    ed.state.doc.descendants((node) => { if (node.type.name === "image") n += 1; });
    return n;
  }

  useEffect(() => { onCountChange?.(state?.images || 0); }, [state?.images, onCountChange]);
  useEffect(() => () => { for (const url of files.current.keys()) URL.revokeObjectURL(url); }, []);

  function insertImages(list) {
    if (!editor) return;
    const picked = Array.from(list || []).filter(Boolean);
    if (!picked.length) return;
    const room = maxImages - countImages(editor);
    if (room <= 0) return onError?.(`사진은 ${maxImages}장까지 붙일 수 있어요.`);
    for (const f of picked) {
      if (!isImageFile(f)) return onError?.("JPG 또는 PNG 이미지만 올릴 수 있어요.");
      if (f.size > MAX_IMAGE_BYTES) return onError?.(`${f.name} — 사진은 한 장에 2MB 이하만 올릴 수 있어요.`);
    }
    if (picked.length > room) onError?.(`사진은 ${maxImages}장까지 붙일 수 있어요. 앞의 ${room}장만 붙였어요.`);
    // 한 번에 넣는다 — setImage 를 잇달아 부르면 방금 넣어 골라진 사진 자리에 다음 사진이 덮어써서 한 장만 남는다.
    const nodes = picked.slice(0, room).map((f) => {
      const url = URL.createObjectURL(f);
      files.current.set(url, f);
      return { type: "image", attrs: { src: url, alt: "" } };
    });
    editor.chain().focus().insertContent([...nodes, { type: "paragraph" }]).run();
  }

  function setLink() {
    if (!editor) return;
    const previous = editor.getAttributes("link").href || "";
    const url = window.prompt("링크 주소", previous || "https://");
    if (url === null) return;
    if (!url.trim() || url.trim() === "https://") { editor.chain().focus().unsetLink().run(); return; }
    editor.chain().focus().extendMarkRange("link").setLink({ href: url.trim() }).run();
  }

  useImperativeHandle(ref, () => ({
    insertFiles: insertImages,
    collect() {
      if (!editor) return { html: "", files: [] };
      return prepareHtmlForSave(editor.getHTML(), files.current);
    },
    count: () => (editor ? countImages(editor) : 0),
  }));

  if (!editor || !state) return <div className="board-editor field" aria-busy="true" />;
  const run = (fn) => () => fn(editor.chain().focus()).run();
  const alignImage = (align) => () => editor.chain().focus().updateAttributes("image", { "data-align": align }).run();

  return (
    <div className="board-editor-wrap">
      <div className="board-toolbar" role="toolbar" aria-label="본문 서식">
        <ToolButton label="실행 취소" disabled={!state.canUndo} onClick={run((c) => c.undo())}><ArrowCounterClockwiseIcon size={18} /></ToolButton>
        <ToolButton label="다시 실행" disabled={!state.canRedo} onClick={run((c) => c.redo())}><ArrowClockwiseIcon size={18} /></ToolButton>
        <span className="board-tool-sep" />
        <ToolButton label="굵게" active={state.bold} onClick={run((c) => c.toggleBold())}><TextBIcon size={18} /></ToolButton>
        <ToolButton label="기울임" active={state.italic} onClick={run((c) => c.toggleItalic())}><TextItalicIcon size={18} /></ToolButton>
        <ToolButton label="밑줄" active={state.underline} onClick={run((c) => c.toggleUnderline())}><TextUnderlineIcon size={18} /></ToolButton>
        <ToolButton label="취소선" active={state.strike} onClick={run((c) => c.toggleStrike())}><TextStrikethroughIcon size={18} /></ToolButton>
        <span className="board-tool-sep" />
        <label className="board-tool-select">
          <span className="sr-only">글자 크기</span>
          <select value={state.fontSize} onMouseDown={(e) => e.stopPropagation()} onChange={(e) => { const v = e.target.value; const c = editor.chain().focus(); (v ? c.setFontSize(v) : c.unsetFontSize()).run(); }} title="글자 크기">
            {FONT_SIZES.map(([value, label]) => <option key={value || "default"} value={value}>{label}</option>)}
          </select>
        </label>
        <span className="board-tool-color">
          <ToolButton label="글자색" active={colorOpen || Boolean(state.color)} onClick={() => setColorOpen((v) => !v)}>
            <span className="board-tool-swatch" style={{ background: state.color || "currentColor" }} aria-hidden="true" />
          </ToolButton>
          {colorOpen ? (
            <span className="board-color-tray" role="group" aria-label="글자색 고르기">
              {COLORS.map(([hex, label]) => (
                <button key={hex} type="button" title={label} aria-label={label} className="board-color-dot" style={{ background: hex }} onMouseDown={(e) => e.preventDefault()} onClick={() => { editor.chain().focus().setColor(hex).run(); setColorOpen(false); }} />
              ))}
              <button type="button" className="board-color-reset" onMouseDown={(e) => e.preventDefault()} onClick={() => { editor.chain().focus().unsetColor().run(); setColorOpen(false); }}>기본색</button>
            </span>
          ) : null}
        </span>
        <span className="board-tool-sep" />
        <ToolButton label="큰 제목" active={state.h2} onClick={run((c) => c.toggleHeading({ level: 2 }))}><TextHTwoIcon size={18} /></ToolButton>
        <ToolButton label="작은 제목" active={state.h3} onClick={run((c) => c.toggleHeading({ level: 3 }))}><TextHThreeIcon size={18} /></ToolButton>
        <span className="board-tool-sep" />
        <ToolButton label="왼쪽 정렬" active={state.image ? false : state.left} onClick={state.image ? alignImage("left") : run((c) => c.setTextAlign("left"))}><TextAlignLeftIcon size={18} /></ToolButton>
        <ToolButton label="가운데 정렬" active={state.image ? false : state.center} onClick={state.image ? alignImage("center") : run((c) => c.setTextAlign("center"))}><TextAlignCenterIcon size={18} /></ToolButton>
        <ToolButton label="오른쪽 정렬" active={state.image ? false : state.right} onClick={state.image ? alignImage("right") : run((c) => c.setTextAlign("right"))}><TextAlignRightIcon size={18} /></ToolButton>
        <span className="board-tool-sep" />
        <ToolButton label="글머리 기호" active={state.bullet} onClick={run((c) => c.toggleBulletList())}><ListBulletsIcon size={18} /></ToolButton>
        <ToolButton label="번호 목록" active={state.ordered} onClick={run((c) => c.toggleOrderedList())}><ListNumbersIcon size={18} /></ToolButton>
        <ToolButton label="인용" active={state.quote} onClick={run((c) => c.toggleBlockquote())}><QuotesIcon size={18} /></ToolButton>
        <ToolButton label="링크" active={state.link} onClick={setLink}><LinkSimpleIcon size={18} /></ToolButton>
        <span className="board-tool-sep" />
        <ToolButton label="사진 넣기" disabled={state.images >= maxImages} onClick={() => fileInput.current?.click()}><PhotoIcon size={18} /></ToolButton>
        <ToolButton label="서식 지우기" onClick={run((c) => c.unsetAllMarks().clearNodes())}><EraserIcon size={18} /></ToolButton>
        <span className="board-tool-hint">
          {state.images > 0 ? <><b className="num">{state.images}</b>/{maxImages}장 · </> : null}JPG·PNG · 한 장에 2MB · {maxImages}장까지
        </span>
        <input ref={fileInput} type="file" accept="image/png,image/jpeg" multiple hidden onChange={(e) => { const picked = Array.from(e.target.files || []); e.target.value = ""; if (picked.length) insertImages(picked); }} />
      </div>
      <EditorContent editor={editor} />
    </div>
  );
});

export default BoardBodyEditor;
