// Shared reminder for both historical backtests and live-price paper results.
export default function SimBadge({ className = "" }) {
  return (
    <span
      className={"badge badge-ai " + className}
      title="백테스트와 페이퍼 결과는 시뮬레이션이고 실제 거래가 아니에요."
    >
      모의 결과 · 실거래 아님
    </span>
  );
}
