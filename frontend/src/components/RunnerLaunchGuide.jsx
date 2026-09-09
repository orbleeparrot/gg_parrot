import guideUrl from "../assets/runner-launch-guide.svg?url";
import "./RunnerLaunchGuide.css";

export default function RunnerLaunchGuide() {
  return (
    <figure className="runner-launch-guide">
      <div className="runner-launch-guide-frame">
        <object
          data={guideUrl}
          type="image/svg+xml"
          width="960"
          height="800"
          tabIndex={-1}
          aria-label="다운로드한 실행기 파일을 열고, 추가 정보와 실행을 눌러 실행기 창을 여는 안내 애니메이션"
        />
      </div>
    </figure>
  );
}
