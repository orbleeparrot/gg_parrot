import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { api } from "../api.js";
import RunnerSessions from "../components/RunnerSessions.jsx";
import { getAuthUser, updateAuthUser, useAuth } from "../lib/auth.js";
import { RULE_TYPES } from "../lib/macro.js";
import { getUserId } from "../lib/user.js";

const MAX_FILE_BYTES = 2 * 1024 * 1024;
const RUNNER_OPENED_STORAGE_KEY = "ggparrot:runner-opened";
const OFFICIAL_RUNNER_DOWNLOAD_URL = "https://github.com/orbleeparrot/gg_parrot/releases/download/runner-v2/ggparrot-runner.exe";

const CHAPTERS = ["매크로 연결", "실행기 준비", "계정 연결", "실행"];

const COPY = [
  {
    eyebrow: "내 계정에서 시작",
    title: <>내 매크로를 골라<br /><span>바로 연결해요.</span></>,
    description: "리더보드에 등록했거나 언락한 매크로, 직접 가져온 매크로가 내 계정에 보관돼요. 파일부터 찾을 필요 없이 여기서 하나만 고르면 돼요.",
  },
  {
    eyebrow: "내 PC에 한 번만",
    title: <>실행기를 준비하면<br /><span>다음부터 더 빨라져요.</span></>,
    description: "껄무새 실행기는 내 PC에서 주문을 처리해요. 바이낸스 키는 웹이나 껄무새 서버로 보내지 않아요.",
  },
  {
    eyebrow: "껄무새 계정 연결",
    title: <>웹에서 고른 매크로를<br /><span>내 실행기와 이어줘요.</span></>,
    description: "회원 키를 복사할 필요 없이, 실행기를 열 때 지금 로그인한 계정과 선택한 매크로를 자동으로 연결해요.",
  },
  {
    eyebrow: "테스트넷으로 먼저",
    title: <>실행기에서 시작하고<br /><span>이 화면에서 확인해요.</span></>,
    description: "실행기 열기를 누르면 계정과 매크로가 한 번에 전달돼요. 연결이 확인되면 이 화면에서 바로 알려드려요.",
  },
];

const SOURCE_LABEL = {
  created: "내가 등록",
  leaderboard: "리더보드에서 가져옴",
  upload: "직접 가져옴",
  builder: "빌더에서 저장",
};

function legacyDashboardMacros(data) {
  const created = Array.isArray(data?.created) ? data.created : [];
  const purchased = Array.isArray(data?.purchased) ? data.purchased : [];
  return [
    ...created.map((item) => ({
      id: `legacy-created-${item.entry_id}`,
      name: `${String(item.symbol || "BTCUSDT").replace(/USDT$/, "")} 매크로`,
      symbol: item.symbol,
      human_summary: item.human_summary,
      source_type: "created",
      source_ref: String(item.entry_id),
      macro: item.macro,
    })),
    ...purchased.map((item) => ({
      id: `legacy-leaderboard-${item.entry_id}`,
      name: `${String(item.symbol || "BTCUSDT").replace(/USDT$/, "")} 매크로`,
      symbol: item.symbol,
      human_summary: item.human_summary,
      source_type: "leaderboard",
      source_ref: String(item.entry_id),
      macro: item.macro,
    })),
  ].filter((item) => item.macro && item.symbol);
}

function fmtSize(bytes) {
  if (!bytes) return "";
  const mb = bytes / (1024 * 1024);
  return mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function macroMarket(macro) {
  if (macro.market === "spot") return "현물";
  if (macro.market === "futures") return "선물";
  return macro.position_side === "short" || Number(macro.leverage || 1) > 1 ? "선물" : "현물";
}

function performanceView(item) {
  const performance = item?.performance;
  const value = performance?.return_pct;
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return { label: "성과", text: "측정 전", tone: "is-neutral" };
  }
  const label = performance.kind === "paper" ? "모의 수익률" : "백테스트";
  return {
    label: performance.period_label ? `${label} · ${performance.period_label}` : label,
    text: `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`,
    tone: value >= 0 ? "is-positive" : "is-negative",
  };
}

function leaderboardReturn(item) {
  const value = item?.return_pct;
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return { text: "집계 중", tone: "is-neutral" };
  }
  return {
    text: `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`,
    tone: value >= 0 ? "is-positive" : "is-negative",
  };
}

function missingLeaderboardImportRoute(error) {
  return error?.status === 404 || error?.status === 405;
}

function withLeaderboardPerformance(item, entry) {
  if (!item || item.performance) return item;
  const value = entry?.return_pct;
  if (typeof value !== "number" || !Number.isFinite(value)) return item;
  return {
    ...item,
    performance: {
      kind: "paper",
      return_pct: value,
      status: entry.paper_status || "",
      period_label: "",
    },
  };
}

function MacroSummary({ item, compact = false }) {
  const macro = item?.macro || {};
  return (
    <div className={`runner-wizard-macro ${compact ? "is-compact" : ""}`}>
      <div className="runner-wizard-macro-head">
        <span>{SOURCE_LABEL[item.source_type] || "내 매크로"}</span>
        <span>{macroMarket(macro)} · {Number(macro.leverage || 1)}배</span>
      </div>
      <div className="runner-wizard-macro-title">
        <div>
          <strong className="num">{item.symbol}</strong>
          <h3>{item.name}</h3>
        </div>
        <span>{macro.position_side === "short" ? "숏" : "롱"}</span>
      </div>
      <p>{item.human_summary || `${item.symbol} · ${RULE_TYPES[item.rule_type]?.label || item.rule_type}`}</p>
      <dl>
        <div><dt>전략</dt><dd>{RULE_TYPES[item.rule_type]?.label || item.rule_type}</dd></div>
        <div><dt>주기</dt><dd className="num">{macro.candle_interval || "-"}</dd></div>
        <div><dt>손실 제한</dt><dd className={macro.risk?.stop_loss_pct == null ? "" : "num"}>{macro.risk?.stop_loss_pct == null ? "사용 안 함" : `${macro.risk.stop_loss_pct}%`}</dd></div>
      </dl>
    </div>
  );
}

function LibraryActions({ leaderboardOpen = false, onLeaderboard, onUpload, uploadBusy }) {
  return (
    <footer className="runner-wizard-library-actions">
      <button type="button" onClick={onLeaderboard} className="btn btn-m btn-secondary">
        {leaderboardOpen ? "내 매크로로 돌아가기" : "리더보드에서 가져오기"}
      </button>
      <button type="button" onClick={onUpload} disabled={uploadBusy} className="btn btn-m btn-secondary">
        {uploadBusy ? "계정에 저장 중…" : "파일에서 가져오기"}
      </button>
    </footer>
  );
}

function MacroPicker({ items, selected, onSelect, onLeaderboard, onUpload, uploadBusy, uploadError, fileInputRef, onFileChange }) {
  return (
    <div className="runner-wizard-library">
      <input
        ref={fileInputRef}
        type="file"
        accept=".json,.ggm.json,application/json"
        onChange={onFileChange}
        className="sr-only"
        aria-label="껄무새 매크로 파일 가져오기"
      />
      <div className="runner-wizard-macro-list" role="radiogroup" aria-label="내 매크로 목록">
        {items.map((item) => {
          const active = selected?.id === item.id;
          const macro = item.macro || {};
          const result = performanceView(item);
          return (
            <button
              key={item.id}
              type="button"
              role="radio"
              aria-checked={active}
              onClick={() => onSelect(item.id)}
              className={active ? "is-selected" : ""}
            >
              <span className="runner-wizard-radio" aria-hidden="true" />
              <span className="runner-wizard-macro-list-copy">
                <span className="runner-wizard-macro-list-meta">
                  {SOURCE_LABEL[item.source_type] || "내 매크로"} · {macroMarket(macro)} · {macro.position_side === "short" ? "숏" : "롱"}
                </span>
                <span className="runner-wizard-macro-list-title">
                  <strong className="num">{item.symbol}</strong>
                  <span>{item.name}</span>
                </span>
                <small>{item.human_summary || `${item.symbol} · ${RULE_TYPES[item.rule_type]?.label || item.rule_type}`}</small>
              </span>
              <span className={`runner-wizard-macro-return ${result.tone}`}>
                <strong className={result.tone === "is-neutral" ? "" : "num"}>{result.text}</strong>
                {result.tone === "is-neutral" ? null : <small>{result.label}</small>}
              </span>
            </button>
          );
        })}
      </div>
      {uploadError ? <p className="runner-wizard-library-message runner-wizard-error" role="alert">{uploadError}</p> : null}
      <LibraryActions onLeaderboard={onLeaderboard} onUpload={onUpload} uploadBusy={uploadBusy} />
    </div>
  );
}

function EmptyLibrary({ onLeaderboard, onUpload, uploadBusy, uploadError, libraryNotice, fileInputRef, onFileChange }) {
  return (
    <div className="runner-wizard-library is-empty">
      <input
        ref={fileInputRef}
        type="file"
        accept=".json,.ggm.json,application/json"
        onChange={onFileChange}
        className="sr-only"
        aria-label="껄무새 매크로 파일 업로드"
      />
      <div className="runner-wizard-empty-library">
        <div className="runner-wizard-empty-mark num">0</div>
        <h2>아직 연결할 매크로가 없어요.</h2>
        <p>리더보드에서 고르거나 가지고 있는 매크로 파일을 가져와요.</p>
        {libraryNotice ? <p className="runner-wizard-library-notice" role="status">{libraryNotice}</p> : null}
      </div>
      {uploadError ? <p className="runner-wizard-library-message runner-wizard-error" role="alert">{uploadError}</p> : null}
      <LibraryActions onLeaderboard={onLeaderboard} onUpload={onUpload} uploadBusy={uploadBusy} />
    </div>
  );
}

function InlineLeaderboard({ items, ownedItems, selectedId, busy, error, importingId, onImport, onBack, onUpload, uploadBusy, uploadError, fileInputRef, onFileChange }) {
  return (
    <div className="runner-wizard-library">
      <input
        ref={fileInputRef}
        type="file"
        accept=".json,.ggm.json,application/json"
        onChange={onFileChange}
        className="sr-only"
        aria-label="껄무새 매크로 파일 가져오기"
      />
      <div className="runner-wizard-leaderboard-list" aria-label="오늘의 리더보드 매크로">
        {busy ? <div className="runner-wizard-inline-state" role="status">오늘의 수익률을 불러오고 있어요…</div> : null}
        {!busy && items.length === 0 ? <div className="runner-wizard-inline-state">오늘 등록된 매크로가 아직 없어요.</div> : null}
        {!busy ? items.map((item, index) => {
          const result = leaderboardReturn(item);
          const owned = ownedItems.find((row) => (
            ["created", "leaderboard"].includes(row.source_type)
            && String(row.source_ref) === String(item.id)
          ));
          const ownedAndSelected = owned && owned.id === selectedId;
          return (
            <div key={item.id} className="runner-wizard-leaderboard-row">
              <span className="runner-wizard-leaderboard-rank num">{String(index + 1).padStart(2, "0")}</span>
              <div className="runner-wizard-leaderboard-copy">
                <span>{item.username || item.nickname || "익명"}{item.is_owner ? " · 내 매크로" : ""}</span>
                <strong className="num">{item.symbol}</strong>
                <small>{item.locked ? "가져오면 전략 설정을 확인할 수 있어요." : item.human_summary}</small>
              </div>
              <div className="runner-wizard-leaderboard-side">
                <strong className={`${result.tone === "is-neutral" ? "" : "num"} ${result.tone}`.trim()}>{result.text}</strong>
                <button
                  type="button"
                  onClick={() => onImport(item)}
                  disabled={importingId === item.id || ownedAndSelected}
                  className="btn btn-s btn-secondary"
                >
                  {importingId === item.id
                    ? "가져오는 중…"
                    : owned
                      ? ownedAndSelected ? "선택됨" : "선택"
                      : item.locked
                        ? `언락 · ${Number(item.unlock_price || 0).toLocaleString()}P`
                        : item.for_sale ? "가져오기" : "무료 가져오기"}
                </button>
              </div>
            </div>
          );
        }) : null}
      </div>
      {error ? <p className="runner-wizard-library-message runner-wizard-error" role="alert">{error}</p> : null}
      {uploadError ? <p className="runner-wizard-library-message runner-wizard-error" role="alert">{uploadError}</p> : null}
      <LibraryActions leaderboardOpen onLeaderboard={onBack} onUpload={onUpload} uploadBusy={uploadBusy} />
    </div>
  );
}

function Workspace({ title, status, bodyClassName = "", children }) {
  return (
    <div className="runner-wizard-workspace">
      <header>
        <strong>{title}</strong>
        <span>{status}</span>
      </header>
      <div className={`runner-wizard-workspace-body ${bodyClassName}`.trim()}>{children}</div>
    </div>
  );
}

export default function RunnerDownload() {
  const { token, user } = useAuth();
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const headingRef = useRef(null);
  const fileInputRef = useRef(null);
  const performanceHintsRef = useRef(new Map());
  const [library, setLibrary] = useState([]);
  const [libraryBusy, setLibraryBusy] = useState(false);
  const [libraryError, setLibraryError] = useState("");
  const [selectedId, setSelectedId] = useState(location.state?.selectedMacroId || null);
  const [libraryView, setLibraryView] = useState("mine");
  const [leaderboardItems, setLeaderboardItems] = useState([]);
  const [leaderboardBusy, setLeaderboardBusy] = useState(false);
  const [leaderboardError, setLeaderboardError] = useState("");
  const [importingLeaderboardId, setImportingLeaderboardId] = useState(0);
  const [uploadBusy, setUploadBusy] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [downloadInfo, setDownloadInfo] = useState(null);
  const [downloadError, setDownloadError] = useState("");
  const [runnerReady, setRunnerReady] = useState(() => (
    window.localStorage.getItem(RUNNER_OPENED_STORAGE_KEY) === "true"
  ));
  const [downloadStarted, setDownloadStarted] = useState(false);
  const [launchTicket, setLaunchTicket] = useState(null);
  const [launchPhase, setLaunchPhase] = useState("idle");
  const [launchAttempt, setLaunchAttempt] = useState(0);
  const [launchOpenAttempt, setLaunchOpenAttempt] = useState(0);
  const [showLaunchRecovery, setShowLaunchRecovery] = useState(false);
  const [launchError, setLaunchError] = useState("");
  const [manualDownloadBusy, setManualDownloadBusy] = useState(false);
  const [manualDownloadError, setManualDownloadError] = useState("");

  const signedIn = !!(token && user);
  const stepParam = Number(searchParams.get("step") || 1);
  const requestedStep = Number.isFinite(stepParam) ? Math.min(3, Math.max(0, stepParam - 1)) : 0;
  const preferredSourceRef = location.state?.selectedSourceRef ? String(location.state.selectedSourceRef) : "";
  const selected = useMemo(() => (
    library.find((item) => item.id === selectedId)
    || library.find((item) => preferredSourceRef && item.source_ref === preferredSourceRef)
    || library[0]
    || null
  ), [library, preferredSourceRef, selectedId]);
  const step = signedIn && selected ? requestedStep : 0;
  const copy = COPY[step];
  const downloadChecked = downloadInfo != null || !!downloadError;
  const supportsLaunch = downloadInfo?.supports_launch === true;
  const officialRunnerFallback = downloadChecked && !downloadInfo?.available && !!OFFICIAL_RUNNER_DOWNLOAD_URL;
  const runnerAvailable = !!downloadInfo?.available || officialRunnerFallback;
  const downloadUrl = downloadInfo?.available
    ? downloadInfo.url || api.runnerDownloadUrl
    : OFFICIAL_RUNNER_DOWNLOAD_URL;
  const downloadIsExternal = /^https?:\/\//i.test(downloadUrl);
  const runnerDownloadState = !downloadChecked
    ? "loading"
    : runnerAvailable
      ? "available"
      : downloadError
        ? "error"
        : "unavailable";

  function mergePerformanceHints(items) {
    return items.map((item) => {
      if (item.performance) return item;
      const performance = performanceHintsRef.current.get(String(item.id));
      return performance ? { ...item, performance } : item;
    });
  }

  useEffect(() => {
    if (!signedIn) {
      performanceHintsRef.current.clear();
      setLibrary([]);
      setLibraryError("");
      return undefined;
    }
    let alive = true;
    setLibrary([]);
    setLibraryError("");
    setLibraryBusy(true);
    async function loadLibrary({ background = false } = {}) {
      try {
        const data = await api.myMacros();
        if (!alive) return;
        setLibrary(mergePerformanceHints(Array.isArray(data?.items) ? data.items : []));
        setLibraryError("");
      } catch (primaryError) {
        try {
          // Compatibility path while an older backend is still running. Its
          // dashboard already contains registered and unlocked macro JSON.
          const dashboard = await api.myDashboard();
          if (!alive) return;
          setLibrary(mergePerformanceHints(legacyDashboardMacros(dashboard)));
          setLibraryError("");
        } catch (_) {
          if (!alive) return;
          if (!background) setLibrary([]);
          setLibraryError(
            primaryError?.status === 401
              ? "로그인이 만료됐을 수 있어요. 다시 로그인하거나 아래 방법으로 매크로를 선택해 주세요."
              : "계정 목록을 확인하지 못했지만, 아래에서 리더보드나 파일로 시작할 수 있어요.",
          );
        }
      } finally {
        if (alive && !background) setLibraryBusy(false);
      }
    }
    void loadLibrary();
    const poll = window.setInterval(() => void loadLibrary({ background: true }), 5000);
    return () => {
      alive = false;
      window.clearInterval(poll);
    };
  }, [signedIn]);

  useEffect(() => {
    if (!signedIn || libraryView !== "leaderboard") return undefined;
    let alive = true;
    setLeaderboardBusy(true);
    async function loadLeaderboard({ background = false } = {}) {
      try {
        const data = await api.leaderboard(getUserId());
        if (!alive) return;
        setLeaderboardItems(Array.isArray(data?.items) ? data.items : []);
        setLeaderboardError("");
      } catch (error) {
        if (alive) setLeaderboardError(`리더보드를 불러오지 못했어요: ${String(error.message || error)}`);
      } finally {
        if (alive && !background) setLeaderboardBusy(false);
      }
    }
    void loadLeaderboard();
    const poll = window.setInterval(() => void loadLeaderboard({ background: true }), 5000);
    return () => {
      alive = false;
      window.clearInterval(poll);
    };
  }, [libraryView, signedIn]);

  useEffect(() => {
    let alive = true;
    api.runnerDownloadInfo()
      .then((data) => {
        if (alive) setDownloadInfo(data);
      })
      .catch((error) => {
        if (alive) setDownloadError(String(error.message || error));
      });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (step !== 3 || !signedIn || !selected?.id) return undefined;

    setLaunchTicket(null);
    setLaunchError("");
    setManualDownloadError("");
    setShowLaunchRecovery(false);
    if (!downloadChecked) {
      setLaunchPhase("checking");
      return undefined;
    }
    if (!supportsLaunch) {
      setLaunchPhase("unsupported");
      return undefined;
    }

    let alive = true;
    setLaunchPhase("preparing");
    api.runnerLaunchTicketIssue(selected.id, true)
      .then((data) => {
        if (!alive) return;
        const launchId = data?.launch_id ?? data?.id;
        if (!launchId || typeof data?.launch_url !== "string" || !data.launch_url) {
          throw new Error("실행기 연결 주소가 비어 있어요.");
        }
        setLaunchTicket({ ...data, launch_id: launchId });
        setLaunchPhase("ready");
      })
      .catch((error) => {
        if (!alive) return;
        setLaunchError(
          error?.code === "NON_JSON_RESPONSE"
            ? "서버가 아직 빠른 실행 연결을 지원하지 않아요."
            : String(error.message || error),
        );
        setLaunchPhase("error");
      });
    return () => { alive = false; };
  }, [downloadChecked, launchAttempt, selected?.id, signedIn, step, supportsLaunch]);

  useEffect(() => {
    if (step !== 3 || launchPhase !== "opening" || !launchTicket?.launch_id) return undefined;
    let alive = true;
    let pollTimer = 0;
    let pollFailures = 0;

    async function pollLaunchStatus() {
      try {
        const data = await api.runnerLaunchTicketStatus(launchTicket.launch_id);
        if (!alive) return;
        pollFailures = 0;
        const status = String(data?.status || "").toLowerCase();
        if (status === "claimed" || data?.claimed === true || !!data?.claimed_at) {
          setLaunchPhase("claimed");
          return;
        }
        if (["expired", "cancelled", "revoked"].includes(status)) {
          setLaunchError("연결 시간이 만료됐어요. 새 연결을 준비해 주세요.");
          setLaunchPhase("error");
          return;
        }
      } catch (error) {
        if (!alive) return;
        pollFailures += 1;
        if (pollFailures >= 5) {
          setLaunchError(`실행기 연결 상태를 확인하지 못했어요: ${String(error.message || error)}`);
          setLaunchPhase("error");
          return;
        }
      }
      if (alive) pollTimer = window.setTimeout(pollLaunchStatus, 1000);
    }

    pollTimer = window.setTimeout(pollLaunchStatus, 1000);
    return () => {
      alive = false;
      window.clearTimeout(pollTimer);
    };
  }, [launchPhase, launchTicket?.launch_id, step]);

  useEffect(() => {
    if (launchPhase !== "opening") {
      setShowLaunchRecovery(false);
      return undefined;
    }
    const recoveryTimer = window.setTimeout(() => setShowLaunchRecovery(true), 4000);
    return () => window.clearTimeout(recoveryTimer);
  }, [launchOpenAttempt, launchPhase]);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => headingRef.current?.focus({ preventScroll: true }));
    return () => window.cancelAnimationFrame(frame);
  }, [step]);

  function moveTo(nextStep) {
    const next = new URLSearchParams(searchParams);
    next.set("step", String(nextStep + 1));
    setSearchParams(next, { replace: true });
  }

  function previous() {
    moveTo(Math.max(0, step - 1));
  }

  function beginLaunchWait() {
    setLaunchError("");
    setShowLaunchRecovery(false);
    setLaunchOpenAttempt((current) => current + 1);
    setLaunchPhase("opening");
  }

  function retryLaunchTicket() {
    setLaunchError("");
    setLaunchAttempt((current) => current + 1);
  }

  async function retryRunnerDownloadInfo() {
    setDownloadInfo(null);
    setDownloadError("");
    try {
      setDownloadInfo(await api.runnerDownloadInfo());
    } catch (error) {
      setDownloadError(String(error.message || error));
    }
  }

  function confirmRunnerReady({ advance = false } = {}) {
    window.localStorage.setItem(RUNNER_OPENED_STORAGE_KEY, "true");
    setRunnerReady(true);
    if (advance) moveTo(2);
  }

  async function importFile(file) {
    setUploadError("");
    if (!file) return;
    if (file.size > MAX_FILE_BYTES) {
      setUploadError("2MB 이하의 껄무새 매크로 파일을 선택해 주세요.");
      return;
    }
    setUploadBusy(true);
    try {
      const macro = JSON.parse(await file.text());
      if (!macro || typeof macro !== "object" || !macro.symbol || !macro.rule_type || !macro.params) {
        throw new Error("invalid macro");
      }
      const name = file.name.replace(/\.ggm\.json$|\.json$/i, "");
      const data = await api.saveMyMacro(macro, name);
      const item = data.item;
      setLibrary((current) => [item, ...current.filter((row) => row.id !== item.id)]);
      setSelectedId(item.id);
      setLibraryView("mine");
    } catch (error) {
      const message = String(error.message || error);
      setUploadError(
        message === "invalid macro"
          ? "껄무새에서 받은 .ggm.json 파일인지 확인해 주세요."
          : error?.code === "NON_JSON_RESPONSE"
            ? "계정 저장 기능이 아직 서버에 연결되지 않았어요. 백엔드를 새로고침한 뒤 다시 시도해 주세요."
            : `매크로를 저장하지 못했어요: ${message}`,
      );
    } finally {
      setUploadBusy(false);
    }
  }

  function onFileChange(event) {
    const file = event.target.files?.[0];
    void importFile(file);
    event.target.value = "";
  }

  async function importLeaderboardMacro(entry) {
    if (importingLeaderboardId) return;
    setLeaderboardError("");
    setImportingLeaderboardId(entry.id);
    try {
      let imported = library.find((item) => (
        ["created", "leaderboard"].includes(item.source_type)
        && String(item.source_ref) === String(entry.id)
      )) || null;
      if (imported) {
        setSelectedId(imported.id);
        setLibraryView("mine");
        return;
      }
      let unlockedSnapshot = null;
      let accessibleMacro = entry.macro || null;
      if (entry.locked) {
        const unlocked = await api.leaderboardUnlock(entry.id);
        unlockedSnapshot = unlocked.user_macro || null;
        accessibleMacro = unlocked.entry?.macro || accessibleMacro;
        if (unlocked.points_balance != null) {
          updateAuthUser({ ...getAuthUser(), points_balance: unlocked.points_balance });
        }
      }
      if (unlockedSnapshot) imported = unlockedSnapshot;
      if (!imported) {
        try {
          const saved = await api.saveLeaderboardMacro(entry.id);
          imported = saved.item;
        } catch (routeError) {
          if (!missingLeaderboardImportRoute(routeError)) throw routeError;

          // A Vite HMR client can be newer than a still-running FastAPI process.
          // Prefer account-backed legacy data before using the generic save.
          try {
            const current = await api.myMacros();
            imported = (Array.isArray(current?.items) ? current.items : []).find((item) => (
              ["created", "leaderboard"].includes(item.source_type)
              && String(item.source_ref) === String(entry.id)
            )) || null;
          } catch (_) {
            // Older servers expose the same registered/unlocked macros through
            // the dashboard instead of the account-library endpoint.
          }
          if (!imported) {
            try {
              const dashboard = await api.myDashboard();
              imported = legacyDashboardMacros(dashboard).find((item) => (
                String(item.source_ref) === String(entry.id)
              )) || null;
            } catch (_) {
              // Public rows are not present in the legacy dashboard; the
              // generic account save below covers that final compatibility case.
            }
          }
          if (!imported && accessibleMacro) {
            try {
              const fallback = await api.saveMyMacro(
                accessibleMacro,
                `${String(entry.symbol || "BTCUSDT").replace(/USDT$/, "")} 리더보드 매크로`,
              );
              imported = fallback.item;
            } catch (fallbackError) {
              if (!missingLeaderboardImportRoute(fallbackError)) throw fallbackError;
            }
          }
          if (!imported) {
            const compatibilityError = new Error("백엔드가 이전 버전이에요. 백엔드 서버를 다시 시작한 뒤 다시 눌러 주세요.");
            compatibilityError.code = "RUNNER_BACKEND_OUTDATED";
            throw compatibilityError;
          }
        }
      }
      imported = withLeaderboardPerformance(imported, entry);
      if (imported.performance) {
        performanceHintsRef.current.set(String(imported.id), imported.performance);
      }

      let nextLibrary;
      try {
        const refreshed = await api.myMacros();
        nextLibrary = (Array.isArray(refreshed?.items) ? refreshed.items : []).map((item) => (
          item.id === imported.id ? withLeaderboardPerformance(item, entry) : item
        ));
      } catch (_) {
        nextLibrary = [imported, ...library.filter((item) => item.id !== imported.id)];
      }
      const selectedItem = nextLibrary.find((item) => String(item.source_ref) === String(entry.id))
        || nextLibrary.find((item) => item.id === imported.id)
        || imported;
      setLibrary(nextLibrary);
      setSelectedId(selectedItem.id);
      setLibraryView("mine");
    } catch (error) {
      const errorText = String(error.message || error);
      setLeaderboardError(
        [400, 402].includes(error?.status) && /point|포인트/i.test(errorText)
          ? "포인트가 부족해 이 매크로를 언락하지 못했어요."
          : errorText,
      );
    } finally {
      setImportingLeaderboardId(0);
    }
  }

  async function downloadManualMacroFile() {
    if (!selected?.macro || manualDownloadBusy) return;
    setManualDownloadError("");
    setManualDownloadBusy(true);
    try {
      await api.downloadMacroFile(selected.macro);
    } catch (error) {
      setManualDownloadError(String(error.message || error));
    } finally {
      setManualDownloadBusy(false);
    }
  }

  function renderMacroScene() {
    if (!signedIn) {
      return (
        <Workspace title="내 매크로" status="로그인 필요">
          <div className="runner-wizard-login">
            <span className="runner-wizard-account-mark" aria-hidden="true">나</span>
            <h2>내 매크로를 불러오려면 먼저 로그인해요.</h2>
            <p>로그인 뒤 이 화면으로 돌아오면 내 계정에 저장된 매크로를 바로 보여드려요.</p>
          </div>
        </Workspace>
      );
    }
    if (libraryBusy) {
      return <Workspace title="내 매크로" status="불러오는 중"><div className="runner-wizard-state" role="status">계정의 매크로를 확인하고 있어요…</div></Workspace>;
    }
    if (libraryView === "leaderboard") {
      return (
        <Workspace title="리더보드에서 가져오기" status={`${leaderboardItems.length}개`} bodyClassName="is-library">
          <InlineLeaderboard
            items={leaderboardItems}
            ownedItems={library}
            selectedId={selected?.id}
            busy={leaderboardBusy}
            error={leaderboardError}
            importingId={importingLeaderboardId}
            onImport={importLeaderboardMacro}
            onBack={() => setLibraryView("mine")}
            onUpload={() => fileInputRef.current?.click()}
            uploadBusy={uploadBusy}
            uploadError={uploadError}
            fileInputRef={fileInputRef}
            onFileChange={onFileChange}
          />
        </Workspace>
      );
    }
    if (!selected) {
      return (
        <Workspace title="내 매크로" status="0개" bodyClassName="is-library">
          <EmptyLibrary
            onLeaderboard={() => setLibraryView("leaderboard")}
            onUpload={() => fileInputRef.current?.click()}
            uploadBusy={uploadBusy}
            uploadError={uploadError}
            libraryNotice={libraryError}
            fileInputRef={fileInputRef}
            onFileChange={onFileChange}
          />
        </Workspace>
      );
    }
    return (
      <Workspace title="내 매크로 연결" status={`${library.length}개`} bodyClassName="is-library">
        <MacroPicker
          items={library}
          selected={selected}
          onSelect={setSelectedId}
          onLeaderboard={() => setLibraryView("leaderboard")}
          onUpload={() => fileInputRef.current?.click()}
          uploadBusy={uploadBusy}
          uploadError={uploadError}
          fileInputRef={fileInputRef}
          onFileChange={onFileChange}
        />
      </Workspace>
    );
  }

  function renderRunnerScene() {
    const status = runnerReady
      ? "준비됨"
      : downloadStarted
        ? "첫 실행 확인"
        : runnerDownloadState === "loading"
          ? "배포 확인 중"
          : runnerDownloadState === "available"
            ? "다운로드 가능"
            : runnerDownloadState === "error"
              ? "확인 실패"
              : "배포 정보 없음";
    const mark = runnerReady ? "✓" : runnerDownloadState === "loading" ? "…" : runnerAvailable ? "↓" : "—";
    const heading = runnerReady
      ? "이 PC의 실행기가 연결 준비됐어요."
      : downloadStarted
        ? "다운로드한 실행기를 한 번 열어 주세요."
        : runnerDownloadState === "loading"
          ? "실행기 배포를 확인하고 있어요."
          : runnerDownloadState === "available"
            ? "Windows 실행기를 내려받아요."
            : runnerDownloadState === "error"
              ? "배포 상태를 확인하지 못했어요."
              : "실행기 배포 정보가 연결되지 않았어요.";

    return (
      <Workspace title="Windows 실행기" status={status}>
        <div className="runner-wizard-runner">
          <div className={`runner-wizard-runner-mark ${runnerReady ? "is-ready" : ""}`} aria-hidden="true">
            {mark}
          </div>
          <div>
            <h2>{heading}</h2>
            <p>
              {downloadStarted
                ? "최초 실행 때 웹의 ‘실행기 열기’ 주소를 등록해요. 실행기 창이 보이면 아래에서 계속해요."
                : "Windows · 설치 없이 실행 · API 키는 실행기 안에서만 사용"}
            </p>
          </div>
        </div>
        <dl className="runner-wizard-runner-details">
          <div>
            <dt>배포 상태</dt>
            <dd>
              {runnerReady
                ? "이 PC에서 한 번 실행함"
                : runnerDownloadState === "loading"
                  ? "확인 중"
                  : runnerDownloadState === "available"
                    ? "사용 가능"
                    : runnerDownloadState === "error"
                      ? "확인 실패"
                      : "다운로드 정보 없음"}
            </dd>
          </div>
          {runnerAvailable && downloadInfo?.version ? <div><dt>버전</dt><dd className="num">v{downloadInfo.version}</dd></div> : null}
          {runnerAvailable && downloadInfo?.size ? <div><dt>파일 크기</dt><dd className="num">{fmtSize(downloadInfo.size)}</dd></div> : null}
        </dl>
        {downloadStarted && !runnerReady ? (
          <div className="runner-wizard-runner-notice" role="status">
            <strong>다운로드 목록에서 파일을 허용한 뒤 한 번 열어 주세요.</strong>
            <p>브라우저가 ‘확인되지 않은 다운로드’로 막으면 GitHub의 껄무새 runner-v2 파일인지 확인한 뒤 유지·다운로드 계속을 선택해요.</p>
          </div>
        ) : !runnerReady && ["unavailable", "error"].includes(runnerDownloadState) ? (
          <div className="runner-wizard-runner-notice" role={runnerDownloadState === "error" ? "alert" : "status"}>
            <strong>{runnerDownloadState === "error" ? "서버 응답을 받지 못했어요." : "이 서버에 실행기 다운로드 주소가 아직 설정되지 않았어요."}</strong>
            <p>실행기가 이미 있다면 바로 이어가고, 없다면 계정 연결과 매크로 준비를 먼저 끝낼 수 있어요.</p>
          </div>
        ) : null}
        {!runnerReady && !downloadStarted ? (
          <div className="runner-wizard-runner-actions">
            <button type="button" onClick={() => confirmRunnerReady({ advance: true })} className="btn btn-m btn-secondary">
              이 PC에서 실행기를 이미 열었어요
            </button>
            {runnerDownloadState === "error" || runnerDownloadState === "unavailable" ? (
              <button type="button" onClick={() => void retryRunnerDownloadInfo()} className="btn btn-m btn-ghost">
                배포 상태 다시 확인
              </button>
            ) : null}
          </div>
        ) : runnerReady ? (
          <p className="runner-wizard-runner-ready-note">실행기를 한 번 열어 웹에서 바로 연결할 준비가 됐어요.</p>
        ) : null}
      </Workspace>
    );
  }

  function renderAccountScene() {
    return (
      <Workspace title="껄무새 계정" status={`${user?.username || ""} 로그인됨`}>
        <div className="runner-wizard-account-summary">
          <span className="runner-wizard-account-mark" aria-hidden="true">{(user?.username || "나").charAt(0)}</span>
          <div>
            <small>현재 웹 계정</small>
            <strong>{user?.username}</strong>
            <p>실행 현황과 원격 종료가 이 계정에 기록돼요.</p>
          </div>
          <span className="runner-wizard-connected">연결됨</span>
        </div>
        <div className="runner-wizard-auto-connect">
          <span className="runner-wizard-auto-connect-mark" aria-hidden="true">✓</span>
          <div>
            <strong>실행기를 열면 계정이 자동으로 연결돼요.</strong>
            <p>다음 화면에서 만든 일회성 연결 정보가 이 계정과 선택한 매크로를 실행기에 안전하게 전달해요.</p>
          </div>
        </div>
        <p className="runner-wizard-security-note">일회성 연결 정보에는 바이낸스 API Key와 Secret이 포함되지 않아요. 거래소 키는 내 PC의 실행기에서만 입력해요.</p>
      </Workspace>
    );
  }

  function renderLaunchScene() {
    if (["idle", "checking", "preparing"].includes(launchPhase)) {
      return (
        <Workspace title="실행기 연결 준비" status={launchPhase === "preparing" ? "티켓 만드는 중" : "지원 확인 중"}>
          <MacroSummary item={selected} compact />
          <div className="runner-wizard-launch-panel is-waiting" role="status">
            <span className="runner-wizard-launch-mark" aria-hidden="true">…</span>
            <div>
              <h2>{launchPhase === "preparing" ? "실행기에서 열 연결을 준비하고 있어요." : "빠른 실행 지원 여부를 확인하고 있어요."}</h2>
              <p>계정과 선택한 매크로만 연결하며, 실거래 주문은 보내지 않아요.</p>
            </div>
          </div>
        </Workspace>
      );
    }

    if (launchPhase === "unsupported") {
      return (
        <Workspace title="새 실행기가 필요해요" status="업데이트 필요">
          <MacroSummary item={selected} compact />
          <div className="runner-wizard-launch-panel is-warning">
            <span className="runner-wizard-launch-mark" aria-hidden="true">↓</span>
            <div>
              <h2>현재 배포된 실행기는 웹에서 바로 열기를 지원하지 않아요.</h2>
              <p>최신 실행기를 받은 뒤 다시 시도하거나, 매크로 파일을 받아 기존 방식으로 연결할 수 있어요.</p>
            </div>
          </div>
          <div className="runner-wizard-launch-actions">
            <a
              href={downloadUrl}
              download={downloadIsExternal ? undefined : true}
              target={downloadIsExternal ? "_blank" : undefined}
              rel={downloadIsExternal ? "noopener noreferrer" : undefined}
              className="btn btn-m btn-secondary"
            >
              최신 실행기 받기
            </a>
            <button type="button" onClick={() => void downloadManualMacroFile()} disabled={manualDownloadBusy} className="btn btn-m btn-ghost">
              {manualDownloadBusy ? "파일 준비 중…" : "수동으로 연결하기"}
            </button>
          </div>
          <p className="runner-wizard-launch-help">수동 연결을 선택하면 받은 .ggm.json 파일을 실행기에서 직접 열어요.</p>
          {downloadError ? <p className="runner-wizard-error" role="alert">배포 확인 응답: {downloadError}</p> : null}
          {manualDownloadError ? <p className="runner-wizard-error" role="alert">파일을 준비하지 못했어요: {manualDownloadError}</p> : null}
        </Workspace>
      );
    }

    if (launchPhase === "error") {
      return (
        <Workspace title="실행기 연결을 준비하지 못했어요" status="다시 시도 가능">
          <MacroSummary item={selected} compact />
          <div className="runner-wizard-launch-panel is-warning" role="alert">
            <span className="runner-wizard-launch-mark" aria-hidden="true">!</span>
            <div>
              <h2>자동 연결에 응답이 없어요.</h2>
              <p>{launchError || "새 연결을 만들거나 최신 실행기로 다시 시도해 주세요."}</p>
            </div>
          </div>
          <div className="runner-wizard-launch-actions">
            <button type="button" onClick={retryLaunchTicket} className="btn btn-m btn-secondary">새 연결 준비하기</button>
            <a
              href={downloadUrl}
              download={downloadIsExternal ? undefined : true}
              target={downloadIsExternal ? "_blank" : undefined}
              rel={downloadIsExternal ? "noopener noreferrer" : undefined}
              className="btn btn-m btn-ghost"
            >
              최신 실행기 받기
            </a>
            <button type="button" onClick={() => void downloadManualMacroFile()} disabled={manualDownloadBusy} className="btn btn-m btn-ghost">
              {manualDownloadBusy ? "파일 준비 중…" : "수동으로 연결하기"}
            </button>
          </div>
          {manualDownloadError ? <p className="runner-wizard-error" role="alert">파일을 준비하지 못했어요: {manualDownloadError}</p> : null}
        </Workspace>
      );
    }

    if (launchPhase === "opening") {
      return (
        <Workspace title="실행기 열기" status="응답 기다리는 중">
          <MacroSummary item={selected} compact />
          <div className="runner-wizard-launch-panel is-waiting" role="status">
            <span className="runner-wizard-launch-mark" aria-hidden="true">…</span>
            <div>
              <h2>실행기가 연결 정보를 받기를 기다리고 있어요.</h2>
              <p>브라우저에서 실행기 열기 확인 창이 보이면 허용해 주세요.</p>
            </div>
          </div>
          {showLaunchRecovery ? (
            <div className="runner-wizard-launch-recovery">
              <strong>실행기가 열리지 않았나요?</strong>
              <p>브라우저는 앱이 실제로 열렸는지 알 수 없어요. 아래 방법 중 하나로 이어가세요.</p>
              <div className="runner-wizard-launch-actions">
                <a href={launchTicket?.launch_url} onClick={beginLaunchWait} className="btn btn-m btn-secondary">다시 열기</a>
                <a
                  href={downloadUrl}
                  download={downloadIsExternal ? undefined : true}
                  target={downloadIsExternal ? "_blank" : undefined}
                  rel={downloadIsExternal ? "noopener noreferrer" : undefined}
                  className="btn btn-m btn-ghost"
                >
                  최신 실행기 받기
                </a>
                <button type="button" onClick={() => void downloadManualMacroFile()} disabled={manualDownloadBusy} className="btn btn-m btn-ghost">
                  {manualDownloadBusy ? "파일 준비 중…" : "수동으로 연결하기"}
                </button>
              </div>
            </div>
          ) : <p className="runner-wizard-launch-help">실행기 응답이 없으면 잠시 뒤 다른 연결 방법을 보여드릴게요.</p>}
          {manualDownloadError ? <p className="runner-wizard-error" role="alert">파일을 준비하지 못했어요: {manualDownloadError}</p> : null}
        </Workspace>
      );
    }

    if (launchPhase === "claimed") {
      return (
        <Workspace title="실행기가 열렸어요" status="자동 연결됨">
          <div className="runner-wizard-launch-panel is-success" role="status">
            <span className="runner-wizard-launch-mark" aria-hidden="true">✓</span>
            <div>
              <h2>계정과 매크로가 실행기에 전달됐어요.</h2>
              <p>{selected?.name} · {selected?.symbol} · 테스트넷 기본</p>
            </div>
          </div>
          <div className="runner-wizard-launch-callout">
            <span className="num">01</span>
            <div><strong>바이낸스 테스트넷 API 키를 실행기에 입력해요.</strong><p>키는 이 웹이나 껄무새 서버로 전송되지 않아요.</p></div>
          </div>
          <div className="runner-wizard-launch-callout">
            <span className="num">02</span>
            <div><strong>실행기에서 ‘매크로 시작’을 눌러요.</strong><p>첫 상태가 도착하면 아래 실행 현황이 자동으로 갱신돼요.</p></div>
          </div>
          <div className="runner-wizard-session-wrap">
            <RunnerSessions embedded showKey={false} showRunnerLink={false} title="내 실행 현황" />
          </div>
        </Workspace>
      );
    }

    return (
      <Workspace title="실행기에서 시작" status="테스트넷 기본">
        <MacroSummary item={selected} compact />
        <div className="runner-wizard-launch-panel is-ready">
          <span className="runner-wizard-launch-mark" aria-hidden="true">↗</span>
          <div>
            <h2>계정과 매크로가 준비됐어요.</h2>
            <p>아래 ‘실행기 열기’를 누르면 일회성 연결 정보가 실행기로 전달돼요.</p>
          </div>
        </div>
        <div className="runner-wizard-review">
          <div><span>실행 계정</span><strong>{user?.username}</strong></div>
          <div><span>거래 환경</span><strong>테스트넷 · 가짜 자금</strong></div>
          <div><span>API 키 위치</span><strong>내 PC의 실행기</strong></div>
        </div>
        <button type="button" onClick={() => void downloadManualMacroFile()} disabled={manualDownloadBusy} className="runner-wizard-manual-link">
          {manualDownloadBusy ? "수동 연결 파일 준비 중…" : "실행기가 열리지 않으면 수동으로 연결하기"}
        </button>
        {manualDownloadError ? <p className="runner-wizard-error" role="alert">파일을 준비하지 못했어요: {manualDownloadError}</p> : null}
      </Workspace>
    );
  }

  const scene = step === 0 ? renderMacroScene() : step === 1 ? renderRunnerScene() : step === 2 ? renderAccountScene() : renderLaunchScene();

  function renderPrimaryAction() {
    if (step === 0) {
      if (!signedIn) {
        return <Link to="/login?next=%2Frunner" className="btn btn-l btn-primary runner-wizard-next">로그인하고 내 매크로 보기</Link>;
      }
      if (libraryView === "leaderboard") return <p className="runner-wizard-footer-note">목록에서 가져올 매크로를 선택해요.</p>;
      if (!selected) return <p className="runner-wizard-footer-note">위에서 매크로를 찾거나 업로드하면 다음 단계가 열려요.</p>;
      return <button type="button" onClick={() => moveTo(1)} className="btn btn-l btn-primary runner-wizard-next">이 매크로 연결하기</button>;
    }
    if (step === 1) {
      if (runnerReady) return <button type="button" onClick={() => moveTo(2)} className="btn btn-l btn-primary runner-wizard-next">계정 연결로 계속</button>;
      if (downloadStarted) {
        return (
          <button type="button" onClick={() => confirmRunnerReady({ advance: true })} className="btn btn-l btn-primary runner-wizard-next">
            실행기를 열었어요 · 계속
          </button>
        );
      }
      if (runnerDownloadState === "loading") return <button type="button" disabled className="btn btn-l btn-primary runner-wizard-next">실행기 확인 중…</button>;
      if (!runnerAvailable) {
        return <button type="button" onClick={() => moveTo(2)} className="btn btn-l btn-primary runner-wizard-next">계정 연결 먼저 하기</button>;
      }
      return (
        <a
          href={downloadUrl}
          download={downloadIsExternal ? undefined : true}
          target={downloadIsExternal ? "_blank" : undefined}
          rel={downloadIsExternal ? "noopener noreferrer" : undefined}
          onClick={() => setDownloadStarted(true)}
          className="btn btn-l btn-primary runner-wizard-next"
        >
          Windows 실행기 내려받기
        </a>
      );
    }
    if (step === 2) {
      return <button type="button" onClick={() => moveTo(3)} className="btn btn-l btn-primary runner-wizard-next">자동 연결로 계속</button>;
    }
    if (["idle", "checking", "preparing"].includes(launchPhase)) {
      return <button type="button" disabled className="btn btn-l btn-primary runner-wizard-next">실행기 연결 준비 중…</button>;
    }
    if (launchPhase === "ready" && launchTicket?.launch_url) {
      return (
        <a href={launchTicket.launch_url} onClick={beginLaunchWait} className="btn btn-l btn-primary runner-wizard-next">
          실행기 열기
        </a>
      );
    }
    if (["unsupported", "error"].includes(launchPhase)) {
      return (
        <a
          href={downloadUrl}
          download={downloadIsExternal ? undefined : true}
          target={downloadIsExternal ? "_blank" : undefined}
          rel={downloadIsExternal ? "noopener noreferrer" : undefined}
          className="btn btn-l btn-primary runner-wizard-next"
        >
          최신 실행기 받기
        </a>
      );
    }
    if (launchPhase === "opening") {
      return <p className="runner-wizard-footer-note">실행기가 연결 정보를 받는지 확인하고 있어요.</p>;
    }
    return <p className="runner-wizard-footer-note">실행기에서 매크로 시작을 누르면 실행 현황이 자동으로 바뀌어요.</p>;
  }

  return (
    <div className="runner-wizard">
      <section className="runner-wizard-progress" aria-label="빠른 실행 진행률">
        <div>
          <span className="num">{String(step + 1).padStart(2, "0")} / 04</span>
          <strong>{CHAPTERS[step]}</strong>
        </div>
        <div className="runner-wizard-progress-track" role="progressbar" aria-valuemin="1" aria-valuemax="4" aria-valuenow={step + 1}>
          <span style={{ width: `${((step + 1) / 4) * 100}%` }} />
        </div>
        <ol aria-hidden="true">
          {CHAPTERS.map((chapter, index) => (
            <li key={chapter} className={index === step ? "is-current" : index < step ? "is-done" : ""}>{chapter}</li>
          ))}
        </ol>
      </section>

      <section key={step} className="runner-wizard-stage runner-wizard-stage-enter" aria-labelledby="runner-wizard-title">
        <div className="runner-wizard-copy">
          <p>{copy.eyebrow}</p>
          <h1 id="runner-wizard-title" ref={headingRef} tabIndex={-1}>{copy.title}</h1>
          <p>{copy.description}</p>
          {step === 0 && signedIn ? <small><strong>{user.username}</strong> 계정의 저장된 매크로를 보고 있어요.</small> : null}
        </div>
        <div className={`runner-wizard-work ${step === 0 && signedIn ? "is-library" : ""}`.trim()}>{scene}</div>
      </section>

      <footer className="runner-wizard-footer">
        <div>
          {step > 0 ? (
            <button type="button" onClick={previous} className="btn btn-l btn-ghost">이전 화면</button>
          ) : <span />}
          <div>{renderPrimaryAction()}</div>
        </div>
      </footer>
    </div>
  );
}
