import { Icon } from "./icons.jsx";

const STEPS = [
  {
    title: "조건 정하기",
    description: "종목과 전략을 고르고, 필요한 숫자만 바꿔요.",
  },
  {
    title: "결과 확인",
    description: "과거 데이터로 수익률과 최대낙폭을 확인해요.",
  },
  {
    title: "리더보드 등록",
    description: "같은 조건으로 모의매매를 시작하고 오늘의 보드에 올려요.",
  },
];

// current: 0..2 is the active step, 3 means the journey is complete.
export default function JourneySteps({ current = 0, compact = false }) {
  return (
    <ol className={"journey-steps " + (compact ? "journey-steps-compact" : "")}
      aria-label="매크로 등록 과정">
      {STEPS.map((step, index) => {
        const done = current > index;
        const active = current === index;
        return (
          <li
            key={step.title}
            className={"journey-step " + (done ? "is-done " : "") + (active ? "is-active" : "")}
            aria-current={active ? "step" : undefined}
          >
            <span className="journey-node num" aria-hidden="true">
              {done ? <Icon name="check" size={14} strokeWidth={3} /> : String(index + 1).padStart(2, "0")}
            </span>
            <span className="min-w-0">
              <span className={"block t-small font-semibold " + (active || done ? "text-slate-900" : "text-slate-700")}>
                {step.title}
              </span>
              {!compact && (
                <span className="block mt-1 t-small text-slate-500">{step.description}</span>
              )}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
