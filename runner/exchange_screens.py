"""연결 도우미의 거래소 화면 재현 — 업비트 · 빗썸 Open API 관리 화면을 단순화해 그리고 커서가 실제 위치를 누른다.

근거: docs.upbit.com/kr/docs/api-key · 업비트 실제 화면(사용자 캡처 2026-10-12) · 빗썸 API 2.0 실제 화면(사용자 캡처 2026-10-12).
그림일 뿐이다 — 여기서 무엇을 눌러도 거래소에는 아무것도 가지 않고, 실행기의 키 칸도 바뀌지 않는다.
좌표는 96 DPI 기준 픽셀로 적고 Painter 가 화면 배율을 곱한다(글꼴은 포인트라 Tk 가 알아서 키운다).
"""
from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont

FONT = "맑은 고딕"
INK = "#111827"
MUTED = "#6B7280"
PLACEHOLDER = "#A6ABB5"

UP_NAVY = "#093687"
UP_BLUE = "#0062DF"
UP_CHECK = "#1261C4"
UP_LINE = "#D5D6DC"
BT_ORANGE = "#FE6420"
BT_DARK = "#1A1D24"
BT_LINE = "#DCDDE3"
DIM = "#ADB0B5"  # 거래소 창 위에 뜬 작은 창 뒤를 어둡게 — 캔버스는 반투명이 없어 단색으로 덮는다

UP_KEYS = ("a8Kq3Zt0" + "*" * 20, "Wm2xP7Lb" + "*" * 20)
BT_KEYS = ("6b2c78fd" + "*" * 20, "ZDkyNGRk" + "*" * 20)


class Painter:
    """96 DPI 픽셀 좌표로 그리고 배율을 곱한다. 글자 폭은 배율로 나눠 같은 좌표계로 돌려준다."""

    def __init__(self, canvas: tk.Canvas, scale: float):
        self.c = canvas
        self.s = scale
        self.regular = tkfont.Font(root=canvas, family=FONT, size=10)
        self.bold = tkfont.Font(root=canvas, family=FONT, size=10, weight="bold")
        self.mark = tkfont.Font(root=canvas, family=FONT, size=10, weight="bold", slant="italic")

    def p(self, value: float) -> float:
        return value * self.s

    def width(self, text: str, font=None) -> float:
        return (font or self.regular).measure(text) / self.s

    def fit(self, text: str, limit: float, font=None) -> str:
        font = font or self.regular
        if self.width(text, font) <= limit:
            return text
        while text and self.width(text + "…", font) > limit:
            text = text[:-1]
        return text + "…"

    def rect(self, x0, y0, x1, y1, *, fill="", outline="", width=1, tags=()):
        return self.c.create_rectangle(self.p(x0), self.p(y0), self.p(x1), self.p(y1), fill=fill,
                                       outline=outline, width=width if outline else 0, tags=tags)

    def round_rect(self, x0, y0, x1, y1, r, *, fill="", outline="", width=1, tags=()):
        x0, y0, x1, y1, r = (self.p(v) for v in (x0, y0, x1, y1, r))
        points = (x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
                  x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0)
        return self.c.create_polygon(points, smooth=True, fill=fill, outline=outline,
                                     width=width if outline else 0, tags=tags)

    def oval(self, x0, y0, x1, y1, *, fill="", outline="", width=1, tags=()):
        return self.c.create_oval(self.p(x0), self.p(y0), self.p(x1), self.p(y1), fill=fill,
                                  outline=outline, width=width if outline else 0, tags=tags)

    def line(self, *coords, fill, width=1, tags=()):
        return self.c.create_line(*(self.p(v) for v in coords), fill=fill, width=width, tags=tags)

    def text(self, x, y, text, *, font=None, fill=INK, anchor="w", tags=()):
        return self.c.create_text(self.p(x), self.p(y), text=text, font=font or self.regular, fill=fill,
                                  anchor=anchor, tags=tags)

    def check(self, x0, y0, size, *, fill, width=2.0, tags=()):
        """✓ 한 획. 24 칸 그림(M5 12.5 l4.5 4.5 L19 7.5)을 size 칸으로 줄인다."""
        k = size / 24
        return self.line(x0 + 5 * k, y0 + 12.5 * k, x0 + 9.5 * k, y0 + 17 * k, x0 + 19 * k, y0 + 7.5 * k,
                         fill=fill, width=max(1.5, width * self.s), tags=tags)


class Scene:
    """한 장면 = 그린 그림 + 누를 대상 위치 + 켜고 끄는 방법 + 움직임 순서."""

    def __init__(self, canvas: tk.Canvas, scale: float, width: float, *, ip_text=lambda: ""):
        self.canvas = canvas
        self.painter = Painter(canvas, scale)
        self.width = width
        self.height = 0.0
        self.targets: dict[str, tuple[float, float]] = {}
        self.toggles: dict[str, tuple] = {}
        self.inputs: dict[str, dict] = {}
        self.layers: list[str] = []
        self.sequence: list[tuple] = []
        self.ip_text = ip_text

    # --- 조각 -----------------------------------------------------
    def target(self, name, x, y):
        self.targets[name] = (x, y)

    def toggle(self, name, on, off):
        self.toggles.setdefault(name, [])
        self.toggles[name].append((on, off))

    def items(self, tag, **options):
        for item in self.canvas.find_withtag(tag):
            try:
                self.canvas.itemconfigure(item, **options)
            except tk.TclError:
                pass

    def input_box(self, name, x0, y0, x1, y1, *, placeholder, line, focus, radius=0, tags=()):
        p = self.painter
        box = (p.round_rect(x0, y0, x1, y1, radius, fill="#FFFFFF", outline=line, tags=tags)
               if radius else p.rect(x0, y0, x1, y1, fill="#FFFFFF", outline=line, tags=tags))
        cy = (y0 + y1) / 2
        hint = p.text(x0 + 10, cy, p.fit(placeholder, x1 - x0 - 20), fill=PLACEHOLDER, tags=tags)
        typed = p.text(x0 + 10, cy, "", font=p.bold, fill=INK, tags=tags)
        self.inputs[name] = {"box": box, "hint": hint, "typed": typed, "line": line, "focus": focus}
        # 커서는 칸의 오른쪽을 누른다 — 왼쪽부터 찍히는 주소 글자를 가리지 않게.
        self.target(name, min(x1 - 24, x0 + max(150, (x1 - x0) * .6)), cy)

    # --- 상태 -----------------------------------------------------
    def set_on(self, name, on):
        for on_fn, off_fn in self.toggles.get(name, ()):
            (on_fn if on else off_fn)()

    def show_layer(self, name, visible):
        self.items(name, state="normal" if visible else "hidden")

    def focus_input(self, name, focused):
        field = self.inputs[name]
        self.canvas.itemconfigure(field["box"], outline=field["focus"] if focused else field["line"])

    def type_into(self, name, text):
        field = self.inputs[name]
        self.canvas.itemconfigure(field["typed"], text=text)
        self.canvas.itemconfigure(field["hint"], state="hidden" if text else "normal")

    def reset(self):
        for name in self.toggles:
            self.set_on(name, False)
        for name in self.inputs:
            self.focus_input(name, False)
            self.type_into(name, "")
        for name in self.layers:
            self.show_layer(name, False)


# ==================================================================
#  업비트 — 남색 머리줄 · 흰 바탕 · 네모 체크(파란 체크) · 파란 버튼
# ==================================================================
def _up_chrome(scene: Scene, title="Open API 관리"):
    p = scene.painter
    p.rect(0, 0, scene.width, 32, fill=UP_NAVY)
    p.text(12, 16, "UPbit", font=p.mark, fill="#FFFFFF")
    p.text(12 + p.width("UPbit", p.mark) + 12, 16, title, font=p.bold, fill="#FFFFFF")


def _up_box(scene: Scene, name, x, y, label, *, size=16, line="#C4C8CF", width=1, fill="#FFFFFF"):
    p = scene.painter
    box = p.rect(x, y, x + size, y + size, fill=fill, outline=line, width=width)
    mark = p.check(x + size * .08, y + size * .08, size * .84, fill=UP_CHECK, width=2.2)
    scene.canvas.itemconfigure(mark, state="hidden")
    if label:
        p.text(x + size + 6, y + size / 2, label, fill="#333333")
    if name:
        scene.target(name, x + size / 2, y + size / 2)
        scene.toggle(name, lambda: (scene.canvas.itemconfigure(box, outline=UP_CHECK),
                                    scene.canvas.itemconfigure(mark, state="normal")),
                     lambda: (scene.canvas.itemconfigure(box, outline=line),
                              scene.canvas.itemconfigure(mark, state="hidden")))


def _up_button(scene: Scene, name, x0, y0, x1, y1, label, *, off=UP_LINE, on=UP_BLUE):
    p = scene.painter
    body = p.rect(x0, y0, x1, y1, fill=off)
    p.text((x0 + x1) / 2, (y0 + y1) / 2, label, font=p.bold, fill="#FFFFFF", anchor="center")
    if name:
        scene.target(name, (x0 + x1) / 2, (y0 + y1) / 2)
        scene.toggle(name, lambda: scene.canvas.itemconfigure(body, fill=on),
                     lambda: scene.canvas.itemconfigure(body, fill=off))


def _up_label_width(scene: Scene, *labels) -> float:
    return max([72.0] + [scene.painter.width(label) + 6 for label in labels])


def upbit_ip(scene: Scene):
    p, w = scene.painter, scene.width
    _up_chrome(scene)
    y = 44
    lw = _up_label_width(scene, "IP 주소 등록")
    p.text(14, y + 17, "IP 주소 등록", fill="#333333")
    scene.input_box("in", 14 + lw + 12, y, w - 14, y + 34, placeholder='IP 주소 입력(","로 구분하여 최대 10개)',
                    line=UP_LINE, focus=UP_BLUE)
    scene.height = y + 34 + 12
    scene.sequence = [("move", "in"), ("click",), ("focus", "in"), ("type", "in"), ("wait", 1600)]


def upbit_perm(scene: Scene):
    p, w = scene.painter, scene.width
    _up_chrome(scene)
    y = 44
    lw = _up_label_width(scene, "권한 선택")
    p.text(14, y + 8, "권한 선택", fill="#333333")
    gx = 14 + lw + 12
    col = (w - 14 - gx) / 4
    names = (("포켓관리", ""), ("자산조회", "t1"), ("주문조회", "t2"), ("주문하기", "t3"),
             ("출금조회", ""), ("출금하기", ""), ("입금조회", ""), ("입금하기", ""))
    for index, (label, name) in enumerate(names):
        _up_box(scene, name, gx + (index % 4) * col, y + (index // 4) * 26, label)
    y += 2 * 26 + 6
    _up_box(scene, "t4", 14, y, "개인(신용)정보 수집 및 이용 동의(필수)")
    y += 16 + 12
    p.rect(14, y, 14 + 220, y + 44, fill="#F9F9F9", outline="#D3D3D3")
    _up_box(scene, "t5", 26, y + 12, "", size=20, line="#C1C1C1", width=2)
    p.text(26 + 20 + 10, y + 22, "로봇이 아닙니다.", fill="#333333")
    y += 44 + 12
    label = "Open API Key 발급받기"
    _up_button(scene, "t6", 14, y, 14 + p.width(label, p.bold) + 36, y + 40, label)
    scene.height = y + 40 + 12
    scene.sequence = [("move", "t1"), ("click",), ("on", "t1"), ("move", "t2"), ("click",), ("on", "t2"),
                      ("move", "t3"), ("click",), ("on", "t3"), ("move", "t4"), ("click",), ("on", "t4"),
                      ("move", "t5"), ("click",), ("on", "t5"), ("on", "t6"), ("move", "t6"), ("click",),
                      ("wait", 1400)]


def _modal(scene: Scene, x0, y0, x1, y1, *, radius=0):
    p = scene.painter
    if radius:
        p.round_rect(x0 + 2, y0 + 3, x1 + 2, y1 + 3, radius, fill="#E9EBEF")
        return p.round_rect(x0, y0, x1, y1, radius, fill="#FFFFFF", outline="#E5E7EB")
    p.rect(x0 + 2, y0 + 3, x1 + 2, y1 + 3, fill="#E9EBEF")
    return p.rect(x0, y0, x1, y1, fill="#FFFFFF", outline="#E5E7EB")


def _copy_button(scene: Scene, name, x1, y, *, line, ink, on_fill, radius=0, height=26):
    p = scene.painter
    width = p.width("복사") + 20
    x0 = x1 - width
    body = (p.round_rect(x0, y, x1, y + height, radius, fill="#FFFFFF", outline=line) if radius
            else p.rect(x0, y, x1, y + height, fill="#FFFFFF", outline=line))
    text = p.text((x0 + x1) / 2, y + height / 2, "복사", fill=ink, anchor="center")
    scene.target(name, (x0 + x1) / 2, y + height / 2)
    scene.toggle(name, lambda: (scene.canvas.itemconfigure(body, fill=on_fill, outline=on_fill),
                                scene.canvas.itemconfigure(text, fill="#FFFFFF")),
                 lambda: (scene.canvas.itemconfigure(body, fill="#FFFFFF", outline=line),
                          scene.canvas.itemconfigure(text, fill=ink)))
    return x0


def upbit_keys(scene: Scene):
    p, w = scene.painter, scene.width
    _up_chrome(scene)
    x0, x1, y0 = 12, w - 12, 44
    # 높이를 먼저 셈한다: 안쪽 14 · 제목 20 · 10 · 키 칸(10+26+8+26+10) · 10 · 확인 34 · 14
    y1 = y0 + 14 + 20 + 10 + 80 + 10 + 34 + 14
    _modal(scene, x0, y0, x1, y1)
    p.text(x0 + 14, y0 + 14 + 10, "Open API Key 발급 완료", font=p.bold)
    bx0, bx1, by0 = x0 + 14, x1 - 14, y0 + 14 + 20 + 10
    p.rect(bx0, by0, bx1, by0 + 80, fill="#F2F7FD")
    for index, (label, value, name) in enumerate((("Access Key", UP_KEYS[0], "c1"), ("Secret Key", UP_KEYS[1], "c2"))):
        ry = by0 + 10 + index * 34
        p.text(bx0 + 12, ry + 13, label, font=p.bold, fill=UP_BLUE)
        left = _copy_button(scene, name, bx1 - 12, ry, line=UP_BLUE, ink=UP_BLUE, on_fill=UP_BLUE)
        vx = bx0 + 12 + 80 + 10
        p.text(vx, ry + 13, p.fit(value, left - 10 - vx), fill=MUTED)
    ok_w = p.width("확인", p.bold) + 36
    _up_button(scene, "", x1 - 14 - ok_w, by0 + 80 + 10, x1 - 14, by0 + 80 + 10 + 34, "확인", off=UP_BLUE)
    scene.height = y1 + 12
    scene.sequence = [("move", "c1"), ("click",), ("on", "c1"), ("wait", 900), ("move", "c2"), ("click",),
                      ("on", "c2"), ("wait", 1800)]


def upbit_fix(scene: Scene):
    p, w = scene.painter, scene.width
    _up_chrome(scene)
    y = 44
    p.rect(14, y, w - 14, y + 44, fill="#FFFFFF", outline="#E5E7EB")
    _up_box(scene, "t1", 24, y + 14, "")
    p.text(24 + 16 + 10, y + 22, "a8Kq3Zt0" + "*" * 12, fill=MUTED)
    bw = p.width("변경", p.bold) + 24
    body = p.rect(w - 24 - bw, y + 7, w - 24, y + 37, fill=UP_NAVY)
    p.text(w - 24 - bw / 2, y + 22, "변경", font=p.bold, fill="#FFFFFF", anchor="center")
    scene.target("t2", w - 24 - bw / 2, y + 22)
    p.text(w - 24 - bw - 14, y + 22, "● ● ●", fill="#1B3A8C", anchor="e")
    _ = body
    # 허용 IP주소 변경 창 — 'ovl' 층 전체를 켜고 끈다.
    tags = ("ovl",)
    dx0, dx1 = w * .07, w * .93
    dy0 = 32 + 22
    dy1 = dy0 + 14 + 20 + 10 + 34 + 12 + 34 + 14
    p.rect(0, 32, w, max(236, dy1 + 22), fill=DIM, tags=tags)
    p.rect(dx0, dy0, dx1, dy1, fill="#FFFFFF", tags=tags)
    p.text(dx0 + 14, dy0 + 14 + 10, "허용 IP주소 변경", font=p.bold, tags=tags)
    scene.input_box("in", dx0 + 14, dy0 + 44, dx1 - 14, dy0 + 78, placeholder='","로 구분하여 여러개의 IP주소 등록 가능',
                    line=UP_LINE, focus=UP_BLUE, tags=tags)
    ok_w = p.width("확인", p.bold) + 28
    cancel_w = p.width("취소") + 28
    by = dy0 + 78 + 12
    p.rect(dx1 - 14 - ok_w - 6 - cancel_w, by, dx1 - 14 - ok_w - 6, by + 34, fill="#FFFFFF", outline=UP_BLUE, tags=tags)
    p.text(dx1 - 14 - ok_w - 6 - cancel_w / 2, by + 17, "취소", fill=UP_BLUE, anchor="center", tags=tags)
    p.rect(dx1 - 14 - ok_w, by, dx1 - 14, by + 34, fill=UP_BLUE, tags=tags)
    p.text(dx1 - 14 - ok_w / 2, by + 17, "확인", font=p.bold, fill="#FFFFFF", anchor="center", tags=tags)
    scene.target("t5", dx1 - 14 - ok_w / 2, by + 17)
    scene.layers = ["ovl"]
    scene.height = max(236, dy1 + 22)
    scene.sequence = [("move", "t1"), ("click",), ("on", "t1"), ("move", "t2"), ("click",), ("show", "ovl"),
                      ("move", "in"), ("click",), ("focus", "in"), ("type", "in"), ("move", "t5"), ("click",),
                      ("hide", "ovl"), ("wait", 1400)]


def upbit_existing(scene: Scene):
    p, w = scene.painter, scene.width
    _up_chrome(scene)
    y = 44
    p.rect(14, y, w - 14, y + 44, fill="#FFFFFF", outline="#E5E7EB")
    p.text(24, y + 22, "a8Kq3Zt0" + "*" * 12, fill=MUTED)
    bw = p.width("보기") + 20
    p.rect(w - 24 - bw, y + 8, w - 24, y + 36, fill="#FFFFFF", outline=UP_BLUE)
    p.text(w - 24 - bw / 2, y + 22, "보기", fill=UP_BLUE, anchor="center")
    p.text(w - 24 - bw - 12, y + 22, "2027.07.01 만료", fill=MUTED, anchor="e")
    scene.height = y + 44 + 12


# ==================================================================
#  빗썸 API 2.0 — 흰 카드 · 주황 원형 체크 · 검정 버튼
# ==================================================================
def _bt_chrome(scene: Scene, *, tabs=True, logo=None) -> float:
    p, w = scene.painter, scene.width
    p.line(0, 32, w, 32, fill="#EEEEEE")
    x = 12
    if logo is not None:
        scene.canvas.create_image(p.p(x), p.p(16), image=logo, anchor="w")
        x += 18 + 8
    p.text(x, 16, "API 관리", font=p.bold)
    if not tabs:
        return 32 + 12
    first = p.width("API 2.0", p.bold)
    p.text(14, 46, "API 2.0", font=p.bold, fill="#111111")
    p.line(14, 59, 14 + first, 59, fill="#111111", width=2 * p.s)
    p.text(14 + first + 18, 46, "API 1.0", fill="#B5B8BF")
    p.line(0, 60, w, 60, fill="#EEEEEE")
    return 60 + 12


def _bt_circle(scene: Scene, name, x, y, label, *, font=None, ink="#444444"):
    p = scene.painter
    ring = p.oval(x, y, x + 20, y + 20, fill="#FFFFFF", outline="#DCDCE0", width=1.5)
    mark = p.check(x + 4, y + 4, 12, fill="#CFD0D5", width=2.2)
    text = p.text(x + 20 + 6, y + 10, label, font=font, fill=ink)
    if name:
        scene.target(name, x + 10, y + 10)
        scene.toggle(name, lambda: (scene.canvas.itemconfigure(ring, fill=BT_ORANGE, outline=BT_ORANGE),
                                    scene.canvas.itemconfigure(mark, fill="#FFFFFF"),
                                    scene.canvas.itemconfigure(text, fill="#111111")),
                     lambda: (scene.canvas.itemconfigure(ring, fill="#FFFFFF", outline="#DCDCE0"),
                              scene.canvas.itemconfigure(mark, fill="#CFD0D5"),
                              scene.canvas.itemconfigure(text, fill=ink)))


def _bt_button(scene: Scene, name, x0, y0, x1, y1, label, *, off="#C9CBD1", on=BT_DARK):
    p = scene.painter
    body = p.round_rect(x0, y0, x1, y1, 4, fill=off)
    p.text((x0 + x1) / 2, (y0 + y1) / 2, label, font=p.bold, fill="#FFFFFF", anchor="center")
    if name:
        scene.target(name, (x0 + x1) / 2, (y0 + y1) / 2)
        scene.toggle(name, lambda: scene.canvas.itemconfigure(body, fill=on),
                     lambda: scene.canvas.itemconfigure(body, fill=off))


def _bt_ip_row(scene: Scene, y, x0, x1, *, tags=()):
    p = scene.painter
    scene.input_box("in", x0, y, x1 - 36 - 6, y + 36, placeholder="IP 주소를 입력해 주세요. 예) 123.123.123.123",
                    line=BT_LINE, focus=BT_DARK, radius=3, tags=tags)
    p.round_rect(x1 - 36, y, x1, y + 36, 3, fill="#FFFFFF", outline=BT_LINE, tags=tags)
    p.text(x1 - 18, y + 18, "+", fill="#333333", anchor="center", tags=tags)


def bithumb_ip(scene: Scene, logo=None):
    p, w = scene.painter, scene.width
    y = _bt_chrome(scene, logo=logo)
    p.text(14, y + 8, "IP 주소 등록 (필수)", font=p.bold)
    p.text(14 + p.width("IP 주소 등록 (필수)", p.bold) + 6, y + 8, "(최대 5개 입력 가능)", fill="#8B8F98")
    y += 16 + 10
    _bt_ip_row(scene, y, 14, w - 14)
    scene.height = y + 36 + 12
    scene.sequence = [("move", "in"), ("click",), ("focus", "in"), ("type", "in"), ("wait", 1600)]


def bithumb_perm(scene: Scene, logo=None):
    p, w = scene.painter, scene.width
    y = _bt_chrome(scene, logo=logo)
    p.text(14, y + 8, "API 활성 항목 선택 (필수)", font=p.bold)
    y += 16 + 12
    col = (w - 28) / 4
    names = (("자산조회", "t1"), ("주문조회", "t2"), ("주문하기", "t3"), ("출금조회", ""),
             ("출금하기", ""), ("입금조회", ""), ("입금하기", ""))
    for index, (label, name) in enumerate(names):
        _bt_circle(scene, name, 14 + (index % 4) * col, y + (index // 4) * 28, label)
    y += 2 * 28 + 8
    rows = (("모두 동의", p.bold, "#111111"), ("개인정보 수집 및 이용 동의 (필수)", None, "#6B6F78"),
            ("API 이용 주의사항 확인 및 동의 (필수)", None, "#6B6F78"))
    card_h = 10 + len(rows) * 28 + 4 + 40 + 12
    p.round_rect(14, y, w - 14, y + card_h, 8, fill="#FFFFFF", outline="#E8E9ED")
    for index, (label, font, ink) in enumerate(rows):
        _bt_circle(scene, "t4", 26, y + 10 + index * 28, label, font=font, ink=ink)
    button_y = y + 10 + len(rows) * 28 + 4
    _bt_button(scene, "t6", 26, button_y, w - 26, button_y + 40, "API Key 생성")
    scene.targets["t4"] = (26 + 10, y + 10 + 10)
    scene.height = y + card_h + 12
    scene.sequence = [("move", "t1"), ("click",), ("on", "t1"), ("move", "t2"), ("click",), ("on", "t2"),
                      ("move", "t3"), ("click",), ("on", "t3"), ("move", "t4"), ("click",), ("on", "t4"),
                      ("on", "t6"), ("move", "t6"), ("click",), ("wait", 1400)]


def bithumb_keys(scene: Scene, logo=None):
    p, w = scene.painter, scene.width
    y0 = _bt_chrome(scene, tabs=False, logo=logo)
    x0, x1 = 12, w - 12
    y1 = y0 + 14 + 20 + 10 + 80 + 10 + 40 + 14
    _modal(scene, x0, y0, x1, y1, radius=8)
    p.text(x0 + 14, y0 + 14 + 10, "API Key가 발급되었습니다.", font=p.bold)
    bx0, bx1, by0 = x0 + 14, x1 - 14, y0 + 14 + 20 + 10
    p.round_rect(bx0, by0, bx1, by0 + 80, 4, fill="#F5F5FA")
    for index, (label, value, name) in enumerate((("API Key", BT_KEYS[0], "c1"), ("Secret key", BT_KEYS[1], "c2"))):
        ry = by0 + 10 + index * 34
        p.text(bx0 + 12, ry + 13, label, font=p.bold, fill="#111111")
        left = _copy_button(scene, name, bx1 - 12, ry, line=BT_LINE, ink="#111111", on_fill=BT_DARK, radius=4)
        vx = bx0 + 12 + 80 + 10
        p.text(vx, ry + 13, p.fit(value, left - 10 - vx), fill=MUTED)
    _bt_button(scene, "", bx0, by0 + 80 + 10, bx1, by0 + 80 + 10 + 40, "확인", off=BT_DARK)
    scene.height = y1 + 12
    scene.sequence = [("move", "c1"), ("click",), ("on", "c1"), ("wait", 900), ("move", "c2"), ("click",),
                      ("on", "c2"), ("wait", 1800)]


def _bt_history(scene: Scene, y, *, tail="", ghost="") -> float:
    p, w = scene.painter, scene.width
    p.text(14, y + 8, "API Key 발급 내역", font=p.bold)
    y += 16 + 10
    p.line(14, y, w - 14, y, fill="#EEEEEE")
    p.line(14, y + 44, w - 14, y + 44, fill="#EEEEEE")
    p.text(24, y + 22, "xECsmJSx" + "*" * 12, fill=MUTED)
    right = w - 24
    if ghost:
        bw = p.width(ghost) + 24
        p.round_rect(right - bw, y + 7, right, y + 37, 4, fill="#FFFFFF", outline=BT_LINE)
        p.text(right - bw / 2, y + 22, ghost, fill="#111111", anchor="center")
        scene.target("t2", right - bw / 2, y + 22)
        right -= bw + 14
    if tail:
        p.text(right, y + 22, tail, fill="#111111" if tail.startswith("●") else MUTED, anchor="e")
    return y + 44


def bithumb_fix(scene: Scene, logo=None):
    p, w = scene.painter, scene.width
    y = _bt_chrome(scene, tabs=False, logo=logo)
    _bt_history(scene, y, tail="● ● ●", ghost="변경")
    tags = ("ovl",)
    dx0, dx1 = w * .06, w * .94
    dy0 = 32 + 18
    dy1 = dy0 + 14 + 20 + 6 + 18 + 10 + 36 + 12 + 40 + 14
    p.rect(0, 32, w, max(236, dy1 + 18), fill=DIM, tags=tags)
    p.round_rect(dx0, dy0, dx1, dy1, 8, fill="#FFFFFF", tags=tags)
    p.text(dx0 + 14, dy0 + 14 + 10, "허용 IP주소 변경", font=p.bold, tags=tags)
    p.text(dx0 + 14, dy0 + 14 + 20 + 6 + 9, "IP주소 입력 (최대 5개 입력 가능)", fill="#8B8F98", tags=tags)
    row_y = dy0 + 14 + 20 + 6 + 18 + 10
    _bt_ip_row(scene, row_y, dx0 + 14, dx1 - 14, tags=tags)
    by = row_y + 36 + 12
    body = p.round_rect(dx0 + 14, by, dx1 - 14, by + 40, 4, fill=BT_DARK, tags=tags)
    p.text((dx0 + dx1) / 2, by + 20, "변경", font=p.bold, fill="#FFFFFF", anchor="center", tags=tags)
    _ = body
    scene.target("t5", (dx0 + dx1) / 2, by + 20)
    scene.layers = ["ovl"]
    scene.height = max(236, dy1 + 18)
    scene.sequence = [("move", "t2"), ("click",), ("show", "ovl"), ("move", "in"), ("click",), ("focus", "in"),
                      ("type", "in"), ("move", "t5"), ("click",), ("hide", "ovl"), ("wait", 1400)]


def bithumb_existing(scene: Scene, logo=None):
    y = _bt_chrome(scene, tabs=False, logo=logo)
    y = _bt_history(scene, y, tail="2027-07-09 만료")
    scene.height = y + 12


SCENES = {
    "upbit": {"ip": upbit_ip, "perm": upbit_perm, "keys": upbit_keys, "fix": upbit_fix, "existing": upbit_existing},
    "bithumb": {"ip": bithumb_ip, "perm": bithumb_perm, "keys": bithumb_keys, "fix": bithumb_fix,
                "existing": bithumb_existing},
}


# ==================================================================
#  캔버스 + 커서 움직임
# ==================================================================
class ExchangeScreen(tk.Canvas):
    """거래소 화면 한 장. 폭이 바뀌면 다시 그리고, 보이는 동안만 커서 순서를 되풀이한다."""

    MOVE_MS = 520
    STEP_MS = 16
    LOOP_PAUSE_MS = 900

    def __init__(self, master, exchange: str, kind: str, *, scale: float = 1.0, ip_text=lambda: "",
                 logo=None, animate=True):
        super().__init__(master, highlightthickness=1, highlightbackground="#E5E7EB", bd=0,
                         background="#FFFFFF", height=1)
        self.exchange, self.kind, self.scale = exchange, kind, scale
        self.ip_text, self.logo, self.animate = ip_text, logo, animate
        self.scene = None
        self._drawn_width = 0
        self._after = None
        self._cursor_xy = (0.0, 0.0)
        self._running = False
        self.bind("<Configure>", self._on_configure)
        self.bind("<Map>", lambda _event: self.start())
        self.bind("<Unmap>", lambda _event: self.stop())
        self.bind("<Destroy>", lambda _event: self.stop(), add="+")

    # --- 그리기 ---------------------------------------------------
    def _on_configure(self, event):
        width = event.width / self.scale
        if abs(width - self._drawn_width) >= 1:
            self.redraw(width)

    def redraw(self, width: float) -> None:
        self.stop()
        self.delete("all")
        self._drawn_width = width
        scene = Scene(self, self.scale, width, ip_text=self.ip_text)
        draw = SCENES[self.exchange][self.kind]
        if self.exchange == "bithumb":
            draw(scene, logo=self.logo)
        else:
            draw(scene)
        self.scene = scene
        scene.reset()
        self._draw_cursor()
        self.configure(height=int(scene.height * self.scale))
        if self.winfo_ismapped():
            self.start()

    def _draw_cursor(self):
        # 24 칸 화살표(M5 3 l14 8 -6 2 -3 6z)를 18px 로. 끝점(5,3)이 누르는 자리다.
        self.create_polygon(0, 0, 0, 0, 0, 0, 0, 0, fill=INK, outline="#FFFFFF", width=1.5, tags=("cursor",))
        self._place_cursor(self.scene.width * .7, self.scene.height * .8, pressed=False)
        self.itemconfigure("cursor", state="hidden")

    def _place_cursor(self, x, y, *, pressed):
        k = (18 / 24) * (.82 if pressed else 1.0)
        shape = ((5, 3), (19, 11), (13, 13), (10, 19))
        points = []
        for px, py in shape:
            points.extend(((x + (px - 5) * k) * self.scale, (y + (py - 3) * k) * self.scale))
        self.coords("cursor", *points)
        self.tag_raise("cursor")
        self._cursor_xy = (x, y)

    # --- 움직임 ---------------------------------------------------
    def start(self) -> None:
        if not self.animate or self._running or self.scene is None or not self.scene.sequence:
            return
        self._running = True
        self.scene.reset()
        self.itemconfigure("cursor", state="hidden")
        self._schedule(400, self._step, 0)

    def stop(self) -> None:
        self._running = False
        if self._after is not None:
            try:
                self.after_cancel(self._after)
            except tk.TclError:
                pass
            self._after = None

    def _schedule(self, delay, fn, *args):
        if not self._running:
            return
        try:
            self._after = self.after(int(delay), fn, *args)
        except tk.TclError:
            self._running = False

    def _step(self, index: int) -> None:
        self._after = None
        if not self._running:
            return
        scene = self.scene
        if index >= len(scene.sequence):
            self._schedule(self.LOOP_PAUSE_MS, self._restart)
            return
        action = scene.sequence[index]
        kind = action[0]
        if kind == "move":
            self.itemconfigure("cursor", state="normal")
            self._glide(index, scene.targets[action[1]], 0, self._cursor_xy)
            return
        if kind == "click":
            self._place_cursor(*self._cursor_xy, pressed=True)
            self._schedule(120, self._release, index)
            return
        if kind == "on":
            scene.set_on(action[1], True)
            self._schedule(200, self._step, index + 1)
        elif kind == "off":
            scene.set_on(action[1], False)
            self._schedule(200, self._step, index + 1)
        elif kind == "show":
            scene.show_layer(action[1], True)
            self.tag_raise("cursor")
            self._schedule(260, self._step, index + 1)
        elif kind == "hide":
            scene.show_layer(action[1], False)
            self._schedule(200, self._step, index + 1)
        elif kind == "focus":
            scene.focus_input(action[1], True)
            self._schedule(160, self._step, index + 1)
        elif kind == "type":
            self._type(index, action[1], self._ip_value(), 0)
        elif kind == "wait":
            self._schedule(action[1], self._step, index + 1)
        else:
            self._schedule(0, self._step, index + 1)

    def _ip_value(self) -> str:
        try:
            return str(self.ip_text() or "")
        except Exception:
            return ""

    def _glide(self, index, target, frame, origin):
        frames = max(1, self.MOVE_MS // self.STEP_MS)
        t = min(1.0, frame / frames)
        eased = t * t * (3 - 2 * t)
        x = origin[0] + (target[0] - origin[0]) * eased
        y = origin[1] + (target[1] - origin[1]) * eased
        self._place_cursor(x, y, pressed=False)
        if t >= 1.0:
            self._schedule(80, self._step, index + 1)
        else:
            self._schedule(self.STEP_MS, self._glide, index, target, frame + 1, origin)

    def _release(self, index):
        self._place_cursor(*self._cursor_xy, pressed=False)
        self._schedule(140, self._step, index + 1)

    def _type(self, index, name, text, count):
        self.scene.type_into(name, text[:count])
        if count >= len(text):
            self._schedule(260, self._step, index + 1)
        else:
            self._schedule(55, self._type, index, name, text, count + 1)

    def _restart(self):
        self._after = None
        if not self._running:
            return
        self.scene.reset()
        self.itemconfigure("cursor", state="hidden")
        self._place_cursor(self.scene.width * .7, self.scene.height * .8, pressed=False)
        self._schedule(300, self._step, 0)
