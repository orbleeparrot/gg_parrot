import { useEffect, useState } from "react";
import { api } from "../api.js";
import { useAuth } from "../lib/auth.js";
import { PageHeader } from "../components/Page.jsx";
import "./Support.css";

// 고객센터 — 지금은 이메일로만 받는다. 주소는 서버 설정(SUPPORT_EMAIL)에서 온다.
export default function Support() {
  const { user } = useAuth();
  const [email, setEmail] = useState(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let alive = true;
    api.supportInfo().then((d) => { if (alive) setEmail(d.email || ""); }).catch(() => { if (alive) setEmail(""); });
    return () => { alive = false; };
  }, []);

  const subject = encodeURIComponent(`[껄무새 문의]${user ? ` ${user.username}` : ""}`);
  const body = encodeURIComponent(`어떤 화면에서 무엇을 하다가 어떤 일이 있었는지 적어 주세요.\n\n계정: ${user ? user.username : "(로그인 전)"}\n화면: \n시각: \n내용: `);

  async function copy() {
    try {
      await navigator.clipboard.writeText(email);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch { /* 클립보드 막힘 — 주소는 화면에 있다 */ }
  }

  return (
    <div className="support-page">
      <PageHeader title="고객센터" />
      <section className="support-section">
        <h2 className="support-head">이메일로 문의해 주세요</h2>
        <p className="support-copy">지금은 이메일로만 문의를 받고 있어요. 확인하는 대로 답장드릴게요.</p>
        {email === null ? (
          <p className="support-address is-loading" aria-busy="true">주소를 불러오는 중…</p>
        ) : email ? (
          <div className="support-address-row">
            <a className="support-address" href={`mailto:${email}?subject=${subject}&body=${body}`}>{email}</a>
            <a className="btn btn-m btn-primary" href={`mailto:${email}?subject=${subject}&body=${body}`}>메일 쓰기</a>
            <button type="button" className="btn btn-m btn-secondary" onClick={copy}>{copied ? "복사했어요" : "주소 복사"}</button>
          </div>
        ) : (
          <p className="support-copy">문의 이메일 주소를 준비하고 있어요. 조금 뒤에 다시 확인해 주세요.</p>
        )}
      </section>
      <section className="support-section">
        <h2 className="support-head">이렇게 적어 주시면 빨라요</h2>
        <ul className="support-list">
          <li><b>계정 이름</b> — 로그인한 닉네임</li>
          <li><b>어느 화면</b>에서 <b>무엇을 하다가</b> 생긴 일인지</li>
          <li><b>시각</b>과, 있다면 <b>화면 캡처</b></li>
          <li>실행기 문제라면 실행기 창에 보이는 <b>오류 문구</b></li>
        </ul>
      </section>
      <section className="support-section">
        <h2 className="support-head">먼저 확인해 보세요</h2>
        <ul className="support-list">
          <li>사용법은 상단의 <b>사용법</b>, 실행기는 <b>실행기 설치</b> 페이지에 정리돼 있어요.</li>
          <li>비밀번호를 잊었다면 로그인 화면의 <b>비밀번호 찾기</b>로 바로 다시 정할 수 있어요.</li>
          <li>웹의 결과는 모의 계산이며 투자 조언이 아니에요.</li>
        </ul>
      </section>
    </div>
  );
}
