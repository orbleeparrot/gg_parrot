import { useEffect } from "react";
import { Link, useSearchParams } from "react-router-dom";
import ExchangeConnectionGuide from "../components/ExchangeConnectionGuide.jsx";
import { connectionGuidePath, parseConnectionGuide } from "../lib/exchangeConnection.js";

export default function ExchangeConnect() {
  const [params, setParams] = useSearchParams();
  const { exchange, step } = parseConnectionGuide(params);
  useEffect(() => {
    const safe = new URLSearchParams({ exchange, step });
    if (params.toString() !== safe.toString()) setParams(safe, { replace: true });
  }, [exchange, step, params, setParams]);
  return (
    <div className="exchange-connect-page">
      <header>
        <h1 className="t-h2">API 연결은 내 PC 실행기에서</h1>
        <p className="t-body">이 페이지는 공개 안내예요. Windows 실행기를 준비한 뒤 PC의 IP 확인, 공식 키 발급, 실행기 검사를 순서대로 마쳐요. 연습은 키 없이 모의 모드로 할 수 있어요.</p>
        <nav className="exchange-connect-exchanges" aria-label="연결할 거래소">
          {[['upbit', '업비트'], ['bithumb', '빗썸']].map(([value, label]) => <Link key={value} to={connectionGuidePath({ exchange: value })} aria-current={exchange === value ? "page" : undefined} className="btn btn-m btn-secondary"><img src={`/exchanges/${value}.png`} alt="" />{label}</Link>)}
        </nav>
      </header>
      <ExchangeConnectionGuide key={exchange} exchange={exchange} initialStep={step} initialKeyMode={step === "keys" ? "existing" : "new"} onStepChange={(next) => setParams({ exchange, step: next }, { replace: true })} />
      <p className="t-small mt-6"><Link to="/runner/install" className="underline">Windows 실행기 설치 안내</Link> · <Link to="/guide?section=domestic-api" className="underline">API 연결 FAQ</Link></p>
    </div>
  );
}
