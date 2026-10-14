"""키 입력칸 — 점(•)으로 가리고, 칸 안 오른쪽의 눈 아이콘을 누르면 글자로 보인다(브라우저 비밀번호 칸과 같은 동작).

가려져 있으면 '보기'(눈), 보이고 있으면 '숨기기'(빗금 눈). 아이콘은 웹과 같은 Lucide 그림(icons.py)이다.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

try:
    from . import icons
except ImportError:
    import icons

MASK = "•"
STYLE = "Secret.TEntry"
FIELD_BG = "#FFFFFF"
ICON_PX = 18


def configure_style(style: ttk.Style, scale: float = 1.0) -> None:
    """눈 아이콘 자리만큼 오른쪽 안쪽 여백을 더 둔다 — 긴 키가 아이콘 밑으로 들어가지 않게."""
    style.configure(STYLE, padding=(6, 6, 6 + round((ICON_PX + 18) * scale), 6))


class _EyeButton(tk.Label):
    def __init__(self, master, entry: ttk.Entry, scale: float) -> None:
        super().__init__(master, bg=FIELD_BG, bd=0, cursor="hand2", takefocus=0)  # Tab 은 키 칸끼리만 오간다
        size = ICON_PX * scale
        self.images = {(name, tone): icons.icon(self, name, tone, size)
                       for name in ("eye", "eyeoff") for tone in ("muted", "ink")}
        self.entry = entry
        self.revealed = False
        self.hover = False
        self.bind("<Button-1>", lambda _event: self.toggle())
        self.bind("<Enter>", lambda _event: self.draw(hover=True))
        self.bind("<Leave>", lambda _event: self.draw(hover=False))
        self.draw()

    def toggle(self) -> None:
        self.revealed = not self.revealed
        self.entry.configure(show="" if self.revealed else MASK)
        self.draw()

    def draw(self, hover: bool | None = None) -> None:
        if hover is not None:
            self.hover = hover
        name = "eyeoff" if self.revealed else "eye"
        self.configure(image=self.images[(name, "ink" if self.hover else "muted")])


def secret_entry(parent, variable: tk.Variable, *, font, scale: float = 1.0) -> ttk.Entry:
    """가린 입력칸을 만들고 눈 아이콘을 칸 안 오른쪽에 얹는다. 배치(grid/pack)는 부르는 쪽이 한다."""
    entry = ttk.Entry(parent, textvariable=variable, show=MASK, font=font, style=STYLE)
    eye = _EyeButton(parent, entry, scale)
    eye.place(in_=entry, relx=1.0, x=-round(10 * scale), rely=0.5, anchor="e")
    entry.eye = eye  # 테스트 · 상태 확인용
    return entry
