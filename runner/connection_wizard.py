"""거래소 연결 도우미 — 실행기 창 안에서 본문만 바뀐다(새 창을 띄우지 않는다. 창 크기 · 머리줄은 그대로).

질문 '키를 발급 받은 적 있나요?'
  · 처음이에요      → 1 허용 IP 등록 → 2 권한 선택 · 키 발급 → 3 키 복사 → 연결 확인 → [완료] → 실행기 첫 화면
  · 이미 발급 받았어요 → 거래소 API 관리를 바로 열고 기존 키 넣기 → 연결 확인 → [완료]
자동으로 하는 것: 이 PC 주소 조회 · 버튼 하나로 주소 복사 + API 관리 열기 · 거래소 [복사]를 클립보드에서 받아 칸 채우기 ·
두 칸이 차면 클립보드를 비우고 바로 검사 · 키가 들어오면 다음 단계로 · 실패 뒤 이 창으로 돌아오면 다시 검사.
사람만 할 수 있는 것(거래소 로그인 · 본인 인증 · 키 발급 · 허용 IP 저장)은 거래소 화면에서 직접 한다 — 대신할 거래소 API 가 없다.
화면에는 제목 · 값 · 상태 · 버튼만 둔다. 거래소 키는 이 PC 의 실행기 칸에만 들어가고 어디로도 보내지 않는다.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

try:
    from . import assets, connection, credentials
    from .exchange_screens import ExchangeScreen
    from .secret_entry import secret_entry
    from .theme import FONT, UI
except ImportError:
    import assets
    import connection
    import credentials
    from exchange_screens import ExchangeScreen
    from secret_entry import secret_entry
    from theme import FONT, UI

FIELD_LABELS = {"upbit": ("Access Key", "Secret Key"), "bithumb": ("API Key", "Secret Key")}
ROW_LABELS = (("auth", "키 인증"), ("ip", "허용 IP"), ("order", "주문 권한"))
WATCH_STAGES = ("perm", "keys", "existing")
CLIPBOARD_MS = 400
SPIN_MS = 60


class ExchangeConnectionWizard:
    """한 거래소의 연결 도우미. app(RunnerApp)이 만들고 닫는다 — 매매 시작 권한은 없다."""

    def __init__(self, app, exchange: str, generation: int, parent, *, stage: str = "ask"):
        self.app, self.exchange, self.generation = app, exchange, generation
        self.name = app._exchange_name(exchange)
        self.scale = float(getattr(app, "_ui_scale", 1.0) or 1.0)
        self.closed = False
        self.stage = ""
        self.path = "new" if stage in ("ip", "perm", "keys") else ""
        self.got = {"access": False, "secret": False}
        self._timers: dict[str, str] = {}
        self._advance_on_return = ""
        self._spinners: list = []
        self._clip_seen = self._clipboard_text()
        bg = UI["card"]
        self.frame = tk.Frame(parent, bg=UI["border"], padx=1, pady=1)
        self.card = tk.Frame(self.frame, bg=bg)
        self.card.pack(fill="both", expand=True)
        self._build_header()
        self.foot = tk.Frame(self.card, bg=bg)
        self.foot.pack(side="bottom", fill="x", padx=16, pady=(0, 16))
        area = tk.Frame(self.card, bg=bg)
        area.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(area, bg=bg, highlightthickness=0, height=200)
        self.scrollbar = ttk.Scrollbar(area, orient="vertical", command=self.canvas.yview)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.body = tk.Frame(self.canvas, bg=bg)
        window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda _e: (self.canvas.configure(scrollregion=self.canvas.bbox("all")), self._fit_scroll()))
        self.canvas.bind("<Configure>", lambda e: (self.canvas.itemconfigure(window, width=e.width), self._fit_scroll()))
        self.show(stage)

    # ==============================================================
    #  틀
    # ==============================================================
    def _build_header(self) -> None:
        bg = UI["card"]
        head = tk.Frame(self.card, bg=bg)
        head.pack(fill="x", padx=16, pady=16)
        # 어느 단계에서든 거래소로 바로 갈 수 있게 머리줄에 고정한다. 폭이 모자라면 제목이 줄고 버튼은 남도록 버튼부터 둔다.
        self.api_btn = ttk.Button(head, text="API 관리 열기 ↗", style="Shortcut.TButton", command=self.app._open_exchange_api_page)
        self.api_btn.pack(side="right")
        self.home_btn = ttk.Button(head, text=f"{self.name} 열기 ↗", style="Shortcut.TButton", command=self.app._open_exchange_home_page)
        self.home_btn.pack(side="right", padx=(0, 8))
        self._logo = assets.logo(self.card, self.exchange, 24 * self.scale)
        if self._logo is not None:
            tk.Label(head, image=self._logo, bg=bg).pack(side="left", padx=(0, 8))
        tk.Label(head, text=f"{self.name} 연결 도우미", bg=bg, fg=UI["ink"], font=(FONT, 11, "bold"), anchor="w").pack(
            side="left", fill="x", expand=True)
        tk.Frame(self.card, bg=UI["border"], height=1).pack(fill="x", padx=16)

    def scroll_wheel(self, event) -> None:
        if self.closed or not self.scrollbar.winfo_manager():
            return
        self.canvas.yview_scroll(-1 if getattr(event, "delta", 0) > 0 else 1, "units")

    def _fit_scroll(self) -> None:
        need = self.body.winfo_reqheight() > self.canvas.winfo_height() + 1
        if need and not self.scrollbar.winfo_manager():
            self.scrollbar.pack(side="right", fill="y", before=self.canvas)
        elif not need and self.scrollbar.winfo_manager():
            self.scrollbar.pack_forget()
            self.canvas.yview_moveto(0)

    def _later(self, name: str, delay: int, fn, *args) -> None:
        self._cancel(name)
        if self.closed:
            return
        try:
            self._timers[name] = self.frame.after(delay, fn, *args)
        except tk.TclError:
            pass

    def _cancel(self, name: str) -> None:
        timer = self._timers.pop(name, None)
        if timer is not None:
            try:
                self.frame.after_cancel(timer)
            except tk.TclError:
                pass

    def destroy(self) -> None:
        self.closed = True
        for name in list(self._timers):
            self._cancel(name)
        self._spinners = []
        try:
            self.frame.destroy()
        except tk.TclError:
            pass

    # ==============================================================
    #  화면 바꾸기
    # ==============================================================
    def show(self, stage: str) -> None:
        if self.closed:
            return
        self.stage = stage
        self._cancel("spin")
        self._spinners = []
        for child in self.body.winfo_children():
            child.destroy()
        for child in self.foot.winfo_children():
            child.destroy()
        self.ip_value = self.ip_copy = self.ip_retry = self.ip_open = None
        self.slot_rows = {}
        self.manual_check = None
        inner = tk.Frame(self.body, bg=UI["card"])
        inner.pack(fill="both", expand=True, padx=16, pady=(24, 16))
        getattr(self, f"_screen_{stage}")(inner)
        self.canvas.yview_moveto(0)
        if stage in WATCH_STAGES:
            # 단계에 들어설 때 클립보드에 있던 값은 이번에 복사한 키가 아니다 — 새로 들어오는 값만 받는다.
            if stage != "keys" or not any(self.got.values()):
                self._clip_seen = self._clipboard_text()
            self._later("clip", CLIPBOARD_MS, self._poll_clipboard)
        else:
            self._cancel("clip")
        if self._spinners:
            self._later("spin", SPIN_MS, self._spin)
        self._report()

    def refresh(self) -> None:
        """앱의 상태(IP 조회 · 연결 검사 · 키 칸)가 바뀌었을 때 지금 화면만 고친다."""
        if self.closed:
            return
        if self.stage == "ip":
            self._refresh_ip()
        elif self.stage in ("keys", "existing"):
            self._render_slots()
        elif self.stage == "manual":
            self._refresh_manual()
        elif self.stage == "checking":
            check = self.app._wizard_check()
            if check["state"] == "done":
                self.show("result_ok" if check["view"]["connected"] else "result_fail")
            elif check["state"] == "idle":
                # 검사가 시작되지 못했다(키가 비었거나 requests 가 없다) — 기다리게 두지 않는다.
                self.show("result_fail")

    def on_return(self) -> None:
        """사용자가 거래소 브라우저에서 이 창으로 돌아왔다."""
        if self.closed:
            return
        target, self._advance_on_return = self._advance_on_return, ""
        if self.stage == "result_fail":
            self._recheck()
        elif target and self.stage == "ip":
            self.show(target)

    # ==============================================================
    #  공통 조각
    # ==============================================================
    def _steps(self, parent, states) -> None:
        bar = tk.Frame(parent, bg=UI["card"])
        bar.pack(fill="x", pady=(0, 16))
        gap = max(1, round(6 * self.scale))
        for index, state in enumerate(states):
            bar.columnconfigure(index, weight=1, uniform="step")
            color = {"done": UI["ok"], "on": UI["brand"]}.get(state, UI["border"])
            tk.Frame(bar, bg=color, height=max(2, round(4 * self.scale))).grid(
                row=0, column=index, sticky="ew", padx=(0 if index == 0 else gap, 0))

    def _flow_steps(self, current: str) -> tuple:
        """처음 발급은 3칸(허용 IP · 권한·발급 · 키 복사), 기존 키는 2칸(기존 키 넣기 · 확인)."""
        if self.path == "existing":
            return {"existing": ("on", ""), "manual": ("on", ""), "checking": ("done", "on"),
                    "result_fail": ("done", "on"), "result_ok": ("done", "done")}[current]
        return {"ip": ("on", "", ""), "perm": ("done", "on", ""), "keys": ("done", "done", "on"),
                "manual": ("done", "done", "on"), "checking": ("done", "done", "on"),
                "result_fail": ("done", "done", "on"), "result_ok": ("done", "done", "done")}[current]

    def _title(self, parent, text: str, *, icon: str = "") -> None:
        row = tk.Frame(parent, bg=UI["card"])
        row.pack(fill="x", pady=(0, 16))
        if icon:
            self._icon(row, icon, size=24).pack(side="left", padx=(0, 10))
        ttk.Label(row, text=text, style="CardHeading.TLabel").pack(side="left")

    def _icon(self, parent, kind: str, *, size: int = 20) -> tk.Canvas:
        px = max(12, round(size * self.scale))
        canvas = tk.Canvas(parent, width=px, height=px, bg=UI["card"], highlightthickness=0, bd=0)
        k = px / 24
        if kind == "ok":
            canvas.create_oval(0, 0, px, px, fill=UI["ok"], outline="")
            canvas.create_line(6.5 * k, 12.5 * k, 10.5 * k, 16.5 * k, 17.5 * k, 8 * k, fill="#FFFFFF", width=max(2, 2.6 * k),
                               capstyle="round", joinstyle="round")
        elif kind == "bad":
            canvas.create_oval(0, 0, px, px, fill=UI["danger"], outline="")
            for a, b, c, d in ((8, 8, 16, 16), (16, 8, 8, 16)):
                canvas.create_line(a * k, b * k, c * k, d * k, fill="#FFFFFF", width=max(2, 2.6 * k), capstyle="round")
        elif kind == "na":
            canvas.create_oval(0, 0, px, px, fill=UI["na"], outline="")
            canvas.create_line(7.5 * k, 12 * k, 16.5 * k, 12 * k, fill="#FFFFFF", width=max(2, 2.6 * k), capstyle="round")
        elif kind == "wait":
            canvas.create_oval(2 * k, 2 * k, px - 2 * k, px - 2 * k, outline=UI["wait"], width=max(1.5, 2 * k), dash=(3, 2))
        elif kind == "spin":
            canvas.create_oval(2 * k, 2 * k, px - 2 * k, px - 2 * k, outline=UI["border"], width=max(2, 3 * k))
            arc = canvas.create_arc(2 * k, 2 * k, px - 2 * k, px - 2 * k, start=90, extent=100, style="arc",
                                    outline=UI["brand"], width=max(2, 3 * k))
            self._spinners.append((canvas, arc))
        return canvas

    def _spin(self) -> None:
        alive = []
        for canvas, arc in self._spinners:
            try:
                start = float(canvas.itemcget(arc, "start"))
                canvas.itemconfigure(arc, start=(start - 24) % 360)
                alive.append((canvas, arc))
            except tk.TclError:
                pass
        self._spinners = alive
        if alive:
            self._later("spin", SPIN_MS, self._spin)

    def _list_row(self, parent, icon: str, label: str, *, right: str = "", right_color: str = "", bold_right=False,
                  last=False):
        row = tk.Frame(parent, bg=UI["card"], height=max(32, round(40 * self.scale)))
        row.pack(fill="x")
        row.pack_propagate(False)
        icon_widget = self._icon(row, icon)
        icon_widget.pack(side="left", padx=(0, 12))
        text = ttk.Label(row, text=label, style="Card.TLabel")
        text.pack(side="left")
        value = tk.Label(row, text=right, bg=UI["card"], fg=right_color or UI["muted"],
                         font=(FONT, 10, "bold") if bold_right else (FONT, 10))
        value.pack(side="right")
        if not last:
            tk.Frame(parent, bg=UI["line"], height=1).pack(fill="x")
        return row, icon_widget, value

    def _demo(self, parent, kind: str) -> ExchangeScreen:
        small = assets.logo(parent, self.exchange, 18 * self.scale)
        screen = ExchangeScreen(parent, self.exchange, kind, scale=self.scale, ip_text=self._ip_text, logo=small)
        screen._logo_ref = small  # PhotoImage 는 참조가 끊기면 지워진다
        screen.pack(fill="x")
        return screen

    def _ip_text(self) -> str:
        return str(getattr(self.app, "_public_ip", "") or "")

    def _button(self, parent, text, command, *, primary=False, state="normal"):
        return ttk.Button(parent, text=text, command=command, state=state,
                          style="Primary.TButton" if primary else "Ghost.TButton")

    def _foot(self, left=None, right=None) -> None:
        """아래 버튼 줄 — 모든 단계에서 같은 자리. (글자, 동작, 노랑?, 켜짐?)"""
        if left:
            text, command = left
            self._button(self.foot, text, command).pack(side="left")
        if right:
            text, command, primary, enabled = right
            button = self._button(self.foot, text, command, primary=primary, state="normal" if enabled else "disabled")
            button.pack(side="right")
            return button
        return None

    def _back(self, stage: str):
        return ("이전", lambda: self.show(stage))

    # ==============================================================
    #  화면들
    # ==============================================================
    def _screen_ask(self, body) -> None:
        self.path = ""
        ttk.Label(body, text=f"{self.name} API 키를 발급 받은 적 있나요?", style="CardHeading.TLabel").pack(anchor="w", pady=(8, 24))
        self._option(body, "처음이에요", "허용 IP 등록부터 차례로", self._choose_new)
        self._option(body, "이미 발급 받았어요", "보관해 둔 키를 넣고 바로 검사", self._choose_existing, top=12)
        self._foot(left=("돌아가기", self.app._close_connection_wizard))

    def _option(self, parent, title: str, sub: str, command, *, top: int = 0) -> None:
        outer = tk.Frame(parent, bg=UI["border"], padx=1, pady=1, cursor="hand2", takefocus=1,
                         highlightthickness=0)
        outer.pack(fill="x", pady=(top, 0))
        inner = tk.Frame(outer, bg=UI["card"], padx=20, pady=16, cursor="hand2")
        inner.pack(fill="x")
        chevron = tk.Label(inner, text="›", bg=UI["card"], fg=UI["muted"], font=(FONT, 15), cursor="hand2")
        chevron.pack(side="right")
        head = tk.Label(inner, text=title, bg=UI["card"], fg=UI["ink"], font=(FONT, 11, "bold"), cursor="hand2")
        head.pack(anchor="w")
        tail = tk.Label(inner, text=sub, bg=UI["card"], fg=UI["muted"], font=(FONT, 10), cursor="hand2")
        tail.pack(anchor="w", pady=(4, 0))
        widgets = (outer, inner, chevron, head, tail)
        for widget in widgets:
            widget.bind("<Button-1>", lambda _e: command())
            widget.bind("<Enter>", lambda _e: outer.configure(bg=UI["brand"]))
            widget.bind("<Leave>", lambda _e: outer.configure(bg=UI["brand"] if outer.focus_get() is outer else UI["border"]))
        outer.bind("<FocusIn>", lambda _e: outer.configure(bg=UI["brand"]))
        outer.bind("<FocusOut>", lambda _e: outer.configure(bg=UI["border"]))
        outer.bind("<Return>", lambda _e: command())
        outer.bind("<space>", lambda _e: command())

    def _choose_new(self) -> None:
        self.path = "new"
        self.show("ip")

    def _choose_existing(self) -> None:
        self.path = "existing"
        self.show("existing")
        # 이미 발급한 키는 거래소 API 관리에 있다 — 바로 연다.
        self.app._open_exchange_api_page()

    # --- 1 · 허용 IP ---------------------------------------------------
    def _screen_ip(self, body) -> None:
        self.path = "new"
        self._steps(body, self._flow_steps("ip"))
        self._title(body, "허용 IP 등록")
        self._demo(body, "ip")
        row = tk.Frame(body, bg=UI["card"])
        row.pack(fill="x", pady=(16, 0))
        left = tk.Frame(row, bg=UI["card"])
        left.pack(side="left", fill="x", expand=True)
        ttk.Label(left, text="이 PC 주소", style="CardMuted.TLabel").pack(anchor="w")
        self.ip_value = ttk.Label(left, text="", style="CardHeading.TLabel")
        self.ip_value.pack(anchor="w", pady=(2, 0))
        self.ip_copy = self._button(row, "복사", self._copy_ip)
        self.ip_retry = self._button(row, "다시 확인", lambda: self.app._begin_public_ip(force=True))
        self.ip_open = self._button(body, "복사하고 API 관리 열기 ↗", self._copy_and_open, primary=True)
        self.ip_open.pack(fill="x", pady=(16, 0), ipady=max(2, round(4 * self.scale)))
        self._foot(left=("이전", lambda: self.show("ask")), right=("다음", lambda: self.show("perm"), False, True))
        self._refresh_ip()
        # 들어설 때 주소가 없거나 만료됐으면 바로 다시 본다 — 이 단계는 주소를 쓰는 단계다.
        cache = self.app._public_ip_cache
        if not self.app._public_ip_busy and not (cache.result and cache.result.fresh()):
            self.app._begin_public_ip(force=False)

    def _refresh_ip(self) -> None:
        if self.ip_value is None:
            return
        cache = self.app._public_ip_cache
        result = cache.result
        busy = bool(self.app._public_ip_busy)
        address = self.app._public_ip if (result and result.ok and result.fresh()) else ""
        if busy:
            self.ip_value.config(text="확인 중", foreground=UI["muted"])
        elif address:
            self.ip_value.config(text=address, foreground=UI["ink"])
        elif result is not None and result.ok and not result.fresh():
            # 120초가 지난 주소는 보이지 않는다 — 이 단계에 있으면 저절로 다시 본다.
            self.ip_value.config(text="확인 중", foreground=UI["muted"])
            self._later("ip_refresh", 50, self.app._begin_public_ip, False)
        else:
            self.ip_value.config(text="확인 실패" if result is not None else "확인 중",
                                 foreground=UI["danger"] if result is not None else UI["muted"])
        failed = result is not None and not result.ok and not busy
        if failed:
            self.ip_copy.pack_forget()
            if not self.ip_retry.winfo_manager():
                self.ip_retry.pack(side="right")
        else:
            self.ip_retry.pack_forget()
            if not self.ip_copy.winfo_manager():
                self.ip_copy.pack(side="right")
        self.ip_copy.config(state="normal" if address else "disabled")
        self.ip_open.config(state="normal" if address else "disabled")

    def _copy_ip(self) -> None:
        if self.app._copy_public_ip() and self.ip_copy is not None:
            self.ip_copy.config(text="복사됨")
            self._later("copied", 1500, lambda: self.ip_copy is not None and self.ip_copy.winfo_exists() and self.ip_copy.config(text="복사"))

    def _copy_and_open(self) -> None:
        if self.app._copy_public_ip():
            self.app._open_exchange_api_page()
            # 거래소에서 주소를 등록하고 돌아오면 다음 단계로 넘긴다.
            self._advance_on_return = "perm"

    # --- 2 · 권한 · 발급 ----------------------------------------------
    def _screen_perm(self, body) -> None:
        self.path = "new"
        self._steps(body, self._flow_steps("perm"))
        self._title(body, "권한 선택 · 키 발급")
        self._demo(body, "perm")
        self._foot(left=self._back("ip"), right=("다음", lambda: self.show("keys"), False, True))

    # --- 3 · 키 복사 / 기존 키 ---------------------------------------
    def _screen_keys(self, body) -> None:
        self.path = "new"
        self._steps(body, self._flow_steps("keys"))
        self._title(body, "키 복사")
        self._demo(body, "keys")
        self._slots(body)
        self._foot(left=self._back("perm"), right=("직접 입력", lambda: self.show("manual"), False, True))

    def _screen_existing(self, body) -> None:
        self.path = "existing"
        self._steps(body, self._flow_steps("existing"))
        self._title(body, "기존 키 넣기")
        self._demo(body, "existing")
        self._slots(body)
        link = tk.Label(body, text="Secret Key가 없어요 · 새로 발급", bg=UI["card"], fg="#4B5563",
                        font=(FONT, 10, "bold", "underline"), cursor="hand2")
        link.pack(anchor="w", pady=(16, 0))
        link.bind("<Button-1>", lambda _e: self._choose_new())
        self._foot(left=("이전", lambda: self.show("ask")), right=("직접 입력", lambda: self.show("manual"), False, True))

    def _slots(self, body) -> None:
        holder = tk.Frame(body, bg=UI["card"])
        holder.pack(fill="x", pady=(16, 0))
        labels = FIELD_LABELS.get(self.exchange, ("API Key", "Secret Key"))
        for index, (slot, label) in enumerate(zip(("access", "secret"), labels)):
            row, icon, value = self._list_row(holder, "wait", label, last=index == 1)
            self.slot_rows[slot] = (row, icon, value)
        self._render_slots()

    def _render_slots(self) -> None:
        for slot, (row, icon, value) in list(self.slot_rows.items()):
            try:
                got = self.got.get(slot)
                value.config(text="받음" if got else "복사 대기", fg="#15803D" if got else UI["muted"])
                icon.delete("all")
                fresh = self._icon(row, "ok" if got else "wait")
                fresh.pack(side="left", padx=(0, 12), before=icon)
                icon.destroy()
                self.slot_rows[slot] = (row, fresh, value)
            except tk.TclError:
                pass

    # --- 직접 입력 ------------------------------------------------------
    def _screen_manual(self, body) -> None:
        self._steps(body, self._flow_steps("manual"))
        self._title(body, "직접 입력")
        grid = tk.Frame(body, bg=UI["card"])
        grid.pack(fill="x")
        grid.columnconfigure(0, minsize=round(112 * self.scale))
        grid.columnconfigure(1, weight=1)
        labels = FIELD_LABELS.get(self.exchange, ("API Key", "Secret Key"))
        self.manual_entries = []
        for index, (label, variable) in enumerate(zip(labels, self.app.key_vars[self.exchange])):
            ttk.Label(grid, text=label, style="Card.TLabel").grid(row=index, column=0, sticky="w", pady=(0, 8))
            entry = secret_entry(grid, variable, font=(FONT, 10), scale=self.scale)
            entry.grid(row=index, column=1, sticky="ew", pady=(0, 8))
            self.manual_entries.append(entry)
        back = "existing" if self.path == "existing" else "keys"
        self.manual_check = self._foot(left=self._back(back), right=("검사", self._start_check, True, False))
        self._refresh_manual()
        if self.manual_entries:
            self.manual_entries[0].focus_set()

    def _refresh_manual(self) -> None:
        if self.manual_check is None:
            return
        access, secret = (variable.get().strip() for variable in self.app.key_vars[self.exchange])
        self.manual_check.config(state="normal" if access and secret else "disabled")

    # --- 확인 · 결과 ------------------------------------------------------
    def _screen_checking(self, body) -> None:
        self._steps(body, self._flow_steps("checking"))
        self._title(body, "연결 확인 중", icon="spin")
        for index, (_key, label) in enumerate(ROW_LABELS):
            self._list_row(body, "spin", label, last=index == len(ROW_LABELS) - 1)
        self._foot(left=self._back(self._key_stage()), right=("완료", lambda: None, True, False))

    def _key_stage(self) -> str:
        return "existing" if self.path == "existing" else "keys"

    def _rows(self, body, view: dict) -> None:
        holder = tk.Frame(body, bg=UI["card"])
        holder.pack(fill="x")
        ip = self._ip_text()
        for index, (key, label) in enumerate(ROW_LABELS):
            state = view.get(key, "wait")
            icon = {"ok": "ok", "bad": "bad", "na": "na"}.get(state, "na")
            right, color, bold = "", "", False
            if key == "ip" and state == "ok" and ip:
                right, color, bold = ip, UI["ink"], True
            elif state == "bad":
                right, color = {"auth": "키가 맞지 않음", "ip": "등록된 IP와 다름", "order": "주문하기 권한 없음"}[key], UI["danger"]
            elif state == "na":
                right = "확인 불가"
            elif state == "wait":
                right = "대기"
            self._list_row(holder, icon, label, right=right, right_color=color, bold_right=bold,
                           last=index == len(ROW_LABELS) - 1)

    def _screen_result_ok(self, body) -> None:
        check = self.app._wizard_check()
        view = check.get("view") or {}
        self._steps(body, self._flow_steps("result_ok"))
        self._title(body, "연결 완료", icon="ok")
        self._rows(body, view)
        if view.get("problem") == "order_size" and check.get("reason"):
            ttk.Label(body, text=check["reason"], style="CardMuted.TLabel", wraplength=round(560 * self.scale),
                      justify="left").pack(anchor="w", pady=(12, 0))
        if credentials.supported():
            ttk.Checkbutton(body, text="이 PC에 키 기억하기 (Windows 계정으로 암호화)", variable=self.app.remember,
                            command=self.app._on_remember_toggled).pack(anchor="w", pady=(16, 0))
        self._foot(left=self._back(self._key_stage()), right=("완료", self.app._close_connection_wizard, True, True))

    def _screen_result_fail(self, body) -> None:
        check = self.app._wizard_check()
        view = check.get("view") or connection.check_view(self.exchange, False, "")
        problem = view.get("problem", "other")
        self._steps(body, self._flow_steps("result_fail"))
        self._title(body, view.get("headline") or "연결 확인 필요", icon="bad")
        self._rows(body, view)
        acts = tk.Frame(body, bg=UI["card"])
        if problem == "ip":
            tk.Frame(body, bg=UI["card"], height=16).pack(fill="x")
            self._demo(body, "fix")
            self._button(acts, "복사하고 API 관리 열기 ↗", self._copy_and_open_fix, primary=True).pack(side="left")
        elif problem == "auth":
            self._button(acts, "키 다시 복사", self._recopy, primary=True).pack(side="left")
        elif problem == "order":
            self._button(acts, "API 관리 열기 ↗", self.app._open_exchange_api_page, primary=True).pack(side="left")
        else:
            reason = check.get("reason") or ""
            if reason:
                ttk.Label(body, text=reason, style="CardMuted.TLabel", wraplength=round(560 * self.scale),
                          justify="left").pack(anchor="w", pady=(12, 0))
        self._button(acts, "다시 검사", self._recheck).pack(side="left", padx=(8, 0) if acts.winfo_children() else 0)
        acts.pack(anchor="w", pady=(16, 0))
        self._foot(left=self._back(self._key_stage()), right=("완료", lambda: None, True, False))

    def _copy_and_open_fix(self) -> None:
        if self.app._copy_public_ip():
            self.app._open_exchange_api_page()
        else:
            self.app._open_exchange_api_page()

    def _recopy(self) -> None:
        self.got = {"access": False, "secret": False}
        self.show(self._key_stage())

    def _recheck(self) -> None:
        self.show("checking")
        if not self.app._begin_key_check(force=True):
            self.refresh()

    def _start_check(self) -> None:
        self.show("checking")
        if not self.app._begin_key_check(force=True):
            self.refresh()

    # ==============================================================
    #  클립보드로 키 받기
    # ==============================================================
    def _clipboard_text(self) -> str:
        try:
            return str(self.app.root.clipboard_get())
        except (tk.TclError, AttributeError, TypeError):
            return ""

    def _poll_clipboard(self) -> None:
        self._timers.pop("clip", None)
        if self.closed or self.stage not in WATCH_STAGES:
            return
        text = self._clipboard_text()
        if text != self._clip_seen:
            self._clip_seen = text
            key = connection.clipboard_key(text)
            if key:
                self.accept_key(key)
        if not self.closed and self.stage in WATCH_STAGES:
            self._later("clip", CLIPBOARD_MS, self._poll_clipboard)

    def accept_key(self, key: str) -> None:
        """거래소 발급 창에서 복사한 키 하나. 들어온 순서대로 Access → Secret 칸에 넣는다."""
        access, secret = self.app.key_vars[self.exchange]
        if self.got["access"] and self.got["secret"]:
            # 다 받은 뒤 또 들어오면 새로 발급한 키로 보고 처음부터 받는다.
            self.got = {"access": False, "secret": False}
        if not self.got["access"]:
            secret.set("")  # 옛 시크릿과 새 Access 가 짝지어 검사되지 않게 먼저 비운다
            access.set(key)
            self.got["access"] = True
        else:
            if key == access.get().strip():
                return
            secret.set(key)
            self.got["secret"] = True
        if self.stage == "perm":
            self.show("keys")
        else:
            self._render_slots()
            self._report()
        if self.got["access"] and self.got["secret"]:
            # 시크릿이 클립보드에 남지 않게 비운다(Windows 클립보드 기록은 실행기가 지우지 못한다).
            try:
                self.app.root.clipboard_clear()
            except tk.TclError:
                pass
            self._clip_seen = ""
            self._start_check()

    # ==============================================================
    #  웹 가이드에 지금 단계 알리기(키 · 주소는 보내지 않는다)
    # ==============================================================
    def _report(self) -> None:
        view = None
        if self.stage in ("result_ok", "result_fail"):
            view = self.app._wizard_check().get("view")
        self.app._report_setup_progress(self.stage, path=self.path, slots=dict(self.got), view=view)
