// 편집기 HTML ↔ 저장 HTML. 새로 붙인 사진은 blob: 주소를 갖고 있으니, 저장할 때는
// `data-key="new:N"` 자리로 바꾸고 파일을 같은 순서로 보낸다(서버가 N번째 파일 주소를 붙인다).
// DOMParser 는 브라우저에만 있으므로 화면 코드에서만 부른다.

export function prepareHtmlForSave(html, filesByUrl) {
  const doc = new DOMParser().parseFromString(`<body>${html || ""}</body>`, "text/html");
  const files = [];
  for (const img of Array.from(doc.body.querySelectorAll("img"))) {
    const src = img.getAttribute("src") || "";
    if (src.startsWith("blob:")) {
      const file = filesByUrl.get(src);
      if (!file) { img.remove(); continue; }
      img.setAttribute("data-key", `new:${files.length}`);
      img.removeAttribute("src");
      files.push(file);
    }
  }
  return { html: doc.body.innerHTML, files };
}
