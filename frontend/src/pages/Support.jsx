import { useState } from "react";
import { useAuth } from "../lib/auth.js";
import { PageHeader } from "../components/Page.jsx";
import SelectMenu from "../components/SelectMenu.jsx";
import "./Support.css";

// 고객센터 — 접수 시스템이 아직 없어서, 양식을 채우면 메일 본문으로 만들어 메일 앱을 연다.
const CONTACTS = ["heejaerho@hecto.co.kr", "clcleh123@hecto.co.kr"];
const TOPICS = [
  ["account", "계정·로그인"],
  ["runner", "매크로·실행기"],
  ["board", "게시판·댓글"],
  ["points", "포인트·언락"],
  ["etc", "기타"],
];

function topicLabel(key) {
  return (TOPICS.find(([value]) => value === key) || TOPICS[TOPICS.length - 1])[1];
}

export default function Support() {
  const { user } = useAuth();
  const [topic, setTopic] = useState("account");
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [copied, setCopied] = useState(false);

  const subject = `[껄무새 문의] ${topicLabel(topic)}${title.trim() ? ` - ${title.trim()}` : ""}`;
  const mailBody = [
    `계정: ${user ? user.username : "(로그인 전)"}`,
    `문의 유형: ${topicLabel(topic)}`,
    "",
    body.trim() || "(내용을 적어 주세요)",
  ].join("\n");
  const mailto = `mailto:${CONTACTS.join(",")}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(mailBody)}`;

  async function copyMail() {
    try {
      await navigator.clipboard.writeText(`받는 사람: ${CONTACTS.join(", ")}\n제목: ${subject}\n\n${mailBody}`);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch { /* 클립보드가 막힌 브라우저 — 주소와 내용은 화면에 있다 */ }
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

      {/* 문의 양식 — 채우면 메일 제목·본문이 만들어진다. 보내는 건 사용자의 메일 앱. */}
      <form className="support-form" onSubmit={(e) => e.preventDefault()} aria-label="문의 양식">
        <label className="support-field">
          <span>문의 유형</span>
          <SelectMenu value={topic} options={TOPICS} onChange={setTopic} label="문의 유형" className="support-topic" />
        </label>
        <label className="support-field">
          <span>제목</span>
          <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={80} className="field field-sm" placeholder="한 줄로 요약해 주세요" />
        </label>
        <label className="support-field">
          <span>내용</span>
          <textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            maxLength={2000}
            rows={6}
            className="field support-body"
            placeholder={"어느 화면에서 무엇을 하다가 어떤 일이 있었는지 적어 주세요.\n오류 문구나 화면 캡처가 있으면 메일에 함께 첨부해 주세요."}
          />
        </label>
        <div className="support-actions">
          <a className="btn btn-m btn-primary" href={mailto}>메일 앱으로 보내기</a>
          <button type="button" className="btn btn-m btn-secondary" onClick={copyMail}>{copied ? "복사했어요" : "내용 복사"}</button>
        </div>
      </form>
    </div>
  );
}
