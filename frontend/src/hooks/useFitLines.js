// 문단이 정해진 줄 수를 넘으면 글자 크기를 살짝 줄여 맞춘다(최소 크기까지, 0.5px 단위).
// 폭이 바뀌거나 웹폰트가 늦게 들어오면 다시 맞춘다. 요약처럼 길이가 그날그날 다른 글에 쓴다.
import { useLayoutEffect } from "react";

export default function useFitLines(ref, text, { maxLines = 4, minPx = 16 } = {}) {
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    let raf = 0;
    const fit = () => {
      el.style.fontSize = "";
      const computed = getComputedStyle(el);
      let size = parseFloat(computed.fontSize) || 16;
      const lineHeightRatio = (parseFloat(computed.lineHeight) || size * 1.5) / size;
      const fits = () => el.scrollHeight <= Math.ceil(size * lineHeightRatio * maxLines) + 1;
      while (size > minPx && !fits()) {
        size = Math.max(minPx, size - 0.5);
        el.style.fontSize = `${size}px`;
      }
    };
    const schedule = () => {
      if (typeof requestAnimationFrame === "undefined") { fit(); return; }
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(fit);
    };
    fit();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(schedule);
    observer?.observe(el);
    const fonts = typeof document !== "undefined" ? document.fonts : null;
    fonts?.ready?.then(schedule, () => {});
    fonts?.addEventListener?.("loadingdone", schedule);
    return () => {
      if (typeof cancelAnimationFrame !== "undefined") cancelAnimationFrame(raf);
      observer?.disconnect();
      fonts?.removeEventListener?.("loadingdone", schedule);
    };
  }, [ref, text, maxLines, minPx]);
}
