import { useEffect, useId, useRef, useState } from "react";
import guideUrl from "../assets/runner-launch-guide.svg?url";
import "./RunnerLaunchGuide.css";

const MOTION_QUERY = "(prefers-reduced-motion: reduce)";

export default function RunnerLaunchGuide() {
  const captionId = useId();
  const objectRef = useRef(null);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  const [paused, setPaused] = useState(() => window.matchMedia(MOTION_QUERY).matches);

  const animations = () => objectRef.current?.contentDocument?.getAnimations() || [];

  useEffect(() => {
    const preference = window.matchMedia(MOTION_QUERY);
    const onChange = (event) => { if (event.matches) setPaused(true); };
    preference.addEventListener("change", onChange);
    return () => preference.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    if (!ready) return;
    animations().forEach((animation) => paused ? animation.pause() : animation.play());
  }, [paused, ready]);

  function onLoad() {
    const svg = objectRef.current?.contentDocument?.documentElement;
    if (svg?.localName !== "svg") {
      setFailed(true);
      return;
    }
    // Keep the original artwork intact; its standalone document fits this frame.
    svg.style.width = "100%";
    svg.style.height = "100%";
    animations().forEach((animation) => {
      animation.currentTime = 0;
      if (paused) animation.pause();
    });
    setReady(true);
  }

  function replay() {
    animations().forEach((animation) => {
      animation.currentTime = 0;
      animation.play();
    });
    setPaused(false);
  }

  return (
    <figure className="runner-launch-guide" aria-labelledby={captionId}>
      <div className="runner-launch-guide-frame">
        <object
          ref={objectRef}
          data={guideUrl}
          type="image/svg+xml"
          width="960"
          height="800"
          tabIndex={-1}
          aria-label="다운로드한 실행기 파일을 열고, 추가 정보와 실행을 눌러 실행기 창을 여는 안내 애니메이션"
          onLoad={onLoad}
          onError={() => setFailed(true)}
        >
          <p>아래 순서대로 실행기를 열어 주세요. 안내 화면은 원본 보기로도 확인할 수 있어요.</p>
        </object>
      </div>
      <figcaption id={captionId} className="runner-launch-guide-caption">
        <span className="t-caption text-slate-700">
          {failed ? "안내 화면을 불러오지 못했어요." : "파일 열기 → 추가 정보 → 실행"}
        </span>
        <div className="runner-launch-guide-controls t-caption">
          {!failed ? (
            <>
              <button type="button" onClick={() => setPaused((value) => !value)} disabled={!ready}
                aria-label={paused ? "안내 애니메이션 재생" : "안내 애니메이션 일시정지"}>
                <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
                  {paused ? <path d="m5 3 8 5-8 5V3Z" /> : <path d="M4 3h3v10H4zm5 0h3v10H9z" />}
                </svg>
                {paused ? "재생" : "일시정지"}
              </button>
              <button type="button" onClick={replay} disabled={!ready} aria-label="안내 애니메이션 처음부터 보기">처음부터</button>
            </>
          ) : null}
          <a href={guideUrl} target="_blank" rel="noopener noreferrer" aria-label="안내 화면 원본 보기, 새 탭">원본 보기 ↗</a>
        </div>
      </figcaption>
    </figure>
  );
}
