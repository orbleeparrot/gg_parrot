"""Native, local-only domestic exchange setup. Instructions are not authentication."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

try:
    from . import connection, credentials
except ImportError:
    import connection
    import credentials


class ExchangeConnectionWizard:
    STEP_TITLES = ("1 · 이 PC 주소와 공식 페이지", "2 · 본인 발급과 권한", "3 · 로컬 키 입력과 실제 검사")

    def __init__(self, app, exchange: str, generation: int):
        self.app, self.exchange, self.generation = app, exchange, generation
        self.step = 0
        self.closed = False
        self.window = tk.Toplevel(app.root)
        self.window.title(f"{app._exchange_name(exchange)} 거래소 연결 도우미")
        self.window.geometry("640x650")
        self.window.minsize(560, 610)
        self.window.transient(app.root)
        self.window.protocol("WM_DELETE_WINDOW", app._close_connection_wizard)
        self.window.bind("<Escape>", lambda _event: app._close_connection_wizard())
        shell = ttk.Frame(self.window, style="Card.TFrame", padding=18)
        shell.pack(fill="both", expand=True)
        ttk.Label(shell, text=f"{app._exchange_name(exchange)} 거래소 연결 도우미", style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(shell, text="주소 조회 → 공식 페이지에서 본인 발급 → 이 PC에서 검사. 검사만으로 매매를 시작하지 않아요.", style="CardMuted.TLabel", wraplength=510).pack(anchor="w", pady=(6, 10))
        self.progress = ttk.Label(shell, text="", style="CardTitle.TLabel")
        self.progress.pack(anchor="w", pady=(0, 10))
        scroll_area = ttk.Frame(shell, style="Card.TFrame")
        scroll_area.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(scroll_area, highlightthickness=0, bg=ttk.Style(self.window).lookup("Card.TFrame", "background") or "#FFFFFF")
        scrollbar = ttk.Scrollbar(scroll_area, orient="vertical", command=self.canvas.yview)
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.body = ttk.Frame(self.canvas, style="Card.TFrame")
        body_window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(body_window, width=event.width))
        self.window.bind("<MouseWheel>", lambda event: self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units"))
        self.window.bind("<Button-4>", lambda _event: self.canvas.yview_scroll(-1, "units"))
        self.window.bind("<Button-5>", lambda _event: self.canvas.yview_scroll(1, "units"))
        self.window.bind("<Prior>", lambda _event: self.canvas.yview_scroll(-1, "pages"))
        self.window.bind("<Next>", lambda _event: self.canvas.yview_scroll(1, "pages"))
        self.frames = [ttk.Frame(self.body, style="Card.TFrame") for _ in self.STEP_TITLES]
        self._build_ip_step(self.frames[0])
        self._build_permissions_step(self.frames[1])
        self._build_key_step(self.frames[2])
        nav = ttk.Frame(shell, style="Card.TFrame")
        nav.pack(fill="x", pady=(12, 0))
        self.back_btn = ttk.Button(nav, text="이전 안내", style="Ghost.TButton", command=lambda: self.go(self.step - 1))
        self.back_btn.pack(side="left")
        self.next_btn = ttk.Button(nav, text="다음 안내", style="Primary.TButton", command=self.next)
        self.next_btn.pack(side="right")
        self.go(2 if app._connection_setup_state()["kind"] in ("saved", "existing") else 0)

    @staticmethod
    def _text(parent, text, *, title=False):
        label = ttk.Label(parent, text=text, style="CardTitle.TLabel" if title else "Card.TLabel", wraplength=510)
        label.pack(anchor="w", pady=(0, 9))
        return label

    def _build_ip_step(self, frame):
        self.choice_frame = ttk.Frame(frame, style="Card.TFrame")
        self.choice_frame.pack(fill="x", pady=(0, 12))
        self._text(self.choice_frame, "키가 있나요? 발급과 재사용을 구분하세요", title=True)
        self.new_key_btn = ttk.Button(self.choice_frame, text="새 키 발급", style="Primary.TButton", command=self._choose_new_key)
        self.new_key_btn.pack(anchor="w", pady=(0, 6))
        self.existing_key_btn = ttk.Button(self.choice_frame, text="기존 키 입력 · 발급 건너뛰기", style="Ghost.TButton", command=lambda: self.go(2))
        self.existing_key_btn.pack(anchor="w")
        self._text(frame, "현재 실행기 PC의 주소를 확인합니다", title=True)
        self._text(frame, "도우미를 열면 api4.ipify.org에 인증·거래소 키 없이 한 번 조회합니다. 최근 120초 결과와 진행 중인 요청은 재사용하고, 자동으로 반복 요청하지 않아요.")
        self.ip_status = self._text(frame, connection.public_ip_status(None))
        controls = ttk.Frame(frame, style="Card.TFrame"); controls.pack(fill="x", pady=(0, 12))
        self.ip_retry = ttk.Button(controls, text="공인 IPv4 확인", style="Ghost.TButton", command=lambda: self.app._begin_public_ip(force=True))
        self.ip_retry.pack(side="left")
        self.ip_copy = ttk.Button(controls, text="IPv4 복사", style="Ghost.TButton", command=self.app._copy_public_ip, state="disabled")
        self.ip_copy.pack(side="left", padx=(8, 0))
        self._text(frame, connection.PUBLIC_IP_CAVEAT)
        if self.exchange == "upbit":
            self._text(frame, "자동 조회는 주소를 고정하지 않습니다. 업비트가 안내하는 고정 IP 환경은 별도 준비가 필요하며, 이 도우미는 현재 주소가 고정인지 확인하지 않아요.")
        ttk.Button(frame, text="공식 API 관리 페이지 열기", style="Ghost.TButton", command=self.app._open_exchange_api_page).pack(anchor="w", pady=(4, 9))
        self._text(frame, "열기 버튼은 공식 페이지를 브라우저에 띄우는 기능이에요. IP 등록·로그인·키 발급은 그 페이지에서 본인이 직접 진행합니다.")

    def _build_permissions_step(self, frame):
        self._text(frame, "거래소에서 직접 로그인하고 키를 발급하세요", title=True)
        if self.exchange == "upbit":
            self._text(frame, "업비트: PC 공식 로그인 화면의 QR을 업비트 앱의 더보기 → QR 스캐너로 직접 스캔하고 표시 번호를 확인하세요. QR이 보이지 않으면 업비트 앱과 공식 로그인 안내를 확인하세요. 본인 인증·2채널 인증도 거래소에서 직접 진행합니다.")
        else:
            self._text(frame, "빗썸: 공식 페이지의 로그인과 본인 인증 안내를 직접 진행하세요. 이 도우미는 로그인용 QR을 만들거나 거래소 인증을 대신하지 않습니다.")
        self._text(frame, "설정할 권한", title=True)
        self._text(frame, "자산 조회 · 주문 조회 · 주문하기만 켜세요. 입출금 권한은 켜지 마세요. 출금 권한이 없어도 잘못된 주문으로 손실이 생길 수 있어요.")
        self._text(frame, "1단계에서 확인한 주소를 공식 페이지의 허용 IP에 직접 등록하세요. 실행기는 등록 여부를 읽거나 자동으로 등록하지 않습니다.")
        self._text(frame, "Access/API Key와 Secret Key는 본인만 보관하세요. Secret이 처음 한 번만 표시되면 그때 안전하게 보관하고, 채팅·게시글·웹 입력란에는 붙이지 마세요.")
        ttk.Button(frame, text="공식 API 관리 페이지 열기", style="Ghost.TButton", command=self.app._open_exchange_api_page).pack(anchor="w", pady=(4, 9))

    def _build_key_step(self, frame):
        self._text(frame, "키는 실행기 로컬 입력란에서만 사용합니다", title=True)
        self.reuse_status = self._text(frame, "")
        self.key_ip_status = self._text(frame, "")
        ip_controls = ttk.Frame(frame, style="Card.TFrame")
        ip_controls.pack(fill="x", pady=(0, 8))
        self.key_ip_retry = ttk.Button(ip_controls, text="공인 IPv4 확인", style="Ghost.TButton", command=lambda: self.app._begin_public_ip(force=True))
        self.key_ip_retry.pack(anchor="w", pady=(0, 6))
        ttk.Button(ip_controls, text="공식 허용 IP 관리 열기", style="Ghost.TButton", command=self.app._open_exchange_api_page).pack(anchor="w")
        self._text(frame, "저장키 재사용도 현재 허용 IP를 확인해야 해요. 과거 조회 주소는 등록 완료 증거가 아니며 VPN·프록시에서는 거래소 경로가 다를 수 있습니다.")
        modes = ttk.Frame(frame, style="Card.TFrame"); modes.pack(fill="x", pady=(0, 10))
        ttk.Radiobutton(modes, text="모의 · 키 없이 연습", value="mock", variable=self.app.mode, command=self.app._on_mode_change).pack(anchor="w")
        ttk.Radiobutton(modes, text="실전 연결 검사 · 실제 주문 없음", value="live", variable=self.app.mode, command=self.app._on_mode_change).pack(anchor="w", pady=(6, 0))
        self.key_frame = ttk.Frame(frame, style="Card.TFrame")
        self.key_frame.pack(fill="x")
        self.key_frame.columnconfigure(1, weight=1)
        for row, (label, variable) in enumerate(zip(("Access / API Key", "Secret Key"), self.app.key_vars[self.exchange])):
            ttk.Label(self.key_frame, text=label, style="Card.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 10), pady=5)
            ttk.Entry(self.key_frame, textvariable=variable, show="•").grid(row=row, column=1, sticky="ew", pady=5)
        self.mode_note = self._text(frame, "")
        self.check_btn = ttk.Button(frame, text="연결 검사", style="Ghost.TButton", command=self.app._begin_connection_check)
        self.check_btn.pack(anchor="w", pady=(4, 10))
        self.check_status = self._text(frame, "아직 연결 검사하지 않았어요.")
        self._text(frame, "업비트는 실제 주문을 만들지 않는 검증 API를 사용합니다. 빗썸은 읽기 조회만 확인하며 주문 권한은 확인되지 않습니다. 서명 오류는 우회하지 않으며 설정을 수정하고 다시 검사해야 합니다.")
        if credentials.supported():
            ttk.Checkbutton(frame, text=f"{self.app._exchange_name(self.exchange)} 키만 기억하기 · Windows 계정 암호화", variable=self.app.remember).pack(anchor="w", pady=(4, 8))
            self.save_btn = ttk.Button(frame, text="선택한 로컬 저장 설정 적용", style="Ghost.TButton", command=self.app._apply_wizard_storage)
            self.save_btn.pack(anchor="w", pady=(0, 8))
            self.storage_status = self._text(frame, "저장은 선택 사항이며, 이 버튼 또는 매크로 시작 때 현재 선택을 적용합니다. 도우미를 닫는 동작은 저장하지 않습니다.")
            self._text(frame, "저장·저장 해제·삭제는 선택 거래소에만 적용합니다. 다른 거래소·회원 키는 보존해요. 체크 해제 후 적용은 저장만 해제하며 현재 입력은 실행기 창에 남습니다.")
            ttk.Button(frame, text="선택 거래소 키 지우기", style="Ghost.TButton", command=self.app._forget_credentials).pack(anchor="w", pady=(0, 8))
            ttk.Button(frame, text="저장 키 다시 불러오기", style="Ghost.TButton", command=self.app._reload_credentials).pack(anchor="w", pady=(0, 8))
            self.restore_status = self._text(frame, "")
            self.recover_btn = ttk.Button(frame, text="이전 저장 파일 백업 후 새로 저장", style="Ghost.TButton", command=self.app._recover_credentials_storage)
            self.recover_btn.pack(anchor="w", pady=(0, 8))
        else:
            self.storage_status = self._text(frame, "이 환경에서는 Windows 계정 암호화 저장을 지원하지 않아요. 키는 현재 실행기에만 입력됩니다.")
        self._text(frame, "검사 결과·저장 여부는 매매 시작 승인이 아닙니다. 도우미를 닫고 메인 화면의 모드와 매크로를 확인한 뒤 직접 시작하세요.")

    def _choose_new_key(self):
        self.choice_frame.pack_forget()
        self.canvas.yview_moveto(0)

    def go(self, step):
        self.step = max(0, min(2, step))
        for frame in self.frames:
            frame.pack_forget()
        self.frames[self.step].pack(fill="both", expand=True)
        self.progress.config(text=f"{self.step + 1}/3 · {self.STEP_TITLES[self.step]}")
        self.back_btn.config(state="disabled" if self.step == 0 else "normal")
        self.next_btn.config(text="도우미 닫기 · 시작 안 함" if self.step == 2 else "다음 안내")
        self.canvas.yview_moveto(0)
        self.refresh()

    def next(self):
        if self.step == 2:
            self.app._close_connection_wizard()
        else:
            self.go(self.step + 1)

    def refresh(self):
        if self.closed:
            return
        cache = self.app._public_ip_cache
        busy = self.app._public_ip_busy
        status = "공인 IPv4 확인 중 — 이미 진행 중인 조회를 공유합니다." if busy else connection.public_ip_status(cache.result)
        self.ip_status.config(text=status)
        self.key_ip_status.config(text=status)
        self.key_ip_retry.config(state="disabled" if busy else "normal")
        self.ip_retry.config(state="disabled" if busy else "normal")
        copyable = bool(cache.result and cache.result.ok and cache.result.fresh() and self.app._public_ip)
        self.ip_copy.config(state="normal" if copyable else "disabled")
        mock = self.app._run_mode() == "mock"
        if mock:
            self.key_frame.pack_forget()
        elif not self.key_frame.winfo_manager():
            self.key_frame.pack(fill="x", before=self.mode_note)
        self.mode_note.config(text="현재 모의: 키가 필요 없고 실제 거래소 인증·주문 권한을 검사하지 않아요." if mock else "현재 실전 연결 검사: 키로 검사하지만 실제 주문이나 매크로 시작은 하지 않습니다.")
        self.check_status.config(text=self.app._connection_check_note())
        self.check_btn.config(state="disabled" if self.app._connection_busy else "normal")
        state = self.app._connection_setup_state()
        self.reuse_status.config(text=state["note"])
        if credentials.supported():
            self.storage_status.config(text=self.app._storage_note or ("저장된 선택 거래소 키와 현재 입력이 같아요. 연결 유효성은 별도로 검사합니다." if state["kind"] == "saved" else "현재 입력은 아직 저장되지 않았어요. 저장은 선택 사항이며 도우미를 닫아도 자동 저장하지 않습니다."))
        if hasattr(self, "restore_status"):
            failure = self.app._load_failure_note(self.app._credentials_load_status)
            history = getattr(self.app, "history_note", None)
            self.restore_status.config(text=failure or (str(history.cget("text")) if history is not None else "저장 키 다시 불러오기는 모든 거래소의 저장 전 변경을 확인한 뒤 복원합니다."))
            if failure:
                self.recover_btn.pack(anchor="w", pady=(0, 8), before=self.restore_status)
            else:
                self.recover_btn.pack_forget()
