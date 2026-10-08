// 프로 빌더가 잠겨 있을 때 /builder/pro 가 보여 주는 화면.
// 막는 것 자체가 목적이 아니라 "왜 못 들어가는지" 와 "그럼 어디로 가면 되는지" 를 말하는 게 목적이다.
import { useEffect } from "react";
import { Link } from "react-router-dom";
import { Icon } from "../components/icons.jsx";
import { PRO_BUILDER_CLOSED } from "../lib/proBuilder.js";

export default function ProBuilderClosed() {
  useEffect(() => {
    document.title = "프로 빌더 업데이트 중 · 껄무새";
  }, []);

  return (
    <div className="mx-auto w-full max-w-xl px-4 py-10 sm:py-16">
      <div className="rounded-2xl border border-slate-200 bg-white p-6 sm:p-8">
        <p className="flex items-center gap-2 text-amber-700">
          <Icon name="pencilLine" size={18} aria-hidden="true" />
          <span className="t-label font-bold">업데이트 예정</span>
        </p>
        <h1 className="t-h2 mt-2 font-extrabold text-slate-900">{PRO_BUILDER_CLOSED.title}</h1>
        <p className="t-body mt-3 text-slate-700">{PRO_BUILDER_CLOSED.lead}</p>
        <ul className="t-body mt-5 space-y-2 text-slate-700">
          {PRO_BUILDER_CLOSED.points.map((point) => (
            <li key={point} className="flex gap-2">
              <span aria-hidden="true" className="mt-[0.45em] h-1.5 w-1.5 shrink-0 rounded-full bg-slate-400" />
              <span>{point}</span>
            </li>
          ))}
        </ul>
        <div className="mt-7 flex flex-wrap gap-2">
          <Link
            to="/builder"
            className="t-body inline-flex items-center gap-1.5 rounded-xl bg-slate-900 px-4 py-2.5 font-bold text-white hover:bg-slate-800"
          >
            기본 빌더로 가기
            <Icon name="arrowRight" size={16} aria-hidden="true" />
          </Link>
          <Link
            to="/guide"
            className="t-body inline-flex items-center rounded-xl border border-slate-300 px-4 py-2.5 font-bold text-slate-800 hover:bg-slate-50"
          >
            사용 설명 보기
          </Link>
        </div>
      </div>
    </div>
  );
}
