import { useState } from "react";
import { useAuth } from "../lib/auth.js";
import { PageHeader } from "../components/Page.jsx";
import "./Support.css";

// 고객센터 — 접수 시스템이 아직 없다. 담당자 메일 주소와, 그대로 복사해 붙여 쓰는 문의 양식만 둔다.
const CONTACTS = ["heejaerho@hecto.co.kr", "clcleh123@hecto.co.kr"];

function template(username) {
  return [
    "문의 유형 : 계정·로그인 / 매크로·실행기 / 게시판·댓글 / 포인트·언락 / 기타 중 하나",
    `계정(닉네임) : ${username || ""}`,
    "발생 시각 : ",
    "화면 : ",
    "내용 : ",
    "",
    "* 오류 문구나 화면 캡처가 있으면 메일에 함께 첨부해 주세요.",
  ].join("\n");
}

export default function Support() {
  const { user } = useAuth();
  const [copied, setCopied] = useState(false);
  const form = template(user?.username);

  async function copy() {
    try {
      await navigator.clipboard.writeText(form);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch { /* 클립보드가 막힌 브라우저 — 양식은 화면에서 직접 긁어 복사한다 */ }
  }

  return (
    <div className="support-page">
      <PageHeader title="고객센터" />
      <p className="support-copy">
        죄송합니다. 고객센터는 현재 개발 중입니다.
        <br />
        아래의 이메일로 문의를 보내주시길 바랍니다.
      </p>
      <p className="support-contact">
        담당자
        {CONTACTS.map((email, index) => (
          <span key={email}>
            {index === 0 ? " : " : ", "}
            <a href={`mailto:${email}`}>{email}</a>
          </span>
        ))}
      </p>

      <section className="support-form" aria-labelledby="support-form-title">
        <div className="support-form-head">
          <h2 id="support-form-title">문의 양식</h2>
          <button type="button" className="board-text-btn" onClick={copy}>{copied ? "복사했어요" : "복사"}</button>
        </div>
        <pre className="support-template">{form}</pre>
      </section>
    </div>
  );
}
