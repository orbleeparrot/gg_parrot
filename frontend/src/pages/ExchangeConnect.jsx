import { Link, useSearchParams } from "react-router-dom";
import ExchangeConnectionGuide from "../components/ExchangeConnectionGuide.jsx";
import { connectionGuidePath, parseConnectionGuide } from "../lib/exchangeConnection.js";

export default function ExchangeConnect() {
  const [params, setParams] = useSearchParams();
  const { exchange, step } = parseConnectionGuide(params);
  return (
    <div className="exchange-connect-page">
      <header>
        <h1 className="t-h2">거래소 API 연결, 한 단계씩</h1>
        <p className="t-body">실거래에 필요한 키 발급 안내예요. 연습은 키 없이 실행기의 모의 모드에서 할 수 있어요.</p>
        <nav className="exchange-connect-exchanges" aria-label="연결할 거래소">
          {[['upbit', '업비트'], ['bithumb', '빗썸']].map(([value, label]) => <Link key={value} to={connectionGuidePath({ exchange: value })} aria-current={exchange === value ? "page" : undefined} className="btn btn-m btn-secondary"><img src={`/exchanges/${value}.png`} alt="" />{label}</Link>)}
        </nav>
      </header>
      <ExchangeConnectionGuide key={exchange} exchange={exchange} initialStep={step} onStepChange={(next) => setParams({ exchange, step: next }, { replace: true })} />
      <p className="t-small mt-6"><Link to="/runner/install" className="underline">Windows 실행기 설치 안내</Link> · <Link to="/guide?section=domestic-api" className="underline">API 연결 FAQ</Link></p>
    </div>
  );
}
