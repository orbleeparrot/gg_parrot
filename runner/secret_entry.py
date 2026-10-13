"""키 입력칸 — 점(•)으로 가리고, 칸 오른쪽 끝의 눈 아이콘을 누르면 글자로 보인다(브라우저 비밀번호 칸과 같은 동작)."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

try:
    from .theme import UI
except ImportError:
    from theme import UI

MASK = "•"
STYLE = "Secret.TEntry"
FIELD_BG = "#FFFFFF"


def configure_style(style: ttk.Style, scale: float = 1.0) -> None:
    """눈 아이콘 자리만큼 오른쪽 안쪽 여백을 더 둔다 — 긴 키가 아이콘 밑으로 들어가지 않게."""
    style.configure(STYLE, padding=(6, 6, 6 + round(28 * scale), 6))


class _EyeButton(tk.Canvas):
    def __init__(self, master, entry: ttk.Entry, scale: float) -> None:
        self.size = max(18, round(22 * scale))
        super().__init__(master, width=self.size, height=self.size, bg=FIELD_BG, highlightthickness=0, bd=0,
                         cursor="hand2", takefocus=0)  # 브라우저 보기 버튼처럼 Tab 은 키 칸끼리만 오간다.
        self.entry = entry
        self.revealed = False
        self.bind("<Button-1>", lambda _event: self.toggle())
        self.bind("<Enter>", lambda _event: self.draw(hover=True))
        self.bind("<Leave>", lambda _event: self.draw())
        self.draw()

    def toggle(self) -> None:
        self.revealed = not self.revealed
        self.entry.configure(show="" if self.revealed else MASK)
        self.draw(hover=True)

    def draw(self, hover: bool = False) -> None:
        self.delete("all")
        n = self.size
        color = UI["ink"] if hover else UI["muted"]
        width = max(1.5, n / 13)
        top, bottom, mid = n * 0.18, n * 0.82, n * 0.5
        left, right = n * 0.04, n * 0.96
        # 아몬드 모양 눈 + 눈동자. 가린 상태(기본)는 사선을 그어 '보이지 않음' 을 나타낸다.
        self.create_polygon(left, mid, n * 0.3, top, n * 0.7, top, right, mid, n * 0.7, bottom, n * 0.3, bottom,
                            smooth=True, outline=color, fill="", width=width)
        r = n * 0.15
        self.create_oval(mid - r, mid - r, mid + r, mid + r, outline=color, width=width)
        if not self.revealed:
            self.create_line(n * 0.16, n * 0.84, n * 0.84, n * 0.16, fill=FIELD_BG, width=width * 3)
            self.create_line(n * 0.16, n * 0.84, n * 0.84, n * 0.16, fill=color, width=width)


def secret_entry(parent, variable: tk.Variable, *, font, scale: float = 1.0) -> ttk.Entry:
    """가린 입력칸을 만들고 눈 버튼을 칸 안 오른쪽 끝에 얹는다. 배치(grid/pack)는 부르는 쪽이 한다."""
    entry = ttk.Entry(parent, textvariable=variable, show=MASK, font=font, style=STYLE)
    eye = _EyeButton(parent, entry, scale)
    eye.place(in_=entry, relx=1.0, x=-round(8 * scale), rely=0.5, anchor="e")
    entry.eye = eye  # 테스트 · 상태 확인용
    return entry
