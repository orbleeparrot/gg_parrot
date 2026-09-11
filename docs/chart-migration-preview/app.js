(() => {
  // TradingView Lightweight Charts 5.2.1 (bundled for this preview)
  var t = { title: "", visible: true, hitTestTolerance: 3, lastValueVisible: true, priceLineVisible: true, priceLineSource: 0, priceLineWidth: 1, priceLineColor: "", priceLineStyle: 2, baseLineVisible: true, baseLineWidth: 1, baseLineColor: "#B2B5BE", baseLineStyle: 0, priceFormat: { type: "price", precision: 2, minMove: 0.01 } };
  var i;
  var n;
  function s(t2, i2) {
    const n2 = (function(t3, i3) {
      switch (t3) {
        case 0:
        default:
          return [];
        case 1:
          return [i3, i3];
        case 2:
          return [2 * i3, 2 * i3];
        case 3:
          return [6 * i3, 6 * i3];
        case 4:
          return [i3, 4 * i3];
      }
    })(i2, t2.lineWidth);
    return t2.setLineDash(n2), n2;
  }
  function e(t2, i2, n2, s2) {
    t2.beginPath();
    const e2 = t2.lineWidth % 2 ? 0.5 : 0;
    t2.moveTo(n2, i2 + e2), t2.lineTo(s2, i2 + e2), t2.stroke();
  }
  function r(t2, i2) {
    if (!t2) throw new Error("Assertion failed" + (i2 ? ": " + i2 : ""));
  }
  function h(t2) {
    if (void 0 === t2) throw new Error("Value is undefined");
    return t2;
  }
  function a(t2) {
    if (null === t2) throw new Error("Value is null");
    return t2;
  }
  function l(t2) {
    return a(h(t2));
  }
  !(function(t2) {
    t2[t2.Simple = 0] = "Simple", t2[t2.WithSteps = 1] = "WithSteps", t2[t2.Curved = 2] = "Curved";
  })(i || (i = {})), (function(t2) {
    t2[t2.Solid = 0] = "Solid", t2[t2.Dotted = 1] = "Dotted", t2[t2.Dashed = 2] = "Dashed", t2[t2.LargeDashed = 3] = "LargeDashed", t2[t2.SparseDotted = 4] = "SparseDotted";
  })(n || (n = {}));
  var o = class {
    constructor() {
      this.t = [];
    }
    i(t2, i2, n2) {
      const s2 = { h: t2, l: i2, o: true === n2 };
      this.t.push(s2);
    }
    _(t2) {
      const i2 = this.t.findIndex(((i3) => t2 === i3.h));
      i2 > -1 && this.t.splice(i2, 1);
    }
    u(t2) {
      this.t = this.t.filter(((i2) => i2.l !== t2));
    }
    p(t2, i2, n2) {
      const s2 = [...this.t];
      this.t = this.t.filter(((t3) => !t3.o)), s2.forEach(((s3) => s3.h(t2, i2, n2)));
    }
    v() {
      return this.t.length > 0;
    }
    m() {
      this.t = [];
    }
  };
  function _(t2, ...i2) {
    for (const n2 of i2) for (const i3 in n2) void 0 !== n2[i3] && Object.prototype.hasOwnProperty.call(n2, i3) && !["__proto__", "constructor", "prototype"].includes(i3) && ("object" != typeof n2[i3] || void 0 === t2[i3] || Array.isArray(n2[i3]) ? t2[i3] = n2[i3] : _(t2[i3], n2[i3]));
    return t2;
  }
  function u(t2) {
    return "number" == typeof t2 && isFinite(t2);
  }
  function c(t2) {
    return "number" == typeof t2 && t2 % 1 == 0;
  }
  function d(t2) {
    return "string" == typeof t2;
  }
  function f(t2) {
    return "boolean" == typeof t2;
  }
  function p(t2) {
    const i2 = t2;
    if (!i2 || "object" != typeof i2) return i2;
    let n2, s2, e2;
    for (s2 in n2 = Array.isArray(i2) ? [] : {}, i2) i2.hasOwnProperty(s2) && (e2 = i2[s2], n2[s2] = e2 && "object" == typeof e2 ? p(e2) : e2);
    return n2;
  }
  function v(t2) {
    return null !== t2;
  }
  function m(t2) {
    return null === t2 ? void 0 : t2;
  }
  var w = "-apple-system, BlinkMacSystemFont, 'Trebuchet MS', Roboto, Ubuntu, sans-serif";
  function g(t2, i2, n2) {
    return void 0 === i2 && (i2 = w), `${n2 = void 0 !== n2 ? `${n2} ` : ""}${t2}px ${i2}`;
  }
  var M = class {
    constructor(t2) {
      this.M = { S: 1, C: 5, P: NaN, k: "", T: "", R: "", D: "", I: 0, V: 0, B: 0, A: 0, L: 0 }, this.O = t2;
    }
    N() {
      const t2 = this.M, i2 = this.F(), n2 = this.W();
      return t2.P === i2 && t2.T === n2 || (t2.P = i2, t2.T = n2, t2.k = g(i2, n2), t2.A = 2.5 / 12 * i2, t2.I = t2.A, t2.V = i2 / 12 * t2.C, t2.B = i2 / 12 * t2.C, t2.L = 0), t2.R = this.H(), t2.D = this.U(), this.M;
    }
    H() {
      return this.O.N().layout.textColor;
    }
    U() {
      return this.O.$();
    }
    F() {
      return this.O.N().layout.fontSize;
    }
    W() {
      return this.O.N().layout.fontFamily;
    }
  };
  function b(t2) {
    return t2 < 0 ? 0 : t2 > 255 ? 255 : Math.round(t2) || 0;
  }
  function S(t2) {
    return 0.199 * t2[0] + 0.687 * t2[1] + 0.114 * t2[2];
  }
  var x = class {
    constructor(t2, i2) {
      this.j = /* @__PURE__ */ new Map(), this.q = t2, i2 && (this.j = i2);
    }
    Y(t2, i2) {
      if ("transparent" === t2) return t2;
      const n2 = this.K(t2), s2 = n2[3];
      return `rgba(${n2[0]}, ${n2[1]}, ${n2[2]}, ${i2 * s2})`;
    }
    G(t2) {
      const i2 = this.K(t2);
      return { Z: `rgb(${i2[0]}, ${i2[1]}, ${i2[2]})`, X: S(i2) > 160 ? "black" : "white" };
    }
    J(t2) {
      return S(this.K(t2));
    }
    tt(t2, i2, n2) {
      const [s2, e2, r2, h2] = this.K(t2), [a2, l2, o2, _2] = this.K(i2), u2 = [b(s2 + n2 * (a2 - s2)), b(e2 + n2 * (l2 - e2)), b(r2 + n2 * (o2 - r2)), (c2 = h2 + n2 * (_2 - h2), c2 <= 0 || c2 > 1 ? Math.min(Math.max(c2, 0), 1) : Math.round(1e4 * c2) / 1e4)];
      var c2;
      return `rgba(${u2[0]}, ${u2[1]}, ${u2[2]}, ${u2[3]})`;
    }
    K(t2) {
      const i2 = this.j.get(t2);
      if (i2) return i2;
      const n2 = (function(t3) {
        const i3 = document.createElement("div");
        i3.style.display = "none", document.body.appendChild(i3), i3.style.color = t3;
        const n3 = window.getComputedStyle(i3).color;
        return document.body.removeChild(i3), n3;
      })(t2), s2 = n2.match(/^rgba?\s*\((\d+),\s*(\d+),\s*(\d+)(?:,\s*(\d*\.?\d+))?\)$/);
      if (!s2) {
        if (this.q.length) for (const i3 of this.q) {
          const n3 = i3(t2);
          if (n3) return this.j.set(t2, n3), n3;
        }
        throw new Error(`Failed to parse color: ${t2}`);
      }
      const e2 = [parseInt(s2[1], 10), parseInt(s2[2], 10), parseInt(s2[3], 10), s2[4] ? parseFloat(s2[4]) : 1];
      return this.j.set(t2, e2), e2;
    }
  };
  var C = class {
    constructor() {
      this.it = [];
    }
    nt(t2) {
      this.it = t2;
    }
    st(t2, i2, n2) {
      this.it.forEach(((s2) => {
        s2.st(t2, i2, n2);
      }));
    }
  };
  var y = class {
    st(t2, i2, n2) {
      t2.useBitmapCoordinateSpace(((t3) => this.et(t3, i2, n2)));
    }
  };
  var P = class extends y {
    constructor() {
      super(...arguments), this.rt = null;
    }
    ht(t2) {
      this.rt = t2;
    }
    et({ context: t2, horizontalPixelRatio: i2, verticalPixelRatio: n2 }) {
      if (null === this.rt || null === this.rt.lt) return;
      const s2 = this.rt.lt, e2 = this.rt, r2 = Math.max(1, Math.floor(i2)) % 2 / 2, h2 = (h3) => {
        t2.beginPath();
        for (let a2 = s2.to - 1; a2 >= s2.from; --a2) {
          const s3 = e2.ot[a2], l2 = Math.round(s3._t * i2) + r2, o2 = s3.ut * n2, _2 = h3 * n2 + r2;
          t2.moveTo(l2, o2), t2.arc(l2, o2, _2, 0, 2 * Math.PI);
        }
        t2.fill();
      };
      e2.ct > 0 && (t2.fillStyle = e2.dt, h2(e2.ft + e2.ct)), t2.fillStyle = e2.vt, h2(e2.ft);
    }
  };
  function k() {
    return { ot: [{ _t: 0, ut: 0, wt: 0, gt: 0 }], vt: "", dt: "", ft: 0, ct: 0, lt: null };
  }
  var T = { from: 0, to: 1 };
  var R = class {
    constructor(t2, i2, n2) {
      this.Mt = new C(), this.bt = [], this.St = [], this.xt = true, this.O = t2, this.Ct = i2, this.yt = n2, this.Mt.nt(this.bt);
    }
    Pt(t2) {
      this.kt(), this.xt = true;
    }
    Tt() {
      return this.xt && (this.Rt(), this.xt = false), this.Mt;
    }
    kt() {
      const t2 = this.yt.Dt();
      t2.length !== this.bt.length && (this.St = t2.map(k), this.bt = this.St.map(((t3) => {
        const i2 = new P();
        return i2.ht(t3), i2;
      })), this.Mt.nt(this.bt));
    }
    Rt() {
      const t2 = 2 === this.Ct.N().mode || !this.Ct.It(), i2 = this.yt.Vt(), n2 = this.Ct.Et(), s2 = this.O.Bt();
      this.kt(), i2.forEach(((i3, e2) => {
        const r2 = this.St[e2], h2 = i3.At(n2), a2 = i3.zt();
        !t2 && null !== h2 && i3.It() && null !== a2 ? (r2.vt = h2.Lt, r2.ft = h2.ft, r2.ct = h2.Ot, r2.ot[0].gt = h2.gt, r2.ot[0].ut = i3.Ft().Nt(h2.gt, a2.Wt), r2.dt = h2.Ht ?? this.O.Ut(r2.ot[0].ut / i3.Ft().$t()), r2.ot[0].wt = n2, r2.ot[0]._t = s2.jt(n2), r2.lt = T) : r2.lt = null;
      }));
    }
  };
  var D = class extends y {
    constructor(t2) {
      super(), this.qt = t2;
    }
    et({ context: t2, bitmapSize: i2, horizontalPixelRatio: n2, verticalPixelRatio: r2 }) {
      if (null === this.qt) return;
      const h2 = this.qt.Yt.It, a2 = this.qt.Kt.It;
      if (!h2 && !a2) return;
      const l2 = Math.round(this.qt._t * n2), o2 = Math.round(this.qt.ut * r2);
      t2.lineCap = "butt", h2 && l2 >= 0 && (t2.lineWidth = Math.floor(this.qt.Yt.ct * n2), t2.strokeStyle = this.qt.Yt.R, t2.fillStyle = this.qt.Yt.R, s(t2, this.qt.Yt.Gt), (function(t3, i3, n3, s2) {
        t3.beginPath();
        const e2 = t3.lineWidth % 2 ? 0.5 : 0;
        t3.moveTo(i3 + e2, n3), t3.lineTo(i3 + e2, s2), t3.stroke();
      })(t2, l2, 0, i2.height)), a2 && o2 >= 0 && (t2.lineWidth = Math.floor(this.qt.Kt.ct * r2), t2.strokeStyle = this.qt.Kt.R, t2.fillStyle = this.qt.Kt.R, s(t2, this.qt.Kt.Gt), e(t2, o2, 0, i2.width));
    }
  };
  var I = class {
    constructor(t2, i2) {
      this.xt = true, this.Zt = { Yt: { ct: 1, Gt: 0, R: "", It: false }, Kt: { ct: 1, Gt: 0, R: "", It: false }, _t: 0, ut: 0 }, this.Xt = new D(this.Zt), this.Jt = t2, this.yt = i2;
    }
    Pt() {
      this.xt = true;
    }
    Tt(t2) {
      return this.xt && (this.Rt(), this.xt = false), this.Xt;
    }
    Rt() {
      const t2 = this.Jt.It(), i2 = this.yt.Qt().N().crosshair, n2 = this.Zt;
      if (2 === i2.mode) return n2.Kt.It = false, void (n2.Yt.It = false);
      n2.Kt.It = t2 && this.Jt.ti(this.yt), n2.Yt.It = t2 && this.Jt.ii(), n2.Kt.ct = i2.horzLine.width, n2.Kt.Gt = i2.horzLine.style, n2.Kt.R = i2.horzLine.color, n2.Yt.ct = i2.vertLine.width, n2.Yt.Gt = i2.vertLine.style, n2.Yt.R = i2.vertLine.color, n2._t = this.Jt.ni(), n2.ut = this.Jt.si();
    }
  };
  function V(t2, i2, n2, s2, e2, r2) {
    t2.fillRect(i2 + r2, n2, s2 - 2 * r2, r2), t2.fillRect(i2 + r2, n2 + e2 - r2, s2 - 2 * r2, r2), t2.fillRect(i2, n2, r2, e2), t2.fillRect(i2 + s2 - r2, n2, r2, e2);
  }
  function E(t2, i2, n2, s2, e2, r2) {
    t2.save(), t2.globalCompositeOperation = "copy", t2.fillStyle = r2, t2.fillRect(i2, n2, s2, e2), t2.restore();
  }
  function B(t2, i2, n2, s2, e2, r2) {
    t2.beginPath(), t2.roundRect ? t2.roundRect(i2, n2, s2, e2, r2) : (t2.lineTo(i2 + s2 - r2[1], n2), 0 !== r2[1] && t2.arcTo(i2 + s2, n2, i2 + s2, n2 + r2[1], r2[1]), t2.lineTo(i2 + s2, n2 + e2 - r2[2]), 0 !== r2[2] && t2.arcTo(i2 + s2, n2 + e2, i2 + s2 - r2[2], n2 + e2, r2[2]), t2.lineTo(i2 + r2[3], n2 + e2), 0 !== r2[3] && t2.arcTo(i2, n2 + e2, i2, n2 + e2 - r2[3], r2[3]), t2.lineTo(i2, n2 + r2[0]), 0 !== r2[0] && t2.arcTo(i2, n2, i2 + r2[0], n2, r2[0]));
  }
  function A(t2, i2, n2, s2, e2, r2, h2 = 0, a2 = [0, 0, 0, 0], l2 = "") {
    if (t2.save(), !h2 || !l2 || l2 === r2) return B(t2, i2, n2, s2, e2, a2), t2.fillStyle = r2, t2.fill(), void t2.restore();
    const o2 = h2 / 2;
    var _2;
    B(t2, i2 + o2, n2 + o2, s2 - h2, e2 - h2, (_2 = -o2, a2.map(((t3) => 0 === t3 ? t3 : t3 + _2)))), "transparent" !== r2 && (t2.fillStyle = r2, t2.fill()), "transparent" !== l2 && (t2.lineWidth = h2, t2.strokeStyle = l2, t2.closePath(), t2.stroke()), t2.restore();
  }
  function z(t2, i2, n2, s2, e2, r2, h2) {
    t2.save(), t2.globalCompositeOperation = "copy";
    const a2 = t2.createLinearGradient(0, 0, 0, e2);
    a2.addColorStop(0, r2), a2.addColorStop(1, h2), t2.fillStyle = a2, t2.fillRect(i2, n2, s2, e2), t2.restore();
  }
  var L = class {
    constructor(t2, i2) {
      this.ht(t2, i2);
    }
    ht(t2, i2) {
      this.qt = t2, this.ei = i2;
    }
    $t(t2, i2) {
      return this.qt.It ? t2.P + t2.A + t2.I : 0;
    }
    st(t2, i2, n2, s2) {
      if (!this.qt.It || 0 === this.qt.ri.length) return;
      const e2 = this.qt.R, r2 = this.ei.Z, h2 = t2.useBitmapCoordinateSpace(((t3) => {
        const h3 = t3.context;
        h3.font = i2.k;
        const a2 = this.hi(t3, i2, n2, s2), l2 = a2.ai;
        return a2.li ? A(h3, l2.oi, l2._i, l2.ui, l2.ci, r2, l2.di, [l2.ft, 0, 0, l2.ft], r2) : A(h3, l2.fi, l2._i, l2.ui, l2.ci, r2, l2.di, [0, l2.ft, l2.ft, 0], r2), this.qt.pi && (h3.fillStyle = e2, h3.fillRect(l2.fi, l2.mi, l2.wi - l2.fi, l2.gi)), this.qt.Mi && (h3.fillStyle = i2.D, h3.fillRect(a2.li ? l2.bi - l2.di : 0, l2._i, l2.di, l2.Si - l2._i)), a2;
      }));
      t2.useMediaCoordinateSpace((({ context: t3 }) => {
        const n3 = h2.xi;
        t3.font = i2.k, t3.textAlign = h2.li ? "right" : "left", t3.textBaseline = "middle", t3.fillStyle = e2, t3.fillText(this.qt.ri, n3.Ci, (n3._i + n3.Si) / 2 + n3.yi);
      }));
    }
    hi(t2, i2, n2, s2) {
      const { context: e2, bitmapSize: r2, mediaSize: h2, horizontalPixelRatio: a2, verticalPixelRatio: l2 } = t2, o2 = this.qt.pi || !this.qt.Pi ? i2.C : 0, _2 = this.qt.ki ? i2.S : 0, u2 = i2.A + this.ei.Ti, c2 = i2.I + this.ei.Ri, d2 = i2.V, f2 = i2.B, p2 = this.qt.ri, v2 = i2.P, m2 = n2.Di(e2, p2), w2 = Math.ceil(n2.Ii(e2, p2)), g2 = v2 + u2 + c2, M2 = i2.S + d2 + f2 + w2 + o2, b2 = Math.max(1, Math.floor(l2));
      let S2 = Math.round(g2 * l2);
      S2 % 2 != b2 % 2 && (S2 += 1);
      const x2 = _2 > 0 ? Math.max(1, Math.floor(_2 * a2)) : 0, C2 = Math.round(M2 * a2), y2 = Math.round(o2 * a2), P2 = this.ei.Vi ?? this.ei.Ei ?? this.ei.Bi, k2 = Math.round(P2 * l2) - Math.floor(0.5 * l2), T2 = Math.floor(k2 + b2 / 2 - S2 / 2), R2 = T2 + S2, D2 = "right" === s2, I2 = D2 ? h2.width - _2 : _2, V2 = D2 ? r2.width - x2 : x2;
      let E2, B2, A2;
      return D2 ? (E2 = V2 - C2, B2 = V2 - y2, A2 = I2 - o2 - d2 - _2) : (E2 = V2 + C2, B2 = V2 + y2, A2 = I2 + o2 + d2), { li: D2, ai: { _i: T2, mi: k2, Si: R2, ui: C2, ci: S2, ft: 2 * a2, di: x2, oi: E2, fi: V2, wi: B2, gi: b2, bi: r2.width }, xi: { _i: T2 / l2, Si: R2 / l2, Ci: A2, yi: m2 } };
    }
  };
  var O = class {
    constructor(t2) {
      this.Ai = { Bi: 0, Z: "#000", Ri: 0, Ti: 0 }, this.zi = { ri: "", It: false, pi: true, Pi: false, Ht: "", R: "#FFF", Mi: false, ki: false }, this.Li = { ri: "", It: false, pi: false, Pi: true, Ht: "", R: "#FFF", Mi: true, ki: true }, this.xt = true, this.Oi = new (t2 || L)(this.zi, this.Ai), this.Ni = new (t2 || L)(this.Li, this.Ai);
    }
    ri() {
      return this.Fi(), this.zi.ri;
    }
    Bi() {
      return this.Fi(), this.Ai.Bi;
    }
    Pt() {
      this.xt = true;
    }
    $t(t2, i2 = false) {
      return Math.max(this.Oi.$t(t2, i2), this.Ni.$t(t2, i2));
    }
    Wi() {
      return this.Ai.Vi ?? null;
    }
    Hi() {
      return this.Ai.Vi ?? this.Ai.Ei ?? this.Bi();
    }
    Ui(t2) {
      this.Ai.Ei = t2 ?? void 0;
    }
    $i() {
      return this.Fi(), this.zi.It || this.Li.It;
    }
    ji() {
      return this.Fi(), this.zi.It;
    }
    Tt(t2) {
      return this.Fi(), this.zi.pi = this.zi.pi && t2.N().ticksVisible, this.Li.pi = this.Li.pi && t2.N().ticksVisible, this.Oi.ht(this.zi, this.Ai), this.Ni.ht(this.Li, this.Ai), this.Oi;
    }
    qi() {
      return this.Fi(), this.Oi.ht(this.zi, this.Ai), this.Ni.ht(this.Li, this.Ai), this.Ni;
    }
    Fi() {
      this.xt && (this.zi.pi = true, this.Li.pi = false, this.Yi(this.zi, this.Li, this.Ai));
    }
  };
  var N = class extends O {
    constructor(t2, i2, n2) {
      super(), this.Jt = t2, this.Ki = i2, this.Gi = n2;
    }
    Yi(t2, i2, n2) {
      if (t2.It = false, 2 === this.Jt.N().mode) return;
      const s2 = this.Jt.N().horzLine;
      if (!s2.labelVisible) return;
      const e2 = this.Ki.zt();
      if (!this.Jt.It() || this.Ki.Zi() || null === e2) return;
      const r2 = this.Ki.Xi().G(s2.labelBackgroundColor);
      n2.Z = r2.Z, t2.R = r2.X;
      const h2 = 2 / 12 * this.Ki.P();
      n2.Ti = h2, n2.Ri = h2;
      const a2 = this.Gi(this.Ki);
      n2.Bi = a2.Bi, t2.ri = this.Ki.Ji(a2.gt, e2), t2.It = true;
    }
  };
  var F = /[1-9]/g;
  var W = class {
    constructor() {
      this.qt = null;
    }
    ht(t2) {
      this.qt = t2;
    }
    st(t2, i2) {
      if (null === this.qt || false === this.qt.It || 0 === this.qt.ri.length) return;
      const n2 = t2.useMediaCoordinateSpace((({ context: t3 }) => (t3.font = i2.k, Math.round(i2.Qi.Ii(t3, a(this.qt).ri, F)))));
      if (n2 <= 0) return;
      const s2 = i2.tn, e2 = n2 + 2 * s2, r2 = e2 / 2, h2 = this.qt.nn;
      let l2 = this.qt.Bi, o2 = Math.floor(l2 - r2) + 0.5;
      o2 < 0 ? (l2 += Math.abs(0 - o2), o2 = Math.floor(l2 - r2) + 0.5) : o2 + e2 > h2 && (l2 -= Math.abs(h2 - (o2 + e2)), o2 = Math.floor(l2 - r2) + 0.5);
      const _2 = o2 + e2, u2 = Math.ceil(0 + i2.S + i2.C + i2.A + i2.P + i2.I);
      t2.useBitmapCoordinateSpace((({ context: t3, horizontalPixelRatio: n3, verticalPixelRatio: s3 }) => {
        const e3 = a(this.qt);
        t3.fillStyle = e3.Z;
        const r3 = Math.round(o2 * n3), h3 = Math.round(0 * s3), l3 = Math.round(_2 * n3), c2 = Math.round(u2 * s3), d2 = Math.round(2 * n3);
        if (t3.beginPath(), t3.moveTo(r3, h3), t3.lineTo(r3, c2 - d2), t3.arcTo(r3, c2, r3 + d2, c2, d2), t3.lineTo(l3 - d2, c2), t3.arcTo(l3, c2, l3, c2 - d2, d2), t3.lineTo(l3, h3), t3.fill(), e3.pi) {
          const r4 = Math.round(e3.Bi * n3), a2 = h3, l4 = Math.round((a2 + i2.C) * s3);
          t3.fillStyle = e3.R;
          const o3 = Math.max(1, Math.floor(n3)), _3 = Math.floor(0.5 * n3);
          t3.fillRect(r4 - _3, a2, o3, l4 - a2);
        }
      })), t2.useMediaCoordinateSpace((({ context: t3 }) => {
        const n3 = a(this.qt), e3 = 0 + i2.S + i2.C + i2.A + i2.P / 2;
        t3.font = i2.k, t3.textAlign = "left", t3.textBaseline = "middle", t3.fillStyle = n3.R;
        const r3 = i2.Qi.Di(t3, "Apr0");
        t3.translate(o2 + s2, e3 + r3), t3.fillText(n3.ri, 0, 0);
      }));
    }
  };
  var H = class {
    constructor(t2, i2, n2) {
      this.xt = true, this.Xt = new W(), this.Zt = { It: false, Z: "#4c525e", R: "white", ri: "", nn: 0, Bi: NaN, pi: true }, this.Ct = t2, this.sn = i2, this.Gi = n2;
    }
    Pt() {
      this.xt = true;
    }
    Tt() {
      return this.xt && (this.Rt(), this.xt = false), this.Xt.ht(this.Zt), this.Xt;
    }
    Rt() {
      const t2 = this.Zt;
      if (t2.It = false, 2 === this.Ct.N().mode) return;
      const i2 = this.Ct.N().vertLine;
      if (!i2.labelVisible) return;
      const n2 = this.sn.Bt();
      if (n2.Zi()) return;
      t2.nn = n2.nn();
      const s2 = this.Gi();
      if (null === s2) return;
      t2.Bi = s2.Bi;
      const e2 = n2.en(this.Ct.Et());
      t2.ri = n2.rn(a(e2)), t2.It = true;
      const r2 = this.sn.Xi().G(i2.labelBackgroundColor);
      t2.Z = r2.Z, t2.R = r2.X, t2.pi = n2.N().ticksVisible;
    }
  };
  var U = class {
    constructor() {
      this.hn = null, this.an = 0;
    }
    ln() {
      return this.an;
    }
    _n(t2) {
      this.an = t2;
    }
    Ft() {
      return this.hn;
    }
    un(t2) {
      this.hn = t2;
    }
    cn(t2) {
      return [];
    }
    dn() {
      return [];
    }
    It() {
      return true;
    }
  };
  var $;
  !(function(t2) {
    t2[t2.Normal = 0] = "Normal", t2[t2.Magnet = 1] = "Magnet", t2[t2.Hidden = 2] = "Hidden", t2[t2.MagnetOHLC = 3] = "MagnetOHLC";
  })($ || ($ = {}));
  var j = class extends U {
    constructor(t2, i2) {
      super(), this.yt = null, this.fn = NaN, this.pn = 0, this.vn = false, this.mn = /* @__PURE__ */ new Map(), this.wn = false, this.gn = /* @__PURE__ */ new WeakMap(), this.Mn = /* @__PURE__ */ new WeakMap(), this.bn = NaN, this.Sn = NaN, this.xn = NaN, this.Cn = NaN, this.sn = t2, this.yn = i2;
      this.Pn = /* @__PURE__ */ ((t3, i3) => (n3) => {
        const s2 = i3(), e2 = t3();
        if (n3 === a(this.yt).kn()) return { gt: e2, Bi: s2 };
        {
          const t4 = a(n3.zt());
          return { gt: n3.Tn(s2, t4), Bi: s2 };
        }
      })((() => this.fn), (() => this.Sn));
      const n2 = /* @__PURE__ */ ((t3, i3) => () => {
        const n3 = this.sn.Bt().Rn(t3()), s2 = i3();
        return n3 && Number.isFinite(s2) ? { wt: n3, Bi: s2 } : null;
      })((() => this.pn), (() => this.ni()));
      this.Dn = new H(this, t2, n2);
    }
    N() {
      return this.yn;
    }
    In(t2, i2) {
      this.xn = t2, this.Cn = i2;
    }
    Vn() {
      this.xn = NaN, this.Cn = NaN;
    }
    En() {
      return this.xn;
    }
    Bn() {
      return this.Cn;
    }
    An(t2, i2, n2) {
      this.wn || (this.wn = true), this.vn = true, this.zn(t2, i2, n2);
    }
    Et() {
      return this.pn;
    }
    ni() {
      return this.bn;
    }
    si() {
      return this.Sn;
    }
    It() {
      return this.vn;
    }
    Ln() {
      this.vn = false, this.On(), this.fn = NaN, this.bn = NaN, this.Sn = NaN, this.yt = null, this.Vn(), this.Nn();
    }
    Fn(t2) {
      if (!this.yn.doNotSnapToHiddenSeriesIndices) return t2;
      const i2 = this.sn, n2 = i2.Bt();
      let s2 = null, e2 = null;
      for (const n3 of i2.Wn()) {
        const i3 = n3.Un().Hn(t2, -1);
        if (i3) {
          if (i3.$n === t2) return t2;
          (null === s2 || i3.$n > s2) && (s2 = i3.$n);
        }
        const r3 = n3.Un().Hn(t2, 1);
        if (r3) {
          if (r3.$n === t2) return t2;
          (null === e2 || r3.$n < e2) && (e2 = r3.$n);
        }
      }
      const r2 = [s2, e2].filter(v);
      if (0 === r2.length) return t2;
      const h2 = n2.jt(t2), a2 = r2.map(((t3) => Math.abs(h2 - n2.jt(t3))));
      return r2[a2.indexOf(Math.min(...a2))];
    }
    jn(t2) {
      let i2 = this.gn.get(t2);
      i2 || (i2 = new I(this, t2), this.gn.set(t2, i2));
      let n2 = this.Mn.get(t2);
      return n2 || (n2 = new R(this.sn, this, t2), this.Mn.set(t2, n2)), [i2, n2];
    }
    ti(t2) {
      return t2 === this.yt && this.yn.horzLine.visible;
    }
    ii() {
      return this.yn.vertLine.visible;
    }
    qn(t2, i2) {
      this.vn && this.yt === t2 || this.mn.clear();
      const n2 = [];
      return this.yt === t2 && n2.push(this.Yn(this.mn, i2, this.Pn)), n2;
    }
    dn() {
      return this.vn ? [this.Dn] : [];
    }
    Kn() {
      return this.yt;
    }
    Nn() {
      this.sn.Gn().forEach(((t2) => {
        this.gn.get(t2)?.Pt(), this.Mn.get(t2)?.Pt();
      })), this.mn.forEach(((t2) => t2.Pt())), this.Dn.Pt();
    }
    Zn(t2) {
      return t2 && !t2.kn().Zi() ? t2.kn() : null;
    }
    zn(t2, i2, n2) {
      this.Xn(t2, i2, n2) && this.Nn();
    }
    Xn(t2, i2, n2) {
      const s2 = this.bn, e2 = this.Sn, r2 = this.fn, h2 = this.pn, a2 = this.yt, l2 = this.Zn(n2);
      this.pn = t2, this.bn = isNaN(t2) ? NaN : this.sn.Bt().jt(t2), this.yt = n2;
      const o2 = null !== l2 ? l2.zt() : null;
      return null !== l2 && null !== o2 ? (this.fn = i2, this.Sn = l2.Nt(i2, o2)) : (this.fn = NaN, this.Sn = NaN), s2 !== this.bn || e2 !== this.Sn || h2 !== this.pn || r2 !== this.fn || a2 !== this.yt;
    }
    On() {
      const t2 = this.sn.Jn().map(((t3) => t3.Un().Qn())).filter(v), i2 = 0 === t2.length ? null : Math.max(...t2);
      this.pn = null !== i2 ? i2 : NaN;
    }
    Yn(t2, i2, n2) {
      let s2 = t2.get(i2);
      return void 0 === s2 && (s2 = new N(this, i2, n2), t2.set(i2, s2)), s2;
    }
  };
  function q(t2) {
    return "left" === t2 || "right" === t2;
  }
  var Y = class _Y {
    constructor(t2) {
      this.ts = /* @__PURE__ */ new Map(), this.ns = [], this.ss = t2;
    }
    es(t2, i2) {
      const n2 = (function(t3, i3) {
        return void 0 === t3 ? i3 : { rs: Math.max(t3.rs, i3.rs), hs: t3.hs || i3.hs };
      })(this.ts.get(t2), i2);
      this.ts.set(t2, n2);
    }
    ls() {
      return this.ss;
    }
    _s(t2) {
      const i2 = this.ts.get(t2);
      return void 0 === i2 ? { rs: this.ss } : { rs: Math.max(this.ss, i2.rs), hs: i2.hs };
    }
    us() {
      this.cs(), this.ns = [{ ds: 0 }];
    }
    fs(t2) {
      this.cs(), this.ns = [{ ds: 1, Wt: t2 }];
    }
    ps(t2) {
      this.vs(), this.ns.push({ ds: 5, Wt: t2 });
    }
    cs() {
      this.vs(), this.ns.push({ ds: 6 });
    }
    ws() {
      this.cs(), this.ns = [{ ds: 4 }];
    }
    gs(t2) {
      this.cs(), this.ns.push({ ds: 2, Wt: t2 });
    }
    Ms(t2) {
      this.cs(), this.ns.push({ ds: 3, Wt: t2 });
    }
    bs() {
      return this.ns;
    }
    Ss(t2) {
      for (const i2 of t2.ns) this.xs(i2);
      this.ss = Math.max(this.ss, t2.ss), t2.ts.forEach(((t3, i2) => {
        this.es(i2, t3);
      }));
    }
    static Cs() {
      return new _Y(2);
    }
    static ys() {
      return new _Y(3);
    }
    xs(t2) {
      switch (t2.ds) {
        case 0:
          this.us();
          break;
        case 1:
          this.fs(t2.Wt);
          break;
        case 2:
          this.gs(t2.Wt);
          break;
        case 3:
          this.Ms(t2.Wt);
          break;
        case 4:
          this.ws();
          break;
        case 5:
          this.ps(t2.Wt);
          break;
        case 6:
          this.vs();
      }
    }
    vs() {
      const t2 = this.ns.findIndex(((t3) => 5 === t3.ds));
      -1 !== t2 && this.ns.splice(t2, 1);
    }
  };
  var K = class {
    formatTickmarks(t2) {
      return t2.map(((t3) => this.format(t3)));
    }
  };
  var G = ".";
  function Z(t2, i2) {
    if (!u(t2)) return "n/a";
    if (!c(i2)) throw new TypeError("invalid length");
    if (i2 < 0 || i2 > 16) throw new TypeError("invalid length");
    if (0 === i2) return t2.toString();
    return ("0000000000000000" + t2.toString()).slice(-i2);
  }
  var X = class extends K {
    constructor(t2, i2) {
      if (super(), i2 || (i2 = 1), u(t2) && c(t2) || (t2 = 100), t2 < 0) throw new TypeError("invalid base");
      this.Ki = t2, this.Ps = i2, this.ks();
    }
    format(t2) {
      const i2 = t2 < 0 ? "\u2212" : "";
      return t2 = Math.abs(t2), i2 + this.Ts(t2);
    }
    ks() {
      if (this.Rs = 0, this.Ki > 0 && this.Ps > 0) {
        let t2 = this.Ki;
        for (; t2 > 1; ) t2 /= 10, this.Rs++;
      }
    }
    Ts(t2) {
      const i2 = this.Ki / this.Ps;
      let n2 = Math.floor(t2), s2 = "";
      const e2 = void 0 !== this.Rs ? this.Rs : NaN;
      if (i2 > 1) {
        let r2 = +(Math.round(t2 * i2) - n2 * i2).toFixed(this.Rs);
        r2 >= i2 && (r2 -= i2, n2 += 1), s2 = G + Z(+r2.toFixed(this.Rs) * this.Ps, e2);
      } else n2 = Math.round(n2 * i2) / i2, e2 > 0 && (s2 = G + Z(0, e2));
      return n2.toFixed(0) + s2;
    }
  };
  var J = class extends X {
    constructor(t2 = 100) {
      super(t2);
    }
    format(t2) {
      return `${super.format(t2)}%`;
    }
  };
  var Q = class extends K {
    constructor(t2) {
      super(), this.Ds = t2;
    }
    format(t2) {
      let i2 = "";
      return t2 < 0 && (i2 = "-", t2 = -t2), t2 < 995 ? i2 + this.Is(t2) : t2 < 999995 ? i2 + this.Is(t2 / 1e3) + "K" : t2 < 999999995 ? (t2 = 1e3 * Math.round(t2 / 1e3), i2 + this.Is(t2 / 1e6) + "M") : (t2 = 1e6 * Math.round(t2 / 1e6), i2 + this.Is(t2 / 1e9) + "B");
    }
    Is(t2) {
      let i2;
      const n2 = Math.pow(10, this.Ds);
      return i2 = (t2 = Math.round(t2 * n2) / n2) >= 1e-15 && t2 < 1 ? t2.toFixed(this.Ds).replace(/\.?0+$/, "") : String(t2), i2.replace(/(\.[1-9]*)0+$/, ((t3, i3) => i3));
    }
  };
  var tt = /[2-9]/g;
  var it = class {
    constructor(t2 = 50) {
      this.Vs = 0, this.Es = 1, this.Bs = 1, this.As = {}, this.zs = /* @__PURE__ */ new Map(), this.Ls = t2;
    }
    Os() {
      this.Vs = 0, this.zs.clear(), this.Es = 1, this.Bs = 1, this.As = {};
    }
    Ii(t2, i2, n2) {
      return this.Ns(t2, i2, n2).width;
    }
    Di(t2, i2, n2) {
      const s2 = this.Ns(t2, i2, n2);
      return ((s2.actualBoundingBoxAscent || 0) - (s2.actualBoundingBoxDescent || 0)) / 2;
    }
    Ns(t2, i2, n2) {
      const s2 = n2 || tt, e2 = String(i2).replace(s2, "0");
      if (this.zs.has(e2)) return h(this.zs.get(e2)).Fs;
      if (this.Vs === this.Ls) {
        const t3 = this.As[this.Bs];
        delete this.As[this.Bs], this.zs.delete(t3), this.Bs++, this.Vs--;
      }
      t2.save(), t2.textBaseline = "middle";
      const r2 = t2.measureText(e2);
      return t2.restore(), 0 === r2.width && i2.length || (this.zs.set(e2, { Fs: r2, Ws: this.Es }), this.As[this.Es] = e2, this.Vs++, this.Es++), r2;
    }
  };
  var nt = class {
    constructor(t2) {
      this.Hs = null, this.M = null, this.Us = "right", this.$s = t2;
    }
    js(t2, i2, n2) {
      this.Hs = t2, this.M = i2, this.Us = n2;
    }
    st(t2) {
      null !== this.M && null !== this.Hs && this.Hs.st(t2, this.M, this.$s, this.Us);
    }
  };
  var st = class {
    constructor(t2, i2, n2) {
      this.qs = t2, this.$s = new it(50), this.Ys = i2, this.O = n2, this.F = -1, this.Xt = new nt(this.$s);
    }
    Tt() {
      const t2 = this.O.Ks(this.Ys);
      if (null === t2) return null;
      const i2 = t2.Gs(this.Ys) ? t2.Zs() : this.Ys.Ft();
      if (null === i2) return null;
      const n2 = t2.Xs(i2);
      if ("overlay" === n2) return null;
      const s2 = this.O.Js();
      return s2.P !== this.F && (this.F = s2.P, this.$s.Os()), this.Xt.js(this.qs.qi(), s2, n2), this.Xt;
    }
  };
  var et = class extends y {
    constructor() {
      super(...arguments), this.qt = null;
    }
    ht(t2) {
      this.qt = t2;
    }
    Qs(t2, i2) {
      if (!this.qt?.It) return null;
      const { ut: n2, ct: s2, te: e2 } = this.qt;
      return i2 >= n2 - s2 - 7 && i2 <= n2 + s2 + 7 ? { ie: this.qt, ne: Math.abs(i2 - n2), se: 2, ee: "price-line", te: e2 } : null;
    }
    et({ context: t2, bitmapSize: i2, horizontalPixelRatio: n2, verticalPixelRatio: r2 }) {
      if (null === this.qt) return;
      if (false === this.qt.It) return;
      const h2 = Math.round(this.qt.ut * r2);
      h2 < 0 || h2 > i2.height || (t2.lineCap = "butt", t2.strokeStyle = this.qt.R, t2.lineWidth = Math.floor(this.qt.ct * n2), s(t2, this.qt.Gt), e(t2, h2, 0, i2.width));
    }
  };
  var rt = class {
    constructor(t2) {
      this.re = { ut: 0, R: "rgba(0, 0, 0, 0)", ct: 1, Gt: 0, It: false }, this.he = new et(), this.xt = true, this.ae = t2, this.le = t2.Qt(), this.he.ht(this.re);
    }
    Pt() {
      this.xt = true;
    }
    Tt() {
      return this.ae.It() ? (this.xt && (this.oe(), this.xt = false), this.he) : null;
    }
  };
  var ht = class extends rt {
    constructor(t2) {
      super(t2);
    }
    oe() {
      this.re.It = false;
      const t2 = this.ae.Ft(), i2 = t2._e()._e;
      if (2 !== i2 && 3 !== i2) return;
      const n2 = this.ae.N();
      if (!n2.baseLineVisible || !this.ae.It()) return;
      const s2 = this.ae.zt();
      null !== s2 && (this.re.It = true, this.re.ut = t2.Nt(s2.Wt, s2.Wt), this.re.R = n2.baseLineColor, this.re.ct = n2.baseLineWidth, this.re.Gt = n2.baseLineStyle);
    }
  };
  var at = class extends y {
    constructor() {
      super(...arguments), this.qt = null;
    }
    ht(t2) {
      this.qt = t2;
    }
    ue() {
      return this.qt;
    }
    et({ context: t2, horizontalPixelRatio: i2, verticalPixelRatio: n2 }) {
      const s2 = this.qt;
      if (null === s2) return;
      const e2 = Math.max(1, Math.floor(i2)), r2 = e2 % 2 / 2, h2 = Math.round(s2.ce.x * i2) + r2, a2 = s2.ce.y * n2;
      t2.fillStyle = s2.de, t2.beginPath();
      const l2 = Math.max(2, 1.5 * s2.fe) * i2;
      t2.arc(h2, a2, l2, 0, 2 * Math.PI, false), t2.fill(), t2.fillStyle = s2.pe, t2.beginPath(), t2.arc(h2, a2, s2.ft * i2, 0, 2 * Math.PI, false), t2.fill(), t2.lineWidth = e2, t2.strokeStyle = s2.ve, t2.beginPath(), t2.arc(h2, a2, s2.ft * i2 + e2 / 2, 0, 2 * Math.PI, false), t2.stroke();
    }
  };
  var lt = [{ me: 0, we: 0.25, ge: 4, Me: 10, be: 0.25, Se: 0, xe: 0.4, Ce: 0.8 }, { me: 0.25, we: 0.525, ge: 10, Me: 14, be: 0, Se: 0, xe: 0.8, Ce: 0 }, { me: 0.525, we: 1, ge: 14, Me: 14, be: 0, Se: 0, xe: 0, Ce: 0 }];
  var ot = class {
    constructor(t2) {
      this.Xt = new at(), this.xt = true, this.ye = true, this.Pe = performance.now(), this.ke = this.Pe - 1, this.Te = t2;
    }
    Re() {
      this.ke = this.Pe - 1, this.Pt();
    }
    De() {
      if (this.Pt(), 2 === this.Te.N().lastPriceAnimation) {
        const t2 = performance.now(), i2 = this.ke - t2;
        if (i2 > 0) return void (i2 < 650 && (this.ke += 2600));
        this.Pe = t2, this.ke = t2 + 2600;
      }
    }
    Pt() {
      this.xt = true;
    }
    Ie() {
      this.ye = true;
    }
    It() {
      return 0 !== this.Te.N().lastPriceAnimation;
    }
    Ve() {
      switch (this.Te.N().lastPriceAnimation) {
        case 0:
          return false;
        case 1:
          return true;
        case 2:
          return performance.now() <= this.ke;
      }
    }
    Tt() {
      return this.xt ? (this.Rt(), this.xt = false, this.ye = false) : this.ye && (this.Ee(), this.ye = false), this.Xt;
    }
    Rt() {
      this.Xt.ht(null);
      const t2 = this.Te.Qt().Bt(), i2 = t2.Be(), n2 = this.Te.zt();
      if (null === i2 || null === n2) return;
      const s2 = this.Te.Ae(true);
      if (s2.ze || !i2.Le(s2.$n)) return;
      const e2 = { x: t2.jt(s2.$n), y: this.Te.Ft().Nt(s2.gt, n2.Wt) }, r2 = s2.R, h2 = this.Te.N().lineWidth, a2 = this.Oe(this.Ne(), r2);
      this.Xt.ht({ de: r2, fe: h2, pe: a2.pe, ve: a2.ve, ft: a2.ft, ce: e2 });
    }
    Ee() {
      const t2 = this.Xt.ue();
      if (null !== t2) {
        const i2 = this.Oe(this.Ne(), t2.de);
        t2.pe = i2.pe, t2.ve = i2.ve, t2.ft = i2.ft;
      }
    }
    Ne() {
      return this.Ve() ? performance.now() - this.Pe : 2599;
    }
    Fe(t2, i2, n2, s2) {
      const e2 = n2 + (s2 - n2) * i2;
      return this.Te.Qt().Xi().Y(t2, e2);
    }
    Oe(t2, i2) {
      const n2 = t2 % 2600 / 2600;
      let s2;
      for (const t3 of lt) if (n2 >= t3.me && n2 <= t3.we) {
        s2 = t3;
        break;
      }
      r(void 0 !== s2, "Last price animation internal logic error");
      const e2 = (n2 - s2.me) / (s2.we - s2.me);
      return { pe: this.Fe(i2, e2, s2.be, s2.Se), ve: this.Fe(i2, e2, s2.xe, s2.Ce), ft: (h2 = e2, a2 = s2.ge, l2 = s2.Me, a2 + (l2 - a2) * h2) };
      var h2, a2, l2;
    }
  };
  var _t = class extends rt {
    constructor(t2) {
      super(t2);
    }
    oe() {
      const t2 = this.re;
      t2.It = false;
      const i2 = this.ae.N();
      if (!i2.priceLineVisible || !this.ae.It()) return;
      const n2 = this.ae.Ae(0 === i2.priceLineSource);
      n2.ze || (t2.It = true, t2.ut = n2.Bi, t2.R = this.ae.We(n2.R), t2.ct = i2.priceLineWidth, t2.Gt = i2.priceLineStyle);
    }
  };
  var ut = class extends O {
    constructor(t2) {
      super(), this.Jt = t2;
    }
    Yi(t2, i2, n2) {
      t2.It = false, i2.It = false;
      const s2 = this.Jt;
      if (!s2.It()) return;
      const e2 = s2.N(), r2 = e2.lastValueVisible, h2 = "" !== s2.He(), a2 = 0 === e2.seriesLastValueMode, l2 = s2.Ae(false);
      if (l2.ze) return;
      r2 && (t2.ri = this.Ue(l2, r2, a2), t2.It = 0 !== t2.ri.length), (h2 || a2) && (i2.ri = this.$e(l2, r2, h2, a2), i2.It = i2.ri.length > 0);
      const o2 = s2.We(l2.R), _2 = this.Jt.Qt().Xi().G(o2);
      n2.Z = _2.Z, n2.Bi = l2.Bi, i2.Ht = s2.Qt().Ut(l2.Bi / s2.Ft().$t()), t2.Ht = o2, t2.R = _2.X, i2.R = _2.X;
    }
    $e(t2, i2, n2, s2) {
      let e2 = "";
      const r2 = this.Jt.He();
      return n2 && 0 !== r2.length && (e2 += `${r2} `), i2 && s2 && (e2 += this.Jt.Ft().je() ? t2.qe : t2.Ye), e2.trim();
    }
    Ue(t2, i2, n2) {
      return i2 ? n2 ? this.Jt.Ft().je() ? t2.Ye : t2.qe : t2.ri : "";
    }
  };
  function ct(t2, i2, n2, s2) {
    const e2 = Number.isFinite(i2), r2 = Number.isFinite(n2);
    return e2 && r2 ? t2(i2, n2) : e2 || r2 ? e2 ? i2 : n2 : s2;
  }
  var dt = class _dt {
    constructor(t2, i2) {
      this.Ke = t2, this.Ge = i2;
    }
    Ze(t2) {
      return null !== t2 && (this.Ke === t2.Ke && this.Ge === t2.Ge);
    }
    Xe() {
      return new _dt(this.Ke, this.Ge);
    }
    Je() {
      return this.Ke;
    }
    Qe() {
      return this.Ge;
    }
    tr() {
      return this.Ge - this.Ke;
    }
    Zi() {
      return this.Ge === this.Ke || Number.isNaN(this.Ge) || Number.isNaN(this.Ke);
    }
    Ss(t2) {
      return null === t2 ? this : new _dt(ct(Math.min, this.Je(), t2.Je(), -1 / 0), ct(Math.max, this.Qe(), t2.Qe(), 1 / 0));
    }
    ir(t2) {
      if (!u(t2)) return;
      if (0 === this.Ge - this.Ke) return;
      const i2 = 0.5 * (this.Ge + this.Ke);
      let n2 = this.Ge - i2, s2 = this.Ke - i2;
      n2 *= t2, s2 *= t2, this.Ge = i2 + n2, this.Ke = i2 + s2;
    }
    nr(t2) {
      u(t2) && (this.Ge += t2, this.Ke += t2);
    }
    sr() {
      return { minValue: this.Ke, maxValue: this.Ge };
    }
    static er(t2) {
      return null === t2 ? null : new _dt(t2.minValue, t2.maxValue);
    }
  };
  var ft = class _ft {
    constructor(t2, i2) {
      this.rr = t2, this.hr = i2 || null;
    }
    ar() {
      return this.rr;
    }
    lr() {
      return this.hr;
    }
    sr() {
      return { priceRange: null === this.rr ? null : this.rr.sr(), margins: this.hr || void 0 };
    }
    static er(t2) {
      return null === t2 ? null : new _ft(dt.er(t2.priceRange), t2.margins);
    }
  };
  var pt = [2, 4, 8, 16, 32, 64, 128, 256, 512];
  var vt = "Custom series with conflation reducer must have a priceValueBuilder method";
  var mt = class extends rt {
    constructor(t2, i2) {
      super(t2), this._r = i2;
    }
    oe() {
      const t2 = this.re;
      t2.It = false;
      const i2 = this._r.N();
      if (!this.ae.It() || !i2.lineVisible) return;
      const n2 = this._r.ur();
      null !== n2 && (t2.It = true, t2.ut = n2, t2.R = i2.color, t2.ct = i2.lineWidth, t2.Gt = i2.lineStyle, t2.te = this._r.N().id);
    }
  };
  var wt = class extends O {
    constructor(t2, i2) {
      super(), this.Te = t2, this._r = i2;
    }
    Yi(t2, i2, n2) {
      t2.It = false, i2.It = false;
      const s2 = this._r.N(), e2 = s2.axisLabelVisible, r2 = "" !== s2.title, h2 = this.Te;
      if (!e2 || !h2.It()) return;
      const a2 = this._r.ur();
      if (null === a2) return;
      r2 && (i2.ri = s2.title, i2.It = true), i2.Ht = h2.Qt().Ut(a2 / h2.Ft().$t()), t2.ri = this.cr(s2.price), t2.It = true;
      const l2 = this.Te.Qt().Xi().G(s2.axisLabelColor || s2.color);
      n2.Z = l2.Z;
      const o2 = s2.axisLabelTextColor || l2.X;
      t2.R = o2, i2.R = o2, n2.Bi = a2;
    }
    cr(t2) {
      const i2 = this.Te.zt();
      return null === i2 ? "" : this.Te.Ft().Ji(t2, i2.Wt);
    }
  };
  var gt = class {
    constructor(t2, i2) {
      this.Te = t2, this.yn = i2, this.dr = new mt(t2, this), this.qs = new wt(t2, this), this.pr = new st(this.qs, t2, t2.Qt());
    }
    vr(t2) {
      _(this.yn, t2), this.Pt(), this.Te.Qt().mr();
    }
    N() {
      return this.yn;
    }
    wr() {
      return this.dr;
    }
    gr() {
      return this.pr;
    }
    Mr() {
      return this.qs;
    }
    Pt() {
      this.dr.Pt(), this.qs.Pt();
    }
    ur() {
      const t2 = this.Te, i2 = t2.Ft();
      if (t2.Qt().Bt().Zi() || i2.Zi()) return null;
      const n2 = t2.zt();
      return null === n2 ? null : i2.Nt(this.yn.price, n2.Wt);
    }
  };
  var Mt = class {
    constructor() {
      this.br = /* @__PURE__ */ new WeakMap();
    }
    Sr(t2, i2, n2) {
      const s2 = 1 / i2 * n2;
      if (t2 >= s2) return 1;
      const e2 = s2 / t2, r2 = Math.pow(2, Math.floor(Math.log2(e2)));
      return Math.min(r2, 512);
    }
    Cr(t2, i2, n2, s2 = false, e2) {
      if (0 === t2.length || i2 <= 1) return t2;
      const r2 = this.yr(i2);
      if (r2 <= 1) return t2;
      const h2 = this.Pr(t2);
      let a2 = h2.kr.get(r2);
      return void 0 !== a2 || (a2 = this.Tr(t2, r2, n2, s2, e2, h2.kr), h2.kr.set(r2, a2)), a2;
    }
    Rr(t2, i2, n2, s2, e2 = false, r2) {
      if (n2 < 1 || 0 === t2.length) return t2;
      const h2 = this.Pr(t2), a2 = h2.kr.get(n2);
      if (!a2) return this.Cr(t2, n2, s2, e2, r2);
      const l2 = this.Dr(t2, i2, n2, a2, e2, s2, r2);
      return h2.kr.set(n2, l2), l2;
    }
    yr(t2) {
      if (t2 <= 2) return 2;
      for (const i2 of pt) if (t2 <= i2) return i2;
      return 512;
    }
    Ir(t2) {
      if (0 === t2.length) return 0;
      const i2 = t2[0], n2 = t2[t2.length - 1];
      return 31 * t2.length + 17 * i2.$n + 13 * n2.$n;
    }
    Tr(t2, i2, n2, s2 = false, e2, r2 = /* @__PURE__ */ new Map()) {
      if (2 === i2) return this.Vr(t2, 2, n2, s2, e2);
      const h2 = i2 / 2;
      let a2 = r2.get(h2);
      return a2 || (a2 = this.Tr(t2, h2, n2, s2, e2, r2), r2.set(h2, a2)), this.Er(a2, n2, s2, e2);
    }
    Vr(t2, i2, n2, s2 = false, e2) {
      const r2 = this.Br(t2, i2, n2, s2, e2);
      return this.Ar(r2, s2);
    }
    Er(t2, i2, n2 = false, s2) {
      const e2 = this.Br(t2, 2, i2, n2, s2);
      return this.Ar(e2, n2);
    }
    Br(t2, i2, n2, s2 = false, e2) {
      const r2 = [];
      for (let h2 = 0; h2 < t2.length; h2 += i2) {
        if (t2.length - h2 >= i2) {
          const i3 = this.zr(t2[h2], t2[h2 + 1], n2, s2, e2);
          i3.Lr = false, r2.push(i3);
        } else if (0 === r2.length) r2.push(this.Or(t2[h2], true));
        else {
          const i3 = r2[r2.length - 1];
          r2[r2.length - 1] = this.Nr(i3, t2[h2], n2, s2, e2);
        }
      }
      return r2;
    }
    Fr(t2, i2) {
      return (t2 ?? 1) + (i2 ?? 1);
    }
    zr(t2, i2, n2, s2 = false, e2) {
      if (!s2 || !n2 || !e2) {
        const n3 = t2.Wt[1] > i2.Wt[1] ? t2.Wt[1] : i2.Wt[1], s3 = t2.Wt[2] < i2.Wt[2] ? t2.Wt[2] : i2.Wt[2];
        return { Wr: t2.$n, Hr: i2.$n, Ur: t2.wt, $r: i2.wt, jr: t2.Wt[0], qr: n3, Yr: s3, Kr: i2.Wt[3], Gr: this.Fr(t2.Gr, i2.Gr), Zr: void 0, Lr: false };
      }
      const r2 = n2(this.Xr(t2, e2), this.Xr(i2, e2)), h2 = e2(r2), a2 = h2.length ? h2[h2.length - 1] : 0;
      return { Wr: t2.$n, Hr: i2.$n, Ur: t2.wt, $r: i2.wt, jr: t2.Wt[0], qr: Math.max(t2.Wt[1], a2), Yr: Math.min(t2.Wt[2], a2), Kr: a2, Gr: this.Fr(t2.Gr, i2.Gr), Zr: r2, Lr: false };
    }
    Nr(t2, i2, n2, s2 = false, e2) {
      if (!s2 || !n2 || !e2) return { Wr: t2.Wr, Hr: i2.$n, Ur: t2.Ur, $r: i2.wt, jr: t2.jr, qr: t2.qr > i2.Wt[1] ? t2.qr : i2.Wt[1], Yr: t2.Yr < i2.Wt[2] ? t2.Yr : i2.Wt[2], Kr: i2.Wt[3], Gr: t2.Gr + (i2.Gr ?? 1), Zr: t2.Zr, Lr: false };
      const r2 = t2.Zr, h2 = this.Xr(i2, e2), a2 = r2 ? { data: r2, index: t2.Wr, originalTime: t2.Ur, time: t2.Ur, priceValues: e2(r2) } : null, l2 = a2 ? n2(a2, h2) : h2.data, o2 = a2 ? e2(l2) : h2.priceValues, _2 = o2.length ? o2[o2.length - 1] : 0;
      return { Wr: t2.Wr, Hr: i2.$n, Ur: t2.Ur, $r: i2.wt, jr: t2.jr, qr: Math.max(t2.qr, _2), Yr: Math.min(t2.Yr, _2), Kr: _2, Gr: t2.Gr + (i2.Gr ?? 1), Zr: l2, Lr: false };
    }
    Jr(t2, i2, n2, s2, e2, r2, h2 = false, a2) {
      const l2 = i2 === s2 ? e2 : t2[i2];
      if (n2 - i2 == 1) return this.Or(l2, true);
      const o2 = i2 + 1 === s2 ? e2 : t2[i2 + 1];
      let _2 = this.zr(l2, o2, r2, h2, a2);
      for (let l3 = i2 + 2; l3 < n2; l3++) {
        const i3 = l3 === s2 ? e2 : t2[l3];
        _2 = this.Nr(_2, i3, r2, h2, a2);
      }
      return _2;
    }
    Xr(t2, i2) {
      const n2 = t2.ue ?? {};
      return { data: t2.ue, index: t2.$n, originalTime: t2.Qr, time: t2.wt, priceValues: i2(n2) };
    }
    th(t2, i2 = false) {
      const n2 = true === i2, s2 = !!t2.Zr;
      return { ...{ $n: t2.Wr, wt: t2.Ur, Qr: t2.Ur, Wt: [n2 ? t2.Kr : t2.jr, t2.qr, t2.Yr, t2.Kr], Gr: t2.Gr }, ue: n2 ? s2 ? t2.Zr : { wt: t2.Ur } : void 0 };
    }
    Ar(t2, i2 = false) {
      return t2.map(((t3) => this.th(t3, i2)));
    }
    Dr(t2, i2, n2, s2, e2 = false, r2, h2) {
      if (0 === s2.length) return s2;
      const a2 = t2.length - 1, l2 = Math.floor(a2 / n2) * n2;
      if (Math.min(l2 + n2, t2.length) - l2 < n2 && t2.length > n2) {
        const s3 = t2.slice();
        return s3[s3.length - 1] = i2, this.Cr(s3, n2, r2, e2, h2);
      }
      if (Math.floor((a2 - 1) / n2) === Math.floor(a2 / n2) || 1 === s2.length) {
        const o2 = Math.min(l2 + n2, t2.length), _2 = o2 - l2;
        if (_2 <= 0) return s2;
        const u2 = 1 === _2 ? this.Or(l2 === a2 ? i2 : t2[l2], true) : this.Jr(t2, l2, o2, a2, i2, r2, e2, h2);
        return s2[s2.length - 1] = this.th(u2, e2), s2;
      }
      {
        const s3 = t2.slice();
        return s3[s3.length - 1] = i2, this.Cr(s3, n2, r2, e2, h2);
      }
    }
    Or(t2, i2 = false) {
      return { Wr: t2.$n, Hr: t2.$n, Ur: t2.wt, $r: t2.wt, jr: t2.Wt[0], qr: t2.Wt[1], Yr: t2.Wt[2], Kr: t2.Wt[3], Gr: t2.Gr ?? 1, Zr: t2.ue, Lr: i2 };
    }
    Pr(t2) {
      const i2 = this.ih(t2), n2 = this.Ir(t2);
      return i2.nh !== n2 && (i2.kr.clear(), i2.nh = n2), i2;
    }
    ih(t2) {
      let i2 = this.br.get(t2);
      return void 0 === i2 && (i2 = { nh: this.Ir(t2), kr: /* @__PURE__ */ new Map() }, this.br.set(t2, i2)), i2;
    }
  };
  var bt = class extends U {
    constructor(t2) {
      super(), this.sn = t2;
    }
    Qt() {
      return this.sn;
    }
  };
  var St = { Bar: (t2, i2, n2, s2) => {
    const e2 = i2.upColor, r2 = i2.downColor, h2 = a(t2(n2, s2)), o2 = l(h2.Wt[0]) <= l(h2.Wt[3]);
    return { sh: h2.R ?? (o2 ? e2 : r2) };
  }, Candlestick: (t2, i2, n2, s2) => {
    const e2 = i2.upColor, r2 = i2.downColor, h2 = i2.borderUpColor, o2 = i2.borderDownColor, _2 = i2.wickUpColor, u2 = i2.wickDownColor, c2 = a(t2(n2, s2)), d2 = l(c2.Wt[0]) <= l(c2.Wt[3]);
    return { sh: c2.R ?? (d2 ? e2 : r2), eh: c2.Ht ?? (d2 ? h2 : o2), rh: c2.hh ?? (d2 ? _2 : u2) };
  }, Custom: (t2, i2, n2, s2) => ({ sh: a(t2(n2, s2)).R ?? i2.color }), Area: (t2, i2, n2, s2) => {
    const e2 = a(t2(n2, s2));
    return { sh: e2.vt ?? i2.lineColor, vt: e2.vt ?? i2.lineColor, ah: e2.ah ?? i2.topColor, oh: e2.oh ?? i2.bottomColor };
  }, Baseline: (t2, i2, n2, s2) => {
    const e2 = a(t2(n2, s2));
    return { sh: e2.Wt[3] >= i2.baseValue.price ? i2.topLineColor : i2.bottomLineColor, _h: e2._h ?? i2.topLineColor, uh: e2.uh ?? i2.bottomLineColor, dh: e2.dh ?? i2.topFillColor1, fh: e2.fh ?? i2.topFillColor2, ph: e2.ph ?? i2.bottomFillColor1, mh: e2.mh ?? i2.bottomFillColor2 };
  }, Line: (t2, i2, n2, s2) => {
    const e2 = a(t2(n2, s2));
    return { sh: e2.R ?? i2.color, vt: e2.R ?? i2.color };
  }, Histogram: (t2, i2, n2, s2) => ({ sh: a(t2(n2, s2)).R ?? i2.color }) };
  var xt = class {
    constructor(t2) {
      this.wh = (t3, i2) => void 0 !== i2 ? i2.Wt : this.Te.Un().gh(t3), this.Te = t2, this.Mh = St[t2.bh()];
    }
    Sh(t2, i2) {
      return this.Mh(this.wh, this.Te.N(), t2, i2);
    }
  };
  function Ct(t2, i2, n2, s2, e2 = 0, r2 = i2.length) {
    let h2 = r2 - e2;
    for (; 0 < h2; ) {
      const r3 = h2 >> 1, a2 = e2 + r3;
      s2(i2[a2], n2) === t2 ? (e2 = a2 + 1, h2 -= r3 + 1) : h2 = r3;
    }
    return e2;
  }
  var yt = Ct.bind(null, true);
  var Pt = Ct.bind(null, false);
  var kt;
  !(function(t2) {
    t2[t2.NearestLeft = -1] = "NearestLeft", t2[t2.None = 0] = "None", t2[t2.NearestRight = 1] = "NearestRight";
  })(kt || (kt = {}));
  var Tt = 30;
  var Rt = class {
    constructor() {
      this.xh = [], this.Ch = /* @__PURE__ */ new Map(), this.yh = /* @__PURE__ */ new Map(), this.Ph = [];
    }
    kh() {
      return this.Th() > 0 ? this.xh[this.xh.length - 1] : null;
    }
    Rh() {
      return this.Th() > 0 ? this.Dh(0) : null;
    }
    Qn() {
      return this.Th() > 0 ? this.Dh(this.xh.length - 1) : null;
    }
    Th() {
      return this.xh.length;
    }
    Zi() {
      return 0 === this.Th();
    }
    Le(t2) {
      return null !== this.Ih(t2, 0);
    }
    gh(t2) {
      return this.Hn(t2);
    }
    Hn(t2, i2 = 0) {
      const n2 = this.Ih(t2, i2);
      return null === n2 ? null : { ...this.Vh(n2), $n: this.Dh(n2) };
    }
    Eh() {
      return this.xh;
    }
    Bh(t2, i2, n2) {
      if (this.Zi()) return null;
      let s2 = null;
      for (const e2 of n2) {
        s2 = Dt(s2, this.Ah(t2, i2, e2));
      }
      return s2;
    }
    ht(t2) {
      this.yh.clear(), this.Ch.clear(), this.xh = t2, this.Ph = t2.map(((t3) => t3.$n));
    }
    zh() {
      return this.Ph;
    }
    Dh(t2) {
      return this.xh[t2].$n;
    }
    Vh(t2) {
      return this.xh[t2];
    }
    Ih(t2, i2) {
      const n2 = this.Lh(t2);
      if (null === n2 && 0 !== i2) switch (i2) {
        case -1:
          return this.Oh(t2);
        case 1:
          return this.Nh(t2);
        default:
          throw new TypeError("Unknown search mode");
      }
      return n2;
    }
    Oh(t2) {
      let i2 = this.Fh(t2);
      return i2 > 0 && (i2 -= 1), i2 !== this.xh.length && this.Dh(i2) < t2 ? i2 : null;
    }
    Nh(t2) {
      const i2 = this.Wh(t2);
      return i2 !== this.xh.length && t2 < this.Dh(i2) ? i2 : null;
    }
    Lh(t2) {
      const i2 = this.Fh(t2);
      return i2 === this.xh.length || t2 < this.xh[i2].$n ? null : i2;
    }
    Fh(t2) {
      return yt(this.xh, t2, ((t3, i2) => t3.$n < i2));
    }
    Wh(t2) {
      return Pt(this.xh, t2, ((t3, i2) => t3.$n > i2));
    }
    Hh(t2, i2, n2) {
      let s2 = null;
      for (let e2 = t2; e2 < i2; e2++) {
        const t3 = this.xh[e2].Wt[n2];
        Number.isNaN(t3) || (null === s2 ? s2 = { Uh: t3, $h: t3 } : (t3 < s2.Uh && (s2.Uh = t3), t3 > s2.$h && (s2.$h = t3)));
      }
      return s2;
    }
    Ah(t2, i2, n2) {
      if (this.Zi()) return null;
      let s2 = null;
      const e2 = a(this.Rh()), r2 = a(this.Qn()), h2 = Math.max(t2, e2), l2 = Math.min(i2, r2), o2 = Math.ceil(h2 / Tt) * Tt, _2 = Math.max(o2, Math.floor(l2 / Tt) * Tt);
      {
        const t3 = this.Fh(h2), e3 = this.Wh(Math.min(l2, o2, i2));
        s2 = Dt(s2, this.Hh(t3, e3, n2));
      }
      let u2 = this.Ch.get(n2);
      void 0 === u2 && (u2 = /* @__PURE__ */ new Map(), this.Ch.set(n2, u2));
      for (let t3 = Math.max(o2 + 1, h2); t3 < _2; t3 += Tt) {
        const i3 = Math.floor(t3 / Tt);
        let e3 = u2.get(i3);
        if (void 0 === e3) {
          const t4 = this.Fh(i3 * Tt), s3 = this.Wh((i3 + 1) * Tt - 1);
          e3 = this.Hh(t4, s3, n2), u2.set(i3, e3);
        }
        s2 = Dt(s2, e3);
      }
      {
        const t3 = this.Fh(_2), i3 = this.Wh(l2);
        s2 = Dt(s2, this.Hh(t3, i3, n2));
      }
      return s2;
    }
  };
  function Dt(t2, i2) {
    if (null === t2) return i2;
    if (null === i2) return t2;
    return { Uh: Math.min(t2.Uh, i2.Uh), $h: Math.max(t2.$h, i2.$h) };
  }
  function It() {
    return new Rt();
  }
  var Vt = { setLineStyle: s };
  var Et = class {
    constructor(t2) {
      this.jh = t2;
    }
    st(t2, i2, n2) {
      this.jh.draw(t2, Vt);
    }
    qh(t2, i2, n2) {
      this.jh.drawBackground?.(t2, Vt);
    }
  };
  var Bt = class {
    constructor(t2) {
      this.zs = null, this.Yh = t2;
    }
    Tt() {
      const t2 = this.Yh.renderer();
      if (null === t2) return null;
      if (this.zs?.Kh === t2) return this.zs.Gh;
      const i2 = new Et(t2);
      return this.zs = { Kh: t2, Gh: i2 }, i2;
    }
    Zh() {
      return this.Yh.zOrder?.() ?? "normal";
    }
  };
  var At = class {
    constructor(t2) {
      this.Xh = null, this.Jh = t2;
    }
    Qh() {
      return this.Jh;
    }
    Nn() {
      this.Jh.updateAllViews?.();
    }
    jn() {
      const t2 = this.Jh.paneViews?.() ?? [];
      if (this.Xh?.Kh === t2) return this.Xh.Gh;
      const i2 = t2.map(((t3) => new Bt(t3)));
      return this.Xh = { Kh: t2, Gh: i2 }, i2;
    }
    Qs(t2, i2) {
      return this.Jh.hitTest?.(t2, i2) ?? null;
    }
  };
  var zt = class extends At {
    cn() {
      return [];
    }
  };
  var Lt = class {
    constructor(t2) {
      this.jh = t2;
    }
    st(t2, i2, n2) {
      this.jh.draw(t2, Vt);
    }
    qh(t2, i2, n2) {
      this.jh.drawBackground?.(t2, Vt);
    }
  };
  var Ot = class {
    constructor(t2) {
      this.zs = null, this.Yh = t2;
    }
    Tt() {
      const t2 = this.Yh.renderer();
      if (null === t2) return null;
      if (this.zs?.Kh === t2) return this.zs.Gh;
      const i2 = new Lt(t2);
      return this.zs = { Kh: t2, Gh: i2 }, i2;
    }
    Zh() {
      return this.Yh.zOrder?.() ?? "normal";
    }
  };
  function Nt(t2) {
    return { ri: t2.text(), Bi: t2.coordinate(), Vi: t2.fixedCoordinate?.(), R: t2.textColor(), Z: t2.backColor(), It: t2.visible?.() ?? true, pi: t2.tickVisible?.() ?? true };
  }
  var Ft = class {
    constructor(t2, i2) {
      this.Xt = new W(), this.ta = t2, this.ia = i2;
    }
    Tt() {
      return this.Xt.ht({ nn: this.ia.nn(), ...Nt(this.ta) }), this.Xt;
    }
  };
  var Wt = class extends O {
    constructor(t2, i2) {
      super(), this.ta = t2, this.Ki = i2;
    }
    Yi(t2, i2, n2) {
      const s2 = Nt(this.ta);
      n2.Z = s2.Z, t2.R = s2.R;
      const e2 = 2 / 12 * this.Ki.P();
      n2.Ti = e2, n2.Ri = e2, n2.Bi = s2.Bi, n2.Vi = s2.Vi, t2.ri = s2.ri, t2.It = s2.It, t2.pi = s2.pi;
    }
  };
  var Ht = class extends At {
    constructor(t2, i2) {
      super(t2), this.na = null, this.sa = null, this.ea = null, this.ra = null, this.Te = i2;
    }
    dn() {
      const t2 = this.Jh.timeAxisViews?.() ?? [];
      if (this.na?.Kh === t2) return this.na.Gh;
      const i2 = this.Te.Qt().Bt(), n2 = t2.map(((t3) => new Ft(t3, i2)));
      return this.na = { Kh: t2, Gh: n2 }, n2;
    }
    qn() {
      const t2 = this.Jh.priceAxisViews?.() ?? [];
      if (this.sa?.Kh === t2) return this.sa.Gh;
      const i2 = this.Te.Ft(), n2 = t2.map(((t3) => new Wt(t3, i2)));
      return this.sa = { Kh: t2, Gh: n2 }, n2;
    }
    ha() {
      const t2 = this.Jh.priceAxisPaneViews?.() ?? [];
      if (this.ea?.Kh === t2) return this.ea.Gh;
      const i2 = t2.map(((t3) => new Ot(t3)));
      return this.ea = { Kh: t2, Gh: i2 }, i2;
    }
    aa() {
      const t2 = this.Jh.timeAxisPaneViews?.() ?? [];
      if (this.ra?.Kh === t2) return this.ra.Gh;
      const i2 = t2.map(((t3) => new Ot(t3)));
      return this.ra = { Kh: t2, Gh: i2 }, i2;
    }
    la(t2, i2) {
      return this.Jh.autoscaleInfo?.(t2, i2) ?? null;
    }
  };
  function Ut(t2, i2, n2, s2) {
    t2.forEach(((t3) => {
      i2(t3).forEach(((t4) => {
        t4.Zh() === n2 && s2.push(t4);
      }));
    }));
  }
  function $t(t2) {
    return t2.jn();
  }
  function jt(t2) {
    return t2.ha();
  }
  function qt(t2) {
    return t2.aa();
  }
  var Yt = ["Area", "Line", "Baseline"];
  var Kt = class extends bt {
    constructor(t2, i2, n2, s2, e2) {
      super(t2), this.qt = It(), this.dr = new _t(this), this.oa = [], this._a = new ht(this), this.ua = null, this.ca = null, this.da = null, this.fa = [], this.pa = new Mt(), this.va = /* @__PURE__ */ new Map(), this.ma = null, this.yn = n2, this.wa = i2;
      const r2 = new ut(this);
      if (this.mn = [r2], this.pr = new st(r2, this, t2), Yt.includes(this.wa) && (this.ua = new ot(this)), this.ga(), this.Yh = s2(this, this.Qt(), e2), "Custom" === this.wa) {
        const t3 = this.Yh;
        t3.Ma && this.ba(t3.Ma);
      }
    }
    m() {
      null !== this.da && clearTimeout(this.da);
    }
    We(t2) {
      return this.yn.priceLineColor || t2;
    }
    Ae(t2) {
      const i2 = { ze: true }, n2 = this.Ft();
      if (this.Qt().Bt().Zi() || n2.Zi() || this.qt.Zi()) return i2;
      const s2 = this.Qt().Bt().Be(), e2 = this.zt();
      if (null === s2 || null === e2) return i2;
      let r2, h2;
      if (t2) {
        const t3 = this.qt.kh();
        if (null === t3) return i2;
        r2 = t3, h2 = t3.$n;
      } else {
        const t3 = this.qt.Hn(s2.bi(), -1);
        if (null === t3) return i2;
        if (r2 = this.qt.gh(t3.$n), null === r2) return i2;
        h2 = t3.$n;
      }
      const a2 = r2.Wt[3], l2 = this.Sa().Sh(h2, { Wt: r2 }), o2 = n2.Nt(a2, e2.Wt);
      return { ze: false, gt: a2, ri: n2.Ji(a2, e2.Wt), qe: n2.xa(a2), Ye: n2.Ca(a2, e2.Wt), R: l2.sh, Bi: o2, $n: h2 };
    }
    Sa() {
      return null !== this.ca || (this.ca = new xt(this)), this.ca;
    }
    N() {
      return this.yn;
    }
    vr(t2) {
      const i2 = this.Qt(), { priceScaleId: n2, visible: s2, priceFormat: e2 } = t2;
      void 0 !== n2 && n2 !== this.yn.priceScaleId && i2.ya(this, n2), void 0 !== s2 && s2 !== this.yn.visible && i2.Pa();
      const r2 = void 0 !== t2.conflationThresholdFactor;
      _(this.yn, t2), Object.prototype.hasOwnProperty.call(t2, "autoscaleInfoProvider") && void 0 === t2.autoscaleInfoProvider && (this.yn.autoscaleInfoProvider = void 0), r2 && (this.va.clear(), this.Qt().mr()), void 0 !== e2 && (this.ga(), i2.ka()), i2.Ta(this), i2.Ra(), this.Yh.Pt("options");
    }
    ht(t2, i2) {
      this.qt.ht(t2), this.va.clear();
      const n2 = this.Qt().Bt().N();
      n2.enableConflation && n2.precomputeConflationOnInit && this.Da(n2.precomputeConflationPriority), this.Ia(), null !== this.ua && (i2 && i2.Va ? this.ua.De() : 0 === t2.length && this.ua.Re());
      const s2 = this.Qt().Ks(this);
      this.Qt().Ea(s2), this.Qt().Ta(this), this.Qt().Ra(), this.Qt().mr();
    }
    Ia() {
      this.Yh.Pt("data");
    }
    Ba(t2) {
      const i2 = new gt(this, t2);
      return this.oa.push(i2), this.Qt().Ta(this), i2;
    }
    Aa(t2) {
      const i2 = this.oa.indexOf(t2);
      -1 !== i2 && this.oa.splice(i2, 1), this.Qt().Ta(this);
    }
    za() {
      return this.oa;
    }
    bh() {
      return this.wa;
    }
    zt() {
      const t2 = this.La();
      return null === t2 ? null : { Wt: t2.Wt[3], Oa: t2.wt };
    }
    La() {
      const t2 = this.Qt().Bt().Be();
      if (null === t2) return null;
      const i2 = t2.Na();
      return this.qt.Hn(i2, 1);
    }
    Un() {
      return this.qt;
    }
    ba(t2) {
      this.ma = t2, this.va.clear();
    }
    Fa() {
      return !!this.Qt().Bt().N().enableConflation && this.Wa() > 1;
    }
    Rr(t2) {
      if (!this.Fa()) return;
      const i2 = this.Wa();
      if (!this.va.has(i2)) return;
      const n2 = "Custom" === this.wa, s2 = n2 && this.ma || void 0, e2 = n2 && this.Yh.Ha ? (t3) => {
        const i3 = t3, n3 = this.Yh.Ha(i3);
        return Array.isArray(n3) ? n3 : ["number" == typeof n3 ? n3 : 0];
      } : void 0, r2 = this.pa.Rr(this.qt.Eh(), t2, i2, s2, n2, e2), h2 = It();
      h2.ht(r2), this.va.set(i2, h2);
    }
    Ua() {
      const t2 = this.Qt().Bt().N().enableConflation;
      if ("Custom" === this.wa && null === this.ma) return this.qt;
      if (!t2) return this.qt;
      const i2 = this.Wa(), n2 = this.va.get(i2);
      if (n2) return n2;
      this.$a(i2);
      return this.va.get(i2) ?? this.qt;
    }
    ja(t2) {
      const i2 = this.qt.gh(t2);
      return null === i2 ? null : "Bar" === this.wa || "Candlestick" === this.wa || "Custom" === this.wa ? { jr: i2.Wt[0], qr: i2.Wt[1], Yr: i2.Wt[2], Kr: i2.Wt[3] } : i2.Wt[3];
    }
    qa(t2) {
      const i2 = [];
      Ut(this.fa, $t, "top", i2);
      const n2 = this.ua;
      return null !== n2 && n2.It() ? (null === this.da && n2.Ve() && (this.da = setTimeout((() => {
        this.da = null, this.Qt().Ya();
      }), 0)), n2.Ie(), i2.unshift(n2), i2) : i2;
    }
    jn() {
      const t2 = [];
      this.Ka() || t2.push(this._a), t2.push(this.Yh, this.dr);
      const i2 = this.oa.map(((t3) => t3.wr()));
      return t2.push(...i2), Ut(this.fa, $t, "normal", t2), t2;
    }
    Ga() {
      const t2 = this.Yh.Ga?.() ?? null;
      if (null === t2) return null;
      const i2 = [];
      this.Ka() || i2.push(this._a), i2.push(...t2.Za), Ut(this.fa, $t, "normal", i2);
      const n2 = [];
      n2.push(...t2.qa, this.dr);
      const s2 = this.oa.map(((t3) => t3.wr()));
      return n2.push(...s2), { Za: i2, qa: n2 };
    }
    Xa() {
      return this.Ja($t, "bottom");
    }
    Qa(t2) {
      return this.Ja(jt, t2);
    }
    tl(t2) {
      return this.Ja(qt, t2);
    }
    il(t2, i2) {
      return this.fa.map(((n2) => n2.Qs(t2, i2))).filter(((t3) => null !== t3));
    }
    cn() {
      return [this.pr, ...this.oa.map(((t2) => t2.gr()))];
    }
    qn(t2, i2) {
      if (i2 !== this.hn && !this.Ka()) return [];
      const n2 = [...this.mn];
      for (const t3 of this.oa) n2.push(t3.Mr());
      return this.fa.forEach(((t3) => {
        n2.push(...t3.qn());
      })), n2;
    }
    dn() {
      const t2 = [];
      return this.fa.forEach(((i2) => {
        t2.push(...i2.dn());
      })), t2;
    }
    la(t2, i2) {
      if (void 0 !== this.yn.autoscaleInfoProvider) {
        const n2 = this.yn.autoscaleInfoProvider((() => {
          const n3 = this.nl(t2, i2);
          return null === n3 ? null : n3.sr();
        }));
        return ft.er(n2);
      }
      return this.nl(t2, i2);
    }
    Kh() {
      const t2 = this.yn.priceFormat;
      return t2.base ?? 1 / t2.minMove;
    }
    sl() {
      return this.el;
    }
    Nn() {
      this.Yh.Pt();
      for (const t2 of this.mn) t2.Pt();
      for (const t2 of this.oa) t2.Pt();
      this.dr.Pt(), this._a.Pt(), this.ua?.Pt(), this.fa.forEach(((t2) => t2.Nn()));
    }
    Ft() {
      return a(super.Ft());
    }
    At(t2) {
      if (!(("Line" === this.wa || "Area" === this.wa || "Baseline" === this.wa) && this.yn.crosshairMarkerVisible)) return null;
      const i2 = this.qt.gh(t2);
      if (null === i2) return null;
      return { gt: i2.Wt[3], ft: this.rl(), Ht: this.hl(), Ot: this.al(), Lt: this.ll(t2) };
    }
    He() {
      return this.yn.title;
    }
    It() {
      return this.yn.visible;
    }
    ol(t2) {
      this.fa.push(new Ht(t2, this));
    }
    _l(t2) {
      this.fa = this.fa.filter(((i2) => i2.Qh() !== t2));
    }
    ul() {
      if ("Custom" === this.wa) return (t2) => this.Yh.Ha(t2);
    }
    cl() {
      if ("Custom" === this.wa) return (t2) => this.Yh.dl(t2);
    }
    fl() {
      return this.qt.zh();
    }
    Ka() {
      return !q(this.Ft().pl());
    }
    nl(t2, i2) {
      if (!c(t2) || !c(i2) || this.qt.Zi()) return null;
      const n2 = "Line" === this.wa || "Area" === this.wa || "Baseline" === this.wa || "Histogram" === this.wa ? [3] : [2, 1], s2 = this.qt.Bh(t2, i2, n2);
      let e2 = null !== s2 ? new dt(s2.Uh, s2.$h) : null, r2 = null;
      if ("Histogram" === this.bh()) {
        const t3 = this.yn.base, i3 = new dt(t3, t3);
        e2 = null !== e2 ? e2.Ss(i3) : i3;
      }
      return this.fa.forEach(((n3) => {
        const s3 = n3.la(t2, i2);
        if (s3?.priceRange) {
          const t3 = new dt(s3.priceRange.minValue, s3.priceRange.maxValue);
          e2 = null !== e2 ? e2.Ss(t3) : t3;
        }
        s3?.margins && (r2 = s3.margins);
      })), new ft(e2, r2);
    }
    rl() {
      switch (this.wa) {
        case "Line":
        case "Area":
        case "Baseline":
          return this.yn.crosshairMarkerRadius;
      }
      return 0;
    }
    hl() {
      switch (this.wa) {
        case "Line":
        case "Area":
        case "Baseline": {
          const t2 = this.yn.crosshairMarkerBorderColor;
          if (0 !== t2.length) return t2;
        }
      }
      return null;
    }
    al() {
      switch (this.wa) {
        case "Line":
        case "Area":
        case "Baseline":
          return this.yn.crosshairMarkerBorderWidth;
      }
      return 0;
    }
    ll(t2) {
      switch (this.wa) {
        case "Line":
        case "Area":
        case "Baseline": {
          const t3 = this.yn.crosshairMarkerBackgroundColor;
          if (0 !== t3.length) return t3;
        }
      }
      return this.Sa().Sh(t2).sh;
    }
    ga() {
      switch (this.yn.priceFormat.type) {
        case "custom": {
          const t2 = this.yn.priceFormat.formatter;
          this.el = { format: t2, formatTickmarks: this.yn.priceFormat.tickmarksFormatter ?? ((i2) => i2.map(t2)) };
          break;
        }
        case "volume":
          this.el = new Q(this.yn.priceFormat.precision);
          break;
        case "percent":
          this.el = new J(this.yn.priceFormat.precision);
          break;
        default: {
          const t2 = Math.pow(10, this.yn.priceFormat.precision);
          this.el = new X(t2, this.yn.priceFormat.minMove * t2);
        }
      }
      null !== this.hn && this.hn.vl();
    }
    Ja(t2, i2) {
      const n2 = [];
      return Ut(this.fa, t2, i2, n2), n2;
    }
    Wa() {
      const { ml: t2, wl: i2, gl: n2 } = this.Ml();
      return this.pa.Sr(t2, i2, n2);
    }
    Ml() {
      const t2 = this.Qt().Bt(), i2 = t2.ml(), n2 = window.devicePixelRatio || 1, s2 = t2.N().conflationThresholdFactor;
      return { ml: i2, wl: n2, gl: this.yn.conflationThresholdFactor ?? s2 ?? 1 };
    }
    bl(t2) {
      const i2 = this.qt.Eh();
      let n2;
      if ("Custom" === this.wa && null !== this.ma) {
        const s3 = this.ul();
        if (!s3) throw new Error(vt);
        n2 = this.pa.Cr(i2, t2, this.ma, true, ((t3) => s3(t3)));
      } else n2 = this.pa.Cr(i2, t2);
      const s2 = It();
      return s2.ht(n2), s2;
    }
    $a(t2) {
      const i2 = this.bl(t2);
      this.va.set(t2, i2);
    }
    Da(t2) {
      if ("Custom" === this.wa && (null === this.ma || !this.ul())) return;
      this.va.clear();
      const i2 = this.Qt().Bt().Sl();
      for (const n2 of i2) {
        const i3 = () => {
          this.xl(n2);
        }, s2 = "object" == typeof window && window || "object" == typeof self && self;
        s2?.yl?.Cl ? s2.yl.Cl((() => {
          i3();
        }), { se: t2 }) : Promise.resolve().then((() => i3()));
      }
    }
    xl(t2) {
      if (this.va.has(t2)) return;
      if (0 === this.qt.Eh().length) return;
      const i2 = this.bl(t2);
      this.va.set(t2, i2);
    }
  };
  var Gt = [3];
  var Zt = [0, 1, 2, 3];
  var Xt = class {
    constructor(t2) {
      this.yn = t2;
    }
    Pl(t2, i2, n2) {
      let s2 = t2;
      if (0 === this.yn.mode) return s2;
      const e2 = n2.kn(), r2 = e2.zt();
      if (null === r2) return s2;
      const h2 = e2.Nt(t2, r2), a2 = n2.kl().filter(((t3) => t3 instanceof Kt)).reduce(((t3, s3) => {
        if (n2.Gs(s3) || !s3.It()) return t3;
        const e3 = s3.Ft(), r3 = s3.Un();
        if (e3.Zi() || !r3.Le(i2)) return t3;
        const h3 = r3.gh(i2);
        if (null === h3) return t3;
        const a3 = l(s3.zt()), o3 = 3 === this.yn.mode ? Zt : Gt;
        return t3.concat(o3.map(((t4) => e3.Nt(h3.Wt[t4], a3.Wt))));
      }), []);
      if (0 === a2.length) return s2;
      a2.sort(((t3, i3) => Math.abs(t3 - h2) - Math.abs(i3 - h2)));
      const o2 = a2[0];
      return s2 = e2.Tn(o2, r2), s2;
    }
  };
  function Jt(t2, i2, n2) {
    return Math.min(Math.max(t2, i2), n2);
  }
  function Qt(t2, i2, n2) {
    return i2 - t2 <= n2;
  }
  function ti(t2) {
    const i2 = Math.ceil(t2);
    return i2 % 2 == 0 ? i2 - 1 : i2;
  }
  var ii = class extends y {
    constructor() {
      super(...arguments), this.qt = null;
    }
    ht(t2) {
      this.qt = t2;
    }
    et({ context: t2, bitmapSize: i2, horizontalPixelRatio: n2, verticalPixelRatio: e2 }) {
      if (null === this.qt) return;
      const r2 = Math.max(1, Math.floor(n2));
      t2.lineWidth = r2, (function(t3, i3) {
        t3.save(), t3.lineWidth % 2 && t3.translate(0.5, 0.5), i3(), t3.restore();
      })(t2, (() => {
        const h2 = a(this.qt);
        if (h2.Tl) {
          t2.strokeStyle = h2.Rl, s(t2, h2.Dl), t2.beginPath();
          for (const s2 of h2.Il) {
            const e3 = Math.round(s2.Vl * n2);
            t2.moveTo(e3, -r2), t2.lineTo(e3, i2.height + r2);
          }
          t2.stroke();
        }
        if (h2.El) {
          t2.strokeStyle = h2.Bl, s(t2, h2.Al), t2.beginPath();
          for (const n3 of h2.zl) {
            const s2 = Math.round(n3.Vl * e2);
            t2.moveTo(-r2, s2), t2.lineTo(i2.width + r2, s2);
          }
          t2.stroke();
        }
      }));
    }
  };
  var ni = class {
    constructor(t2) {
      this.Xt = new ii(), this.xt = true, this.yt = t2;
    }
    Pt() {
      this.xt = true;
    }
    Tt() {
      if (this.xt) {
        const t2 = this.yt.Qt().N().grid, i2 = { El: t2.horzLines.visible, Tl: t2.vertLines.visible, Bl: t2.horzLines.color, Rl: t2.vertLines.color, Al: t2.horzLines.style, Dl: t2.vertLines.style, zl: this.yt.kn().Ll(), Il: (this.yt.Qt().Bt().Ll() || []).map(((t3) => ({ Vl: t3.coord }))) };
        this.Xt.ht(i2), this.xt = false;
      }
      return this.Xt;
    }
  };
  var si = class {
    constructor(t2) {
      this.Yh = new ni(t2);
    }
    wr() {
      return this.Yh;
    }
  };
  var ei = { Ol: 4, Nl: 1e-4 };
  function ri(t2, i2) {
    const n2 = 100 * (t2 - i2) / i2;
    return i2 < 0 ? -n2 : n2;
  }
  function hi(t2, i2) {
    const n2 = ri(t2.Je(), i2), s2 = ri(t2.Qe(), i2);
    return new dt(n2, s2);
  }
  function ai(t2, i2) {
    const n2 = 100 * (t2 - i2) / i2 + 100;
    return i2 < 0 ? -n2 : n2;
  }
  function li(t2, i2) {
    const n2 = ai(t2.Je(), i2), s2 = ai(t2.Qe(), i2);
    return new dt(n2, s2);
  }
  function oi(t2, i2) {
    const n2 = Math.abs(t2);
    if (n2 < 1e-15) return 0;
    const s2 = Math.log10(n2 + i2.Nl) + i2.Ol;
    return t2 < 0 ? -s2 : s2;
  }
  function _i(t2, i2) {
    const n2 = Math.abs(t2);
    if (n2 < 1e-15) return 0;
    const s2 = Math.pow(10, n2 - i2.Ol) - i2.Nl;
    return t2 < 0 ? -s2 : s2;
  }
  function ui(t2, i2) {
    if (null === t2) return null;
    const n2 = oi(t2.Je(), i2), s2 = oi(t2.Qe(), i2);
    return new dt(n2, s2);
  }
  function ci(t2, i2) {
    if (null === t2) return null;
    const n2 = _i(t2.Je(), i2), s2 = _i(t2.Qe(), i2);
    return new dt(n2, s2);
  }
  function di(t2) {
    if (null === t2) return ei;
    const i2 = Math.abs(t2.Qe() - t2.Je());
    if (i2 >= 1 || i2 < 1e-15) return ei;
    const n2 = Math.ceil(Math.abs(Math.log10(i2))), s2 = ei.Ol + n2;
    return { Ol: s2, Nl: 1 / Math.pow(10, s2) };
  }
  var fi = class {
    constructor(t2, i2) {
      if (this.Fl = t2, this.Wl = i2, (function(t3) {
        if (t3 < 0) return false;
        if (t3 > 1e18) return true;
        for (let i3 = t3; i3 > 1; i3 /= 10) if (i3 % 10 != 0) return false;
        return true;
      })(this.Fl)) this.Hl = [2, 2.5, 2];
      else {
        this.Hl = [];
        for (let t3 = this.Fl; 1 !== t3; ) {
          if (t3 % 2 == 0) this.Hl.push(2), t3 /= 2;
          else {
            if (t3 % 5 != 0) throw new Error("unexpected base");
            this.Hl.push(2, 2.5), t3 /= 5;
          }
          if (this.Hl.length > 100) throw new Error("something wrong with base");
        }
      }
    }
    Ul(t2, i2, n2) {
      const s2 = 0 === this.Fl ? 0 : 1 / this.Fl;
      let e2 = Math.pow(10, Math.max(0, Math.ceil(Math.log10(t2 - i2)))), r2 = 0, h2 = this.Wl[0];
      for (; ; ) {
        const t3 = Qt(e2, s2, 1e-14) && e2 > s2 + 1e-14, i3 = Qt(e2, n2 * h2, 1e-14), a3 = Qt(e2, 1, 1e-14);
        if (!(t3 && i3 && a3)) break;
        e2 /= h2, h2 = this.Wl[++r2 % this.Wl.length];
      }
      if (e2 <= s2 + 1e-14 && (e2 = s2), e2 = Math.max(1, e2), this.Hl.length > 0 && (a2 = e2, l2 = 1, o2 = 1e-14, Math.abs(a2 - l2) < o2)) for (r2 = 0, h2 = this.Hl[0]; Qt(e2, n2 * h2, 1e-14) && e2 > s2 + 1e-14; ) e2 /= h2, h2 = this.Hl[++r2 % this.Hl.length];
      var a2, l2, o2;
      return e2;
    }
  };
  var pi = class {
    constructor(t2, i2, n2, s2) {
      this.$l = [], this.Ki = t2, this.Fl = i2, this.jl = n2, this.ql = s2;
    }
    Ul(t2, i2) {
      if (t2 < i2) throw new Error("high < low");
      const n2 = this.Ki.$t(), s2 = (t2 - i2) * this.Yl() / n2, e2 = new fi(this.Fl, [2, 2.5, 2]), r2 = new fi(this.Fl, [2, 2, 2.5]), h2 = new fi(this.Fl, [2.5, 2, 2]), a2 = [];
      return a2.push(e2.Ul(t2, i2, s2), r2.Ul(t2, i2, s2), h2.Ul(t2, i2, s2)), (function(t3) {
        if (t3.length < 1) throw Error("array is empty");
        let i3 = t3[0];
        for (let n3 = 1; n3 < t3.length; ++n3) t3[n3] < i3 && (i3 = t3[n3]);
        return i3;
      })(a2);
    }
    Kl() {
      const t2 = this.Ki, i2 = t2.zt();
      if (null === i2) return void (this.$l = []);
      const n2 = t2.$t(), s2 = this.jl(n2 - 1, i2), e2 = this.jl(0, i2), r2 = this.Ki.N().entireTextOnly ? this.Gl() / 2 : 0, h2 = r2, a2 = n2 - 1 - r2, l2 = Math.max(s2, e2), o2 = Math.min(s2, e2);
      if (l2 === o2) return void (this.$l = []);
      const _2 = this.Ul(l2, o2);
      if (this.Zl(i2, _2, l2, o2, h2, a2), t2.Xl() && this.Jl(_2, o2, l2)) {
        const t3 = this.Ki.Ql();
        this.io(i2, _2, h2, a2, t3, 2 * t3);
      }
      const u2 = this.$l.map(((t3) => t3.no)), c2 = this.Ki.so(u2);
      for (let t3 = 0; t3 < this.$l.length; t3++) this.$l[t3].eo = c2[t3];
    }
    Ll() {
      return this.$l;
    }
    Gl() {
      return this.Ki.P();
    }
    Yl() {
      return Math.ceil(this.Gl() * this.Ki.N().tickMarkDensity);
    }
    Zl(t2, i2, n2, s2, e2, r2) {
      const h2 = this.$l, a2 = this.Ki;
      let l2 = n2 % i2;
      l2 += l2 < 0 ? i2 : 0;
      const o2 = n2 >= s2 ? 1 : -1;
      let _2 = null, u2 = 0;
      for (let c2 = n2 - l2; c2 > s2; c2 -= i2) {
        const n3 = this.ql(c2, t2, true);
        null !== _2 && Math.abs(n3 - _2) < this.Yl() || (n3 < e2 || n3 > r2 || (u2 < h2.length ? (h2[u2].Vl = n3, h2[u2].eo = a2.ro(c2), h2[u2].no = c2) : h2.push({ Vl: n3, eo: a2.ro(c2), no: c2 }), u2++, _2 = n3, a2.ho() && (i2 = this.Ul(c2 * o2, s2))));
      }
      h2.length = u2;
    }
    io(t2, i2, n2, s2, e2, r2) {
      const h2 = this.$l, a2 = this.ao(t2, n2, e2, r2), l2 = this.ao(t2, s2, -r2, -e2), o2 = this.ql(0, t2, true) - this.ql(i2, t2, true);
      h2.length > 0 && h2[0].Vl - a2.Vl < o2 / 2 && h2.shift(), h2.length > 0 && l2.Vl - h2[h2.length - 1].Vl < o2 / 2 && h2.pop(), h2.unshift(a2), h2.push(l2);
    }
    ao(t2, i2, n2, s2) {
      const e2 = (n2 + s2) / 2, r2 = this.jl(i2 + n2, t2), h2 = this.jl(i2 + s2, t2), a2 = Math.min(r2, h2), l2 = Math.max(r2, h2), o2 = Math.max(0.1, this.Ul(l2, a2)), _2 = this.jl(i2 + e2, t2), u2 = _2 - _2 % o2, c2 = this.ql(u2, t2, true);
      return { eo: this.Ki.ro(u2), Vl: c2, no: u2 };
    }
    Jl(t2, i2, n2) {
      let s2 = l(this.Ki.ar());
      return this.Ki.ho() && (s2 = ci(s2, this.Ki.lo())), s2.Je() - i2 < t2 && n2 - s2.Qe() < t2;
    }
  };
  function vi(t2) {
    return t2.slice().sort(((t3, i2) => a(t3.ln()) - a(i2.ln())));
  }
  var mi;
  !(function(t2) {
    t2[t2.Normal = 0] = "Normal", t2[t2.Logarithmic = 1] = "Logarithmic", t2[t2.Percentage = 2] = "Percentage", t2[t2.IndexedTo100 = 3] = "IndexedTo100";
  })(mi || (mi = {}));
  var wi = new J();
  var gi = new X(100, 1);
  var Mi = class {
    constructor(t2, i2, n2, s2, e2) {
      this.oo = 0, this._o = null, this.rr = null, this.uo = null, this.co = { do: false, fo: null }, this.po = false, this.vo = 0, this.mo = 0, this.wo = new o(), this.Mo = new o(), this.bo = [], this.So = null, this.xo = null, this.Co = null, this.yo = null, this.Po = null, this.el = gi, this.ko = di(null), this.To = t2, this.yn = i2, this.Ro = n2, this.Do = s2, this.Io = e2, this.Vo = new pi(this, 100, this.Eo.bind(this), this.Bo.bind(this));
    }
    pl() {
      return this.To;
    }
    N() {
      return this.yn;
    }
    vr(t2) {
      if (_(this.yn, t2), this.vl(), void 0 !== t2.mode && this.Ao({ _e: t2.mode }), void 0 !== t2.scaleMargins) {
        const i2 = h(t2.scaleMargins.top), n2 = h(t2.scaleMargins.bottom);
        if (i2 < 0 || i2 > 1) throw new Error(`Invalid top margin - expect value between 0 and 1, given=${i2}`);
        if (n2 < 0 || n2 > 1) throw new Error(`Invalid bottom margin - expect value between 0 and 1, given=${n2}`);
        if (i2 + n2 > 1) throw new Error(`Invalid margins - sum of margins must be less than 1, given=${i2 + n2}`);
        this.zo(), this.Co = null;
      }
    }
    Lo() {
      return this.yn.autoScale;
    }
    Oo() {
      return this.po;
    }
    ho() {
      return 1 === this.yn.mode;
    }
    je() {
      return 2 === this.yn.mode;
    }
    No() {
      return 3 === this.yn.mode;
    }
    lo() {
      return this.ko;
    }
    _e() {
      return { hs: this.yn.autoScale, Fo: this.yn.invertScale, _e: this.yn.mode };
    }
    Ao(t2) {
      const i2 = this._e();
      let n2 = null;
      void 0 !== t2.hs && (this.yn.autoScale = t2.hs), void 0 !== t2._e && (this.yn.mode = t2._e, 2 !== t2._e && 3 !== t2._e || (this.yn.autoScale = true), this.co.do = false), 1 === i2._e && t2._e !== i2._e && (!(function(t3, i3) {
        if (null === t3) return false;
        const n3 = _i(t3.Je(), i3), s3 = _i(t3.Qe(), i3);
        return isFinite(n3) && isFinite(s3);
      })(this.rr, this.ko) ? this.yn.autoScale = true : (n2 = ci(this.rr, this.ko), null !== n2 && this.Wo(n2))), 1 === t2._e && t2._e !== i2._e && (n2 = ui(this.rr, this.ko), null !== n2 && this.Wo(n2));
      const s2 = i2._e !== this.yn.mode;
      s2 && (2 === i2._e || this.je()) && this.vl(), s2 && (3 === i2._e || this.No()) && this.vl(), void 0 !== t2.Fo && i2.Fo !== t2.Fo && (this.yn.invertScale = t2.Fo, this.Ho()), this.Mo.p(i2, this._e());
    }
    Uo() {
      return this.Mo;
    }
    P() {
      return this.Ro.fontSize;
    }
    $t() {
      return this.oo;
    }
    $o(t2) {
      this.oo !== t2 && (this.oo = t2, this.zo(), this.Co = null);
    }
    jo() {
      if (this._o) return this._o;
      const t2 = this.$t() - this.qo() - this.Yo();
      return this._o = t2, t2;
    }
    ar() {
      return this.Ko(), this.rr;
    }
    Wo(t2, i2) {
      const n2 = this.rr;
      (i2 || null === n2 && null !== t2 || null !== n2 && !n2.Ze(t2)) && (this.Co = null, this.rr = t2);
    }
    Go(t2) {
      this.Wo(t2), this.Zo(null !== t2);
    }
    Zi() {
      return this.Ko(), 0 === this.oo || !this.rr || this.rr.Zi();
    }
    Xo(t2) {
      return this.Fo() ? t2 : this.$t() - 1 - t2;
    }
    Nt(t2, i2) {
      return this.je() ? t2 = ri(t2, i2) : this.No() && (t2 = ai(t2, i2)), this.Bo(t2, i2);
    }
    Jo(t2, i2, n2) {
      this.Ko();
      const s2 = this.Yo(), e2 = a(this.ar()), r2 = e2.Je(), h2 = e2.Qe(), l2 = this.jo() - 1, o2 = this.Fo(), _2 = l2 / (h2 - r2), u2 = void 0 === n2 ? 0 : n2.from, c2 = void 0 === n2 ? t2.length : n2.to, d2 = this.Qo();
      for (let n3 = u2; n3 < c2; n3++) {
        const e3 = t2[n3], h3 = e3.gt;
        if (isNaN(h3)) continue;
        let a2 = h3;
        null !== d2 && (a2 = d2(e3.gt, i2));
        const l3 = s2 + _2 * (a2 - r2), u3 = o2 ? l3 : this.oo - 1 - l3;
        e3.ut = u3;
      }
    }
    t_(t2, i2, n2) {
      this.Ko();
      const s2 = this.Yo(), e2 = a(this.ar()), r2 = e2.Je(), h2 = e2.Qe(), l2 = this.jo() - 1, o2 = this.Fo(), _2 = l2 / (h2 - r2), u2 = void 0 === n2 ? 0 : n2.from, c2 = void 0 === n2 ? t2.length : n2.to, d2 = this.Qo();
      for (let n3 = u2; n3 < c2; n3++) {
        const e3 = t2[n3];
        let h3 = e3.jr, a2 = e3.qr, l3 = e3.Yr, u3 = e3.Kr;
        null !== d2 && (h3 = d2(e3.jr, i2), a2 = d2(e3.qr, i2), l3 = d2(e3.Yr, i2), u3 = d2(e3.Kr, i2));
        let c3 = s2 + _2 * (h3 - r2), f2 = o2 ? c3 : this.oo - 1 - c3;
        e3.i_ = f2, c3 = s2 + _2 * (a2 - r2), f2 = o2 ? c3 : this.oo - 1 - c3, e3.n_ = f2, c3 = s2 + _2 * (l3 - r2), f2 = o2 ? c3 : this.oo - 1 - c3, e3.s_ = f2, c3 = s2 + _2 * (u3 - r2), f2 = o2 ? c3 : this.oo - 1 - c3, e3.e_ = f2;
      }
    }
    Tn(t2, i2) {
      const n2 = this.Eo(t2, i2);
      return this.r_(n2, i2);
    }
    r_(t2, i2) {
      let n2 = t2;
      return this.je() ? n2 = (function(t3, i3) {
        return i3 < 0 && (t3 = -t3), t3 / 100 * i3 + i3;
      })(n2, i2) : this.No() && (n2 = (function(t3, i3) {
        return t3 -= 100, i3 < 0 && (t3 = -t3), t3 / 100 * i3 + i3;
      })(n2, i2)), n2;
    }
    kl() {
      return this.bo;
    }
    Dt() {
      return this.xo || (this.xo = vi(this.bo)), this.xo;
    }
    h_(t2) {
      -1 === this.bo.indexOf(t2) && (this.bo.push(t2), this.vl(), this.a_());
    }
    l_(t2) {
      const i2 = this.bo.indexOf(t2);
      if (-1 === i2) throw new Error("source is not attached to scale");
      this.bo.splice(i2, 1), 0 === this.bo.length && (this.Ao({ hs: true }), this.Wo(null)), this.vl(), this.a_();
    }
    zt() {
      let t2 = null;
      for (const i2 of this.bo) {
        const n2 = i2.zt();
        null !== n2 && ((null === t2 || n2.Oa < t2.Oa) && (t2 = n2));
      }
      return null === t2 ? null : t2.Wt;
    }
    Fo() {
      return this.yn.invertScale;
    }
    Ll() {
      const t2 = null === this.zt();
      if (null !== this.Co && (t2 || this.Co.o_ === t2)) return this.Co.Ll;
      this.Vo.Kl();
      const i2 = this.Vo.Ll();
      return this.Co = { Ll: i2, o_: t2 }, this.wo.p(), i2;
    }
    __() {
      return this.wo;
    }
    u_(t2) {
      this.je() || this.No() || null === this.yo && null === this.uo && (this.Zi() || (this.yo = this.oo - t2, this.uo = a(this.ar()).Xe()));
    }
    c_(t2) {
      if (this.je() || this.No()) return;
      if (null === this.yo) return;
      this.Ao({ hs: false }), (t2 = this.oo - t2) < 0 && (t2 = 0);
      let i2 = (this.yo + 0.2 * (this.oo - 1)) / (t2 + 0.2 * (this.oo - 1));
      const n2 = a(this.uo).Xe();
      i2 = Math.max(i2, 0.1), n2.ir(i2), this.Wo(n2);
    }
    d_() {
      this.je() || this.No() || (this.yo = null, this.uo = null);
    }
    f_(t2) {
      this.Lo() || null === this.Po && null === this.uo && (this.Zi() || (this.Po = t2, this.uo = a(this.ar()).Xe()));
    }
    p_(t2) {
      if (this.Lo()) return;
      if (null === this.Po) return;
      const i2 = a(this.ar()).tr() / (this.jo() - 1);
      let n2 = t2 - this.Po;
      this.Fo() && (n2 *= -1);
      const s2 = n2 * i2, e2 = a(this.uo).Xe();
      e2.nr(s2), this.Wo(e2, true), this.Co = null;
    }
    v_() {
      this.Lo() || null !== this.Po && (this.Po = null, this.uo = null);
    }
    sl() {
      return this.el || this.vl(), this.el;
    }
    Ji(t2, i2) {
      switch (this.yn.mode) {
        case 2:
          return this.m_(ri(t2, i2));
        case 3:
          return this.sl().format(ai(t2, i2));
        default:
          return this.cr(t2);
      }
    }
    ro(t2) {
      switch (this.yn.mode) {
        case 2:
          return this.m_(t2);
        case 3:
          return this.sl().format(t2);
        default:
          return this.cr(t2);
      }
    }
    so(t2) {
      switch (this.yn.mode) {
        case 2:
          return this.w_(t2);
        case 3:
          return this.sl().formatTickmarks(t2);
        default:
          return this.g_(t2);
      }
    }
    xa(t2) {
      return this.cr(t2, a(this.So).sl());
    }
    Ca(t2, i2) {
      return t2 = ri(t2, i2), this.m_(t2, wi);
    }
    M_() {
      return this.bo;
    }
    b_(t2) {
      this.co = { fo: t2, do: false };
    }
    Nn() {
      this.bo.forEach(((t2) => t2.Nn()));
    }
    Xl() {
      return this.yn.ensureEdgeTickMarksVisible && this.Lo();
    }
    Ql() {
      return this.P() / 2;
    }
    vl() {
      this.Co = null;
      let t2 = 1 / 0;
      this.So = null;
      for (const i3 of this.bo) i3.ln() < t2 && (t2 = i3.ln(), this.So = i3);
      let i2 = 100;
      null !== this.So && (i2 = Math.round(this.So.Kh())), this.el = gi, this.je() ? (this.el = wi, i2 = 100) : this.No() ? (this.el = new X(100, 1), i2 = 100) : null !== this.So && (this.el = this.So.sl()), this.Vo = new pi(this, i2, this.Eo.bind(this), this.Bo.bind(this)), this.Vo.Kl();
    }
    a_() {
      this.xo = null;
    }
    S_() {
      return null === this.So || this.je() || this.No() ? 1 : 1 / this.So.Kh();
    }
    Xi() {
      return this.Io;
    }
    Zo(t2) {
      this.po = t2;
    }
    qo() {
      return this.Fo() ? this.yn.scaleMargins.bottom * this.$t() + this.mo : this.yn.scaleMargins.top * this.$t() + this.vo;
    }
    Yo() {
      return this.Fo() ? this.yn.scaleMargins.top * this.$t() + this.vo : this.yn.scaleMargins.bottom * this.$t() + this.mo;
    }
    Ko() {
      this.co.do || (this.co.do = true, this.x_());
    }
    zo() {
      this._o = null;
    }
    Bo(t2, i2) {
      if (this.Ko(), this.Zi()) return 0;
      t2 = this.ho() && t2 ? oi(t2, this.ko) : t2;
      const n2 = a(this.ar()), s2 = this.Yo() + (this.jo() - 1) * (t2 - n2.Je()) / n2.tr();
      return this.Xo(s2);
    }
    Eo(t2, i2) {
      if (this.Ko(), this.Zi()) return 0;
      const n2 = this.Xo(t2), s2 = a(this.ar()), e2 = s2.Je() + s2.tr() * ((n2 - this.Yo()) / (this.jo() - 1));
      return this.ho() ? _i(e2, this.ko) : e2;
    }
    Ho() {
      this.Co = null, this.Vo.Kl();
    }
    x_() {
      if (this.Oo() && !this.Lo()) return;
      const t2 = this.co.fo;
      if (null === t2) return;
      let i2 = null;
      const n2 = this.M_();
      let s2 = 0, e2 = 0;
      for (const r3 of n2) {
        if (!r3.It()) continue;
        const n3 = r3.zt();
        if (null === n3) continue;
        const h3 = r3.la(t2.Na(), t2.bi());
        let l2 = h3 && h3.ar();
        if (null !== l2) {
          switch (this.yn.mode) {
            case 1:
              l2 = ui(l2, this.ko);
              break;
            case 2:
              l2 = hi(l2, n3.Wt);
              break;
            case 3:
              l2 = li(l2, n3.Wt);
          }
          if (i2 = null === i2 ? l2 : i2.Ss(a(l2)), null !== h3) {
            const t3 = h3.lr();
            null !== t3 && (s2 = Math.max(s2, t3.above), e2 = Math.max(e2, t3.below));
          }
        }
      }
      if (this.Xl() && (s2 = Math.max(s2, this.Ql()), e2 = Math.max(e2, this.Ql())), s2 === this.vo && e2 === this.mo || (this.vo = s2, this.mo = e2, this.Co = null, this.zo()), null !== i2) {
        if (i2.Je() === i2.Qe()) {
          const t3 = 5 * this.S_();
          this.ho() && (i2 = ci(i2, this.ko)), i2 = new dt(i2.Je() - t3, i2.Qe() + t3), this.ho() && (i2 = ui(i2, this.ko));
        }
        if (this.ho()) {
          const t3 = ci(i2, this.ko), n3 = di(t3);
          if (r2 = n3, h2 = this.ko, r2.Ol !== h2.Ol || r2.Nl !== h2.Nl) {
            const s3 = null !== this.uo ? ci(this.uo, this.ko) : null;
            this.ko = n3, i2 = ui(t3, n3), null !== s3 && (this.uo = ui(s3, n3));
          }
        }
        this.Wo(i2);
      } else null === this.rr && (this.Wo(new dt(-0.5, 0.5)), this.ko = di(null));
      var r2, h2;
    }
    Qo() {
      return this.je() ? ri : this.No() ? ai : this.ho() ? (t2) => oi(t2, this.ko) : null;
    }
    C_(t2, i2, n2) {
      return void 0 === i2 ? (void 0 === n2 && (n2 = this.sl()), n2.format(t2)) : i2(t2);
    }
    y_(t2, i2, n2) {
      return void 0 === i2 ? (void 0 === n2 && (n2 = this.sl()), n2.formatTickmarks(t2)) : i2(t2);
    }
    cr(t2, i2) {
      return this.C_(t2, this.Do.priceFormatter, i2);
    }
    g_(t2, i2) {
      const n2 = this.Do.priceFormatter;
      return this.y_(t2, this.Do.tickmarksPriceFormatter ?? (n2 ? (t3) => t3.map(n2) : void 0), i2);
    }
    m_(t2, i2) {
      return this.C_(t2, this.Do.percentageFormatter, i2);
    }
    w_(t2, i2) {
      const n2 = this.Do.percentageFormatter;
      return this.y_(t2, this.Do.tickmarksPercentageFormatter ?? (n2 ? (t3) => t3.map(n2) : void 0), i2);
    }
  };
  function bi(t2) {
    return t2 instanceof Kt;
  }
  var Si = class {
    constructor(t2, i2) {
      this.bo = [], this.P_ = /* @__PURE__ */ new Map(), this.oo = 0, this.k_ = 0, this.T_ = 1, this.xo = null, this.R_ = null, this.D_ = false, this.I_ = new o(), this.fa = [], this.ia = t2, this.sn = i2, this.V_ = new si(this);
      const n2 = i2.N();
      this.E_ = this.B_("left", n2.leftPriceScale), this.A_ = this.B_("right", n2.rightPriceScale), this.E_.Uo().i(this.z_.bind(this, this.E_), this), this.A_.Uo().i(this.z_.bind(this, this.A_), this), this.L_(n2);
    }
    L_(t2) {
      if (t2.leftPriceScale && this.E_.vr(t2.leftPriceScale), t2.rightPriceScale && this.A_.vr(t2.rightPriceScale), t2.localization && (this.E_.vl(), this.A_.vl()), t2.overlayPriceScales) {
        const i2 = Array.from(this.P_.values());
        for (const n2 of i2) {
          const i3 = a(n2[0].Ft());
          i3.vr(t2.overlayPriceScales), t2.localization && i3.vl();
        }
      }
    }
    O_(t2) {
      switch (t2) {
        case "left":
          return this.E_;
        case "right":
          return this.A_;
      }
      return this.P_.has(t2) ? h(this.P_.get(t2))[0].Ft() : null;
    }
    m() {
      this.Qt().N_().u(this), this.E_.Uo().u(this), this.A_.Uo().u(this), this.bo.forEach(((t2) => {
        t2.m && t2.m();
      })), this.fa = this.fa.filter(((t2) => {
        const i2 = t2.Qh();
        return i2.detached && i2.detached(), false;
      })), this.I_.p();
    }
    F_() {
      return this.T_;
    }
    W_(t2) {
      this.T_ = t2;
    }
    Qt() {
      return this.sn;
    }
    nn() {
      return this.k_;
    }
    $t() {
      return this.oo;
    }
    H_(t2) {
      this.k_ = t2, this.U_();
    }
    $o(t2) {
      this.oo = t2, this.E_.$o(t2), this.A_.$o(t2), this.bo.forEach(((i2) => {
        if (this.Gs(i2)) {
          const n2 = i2.Ft();
          null !== n2 && n2.$o(t2);
        }
      })), this.U_();
    }
    j_(t2) {
      this.D_ = t2;
    }
    q_() {
      return this.D_;
    }
    Y_() {
      return this.bo.filter(bi);
    }
    kl() {
      return this.bo;
    }
    Gs(t2) {
      const i2 = t2.Ft();
      return null === i2 || this.E_ !== i2 && this.A_ !== i2;
    }
    h_(t2, i2, n2) {
      this.K_(t2, i2, n2 ? t2.ln() : this.bo.length);
    }
    l_(t2, i2) {
      const n2 = this.bo.indexOf(t2);
      r(-1 !== n2, "removeDataSource: invalid data source"), this.bo.splice(n2, 1), i2 || this.bo.forEach(((t3, i3) => t3._n(i3)));
      const s2 = a(t2.Ft()).pl();
      if (this.P_.has(s2)) {
        const i3 = h(this.P_.get(s2)), n3 = i3.indexOf(t2);
        -1 !== n3 && (i3.splice(n3, 1), 0 === i3.length && this.P_.delete(s2));
      }
      const e2 = t2.Ft();
      e2 && e2.kl().indexOf(t2) >= 0 && (e2.l_(t2), this.G_(e2)), this.Z_();
    }
    Xs(t2) {
      return t2 === this.E_ ? "left" : t2 === this.A_ ? "right" : "overlay";
    }
    X_() {
      return this.E_;
    }
    J_() {
      return this.A_;
    }
    Q_(t2, i2) {
      t2.u_(i2);
    }
    tu(t2, i2) {
      t2.c_(i2), this.U_();
    }
    iu(t2) {
      t2.d_();
    }
    nu(t2, i2) {
      t2.f_(i2);
    }
    su(t2, i2) {
      t2.p_(i2), this.U_();
    }
    eu(t2) {
      t2.v_();
    }
    U_() {
      this.bo.forEach(((t2) => {
        t2.Nn();
      }));
    }
    kn() {
      const [t2, i2] = this.ru();
      let n2 = null;
      return t2.N().visible && 0 !== t2.kl().length ? n2 = t2 : i2.N().visible && 0 !== i2.kl().length ? n2 = i2 : 0 !== this.bo.length && (n2 = this.bo[0].Ft()), null === n2 && (n2 = this.Zs() ?? t2), n2;
    }
    Zs() {
      const [t2, i2] = this.ru();
      return t2.N().visible ? t2 : i2.N().visible ? i2 : null;
    }
    G_(t2) {
      null !== t2 && t2.Lo() && this.hu(t2);
    }
    au(t2) {
      const i2 = this.ia.Be();
      t2.Ao({ hs: true }), null !== i2 && t2.b_(i2), this.U_();
    }
    lu() {
      this.hu(this.E_), this.hu(this.A_);
    }
    ou() {
      this.G_(this.E_), this.G_(this.A_), this.bo.forEach(((t2) => {
        this.Gs(t2) && this.G_(t2.Ft());
      })), this.U_(), this.sn.mr();
    }
    Dt() {
      return null === this.xo && (this.xo = vi(this.bo)), this.xo;
    }
    _u() {
      const t2 = this.Dt(), i2 = this.sn.cu()?.uu, n2 = this.sn.N().hoveredSeriesOnTop, s2 = this.R_;
      if (null !== s2 && s2.Kh === t2 && s2.du === i2 && s2.fu === n2) return s2.pu;
      const e2 = (function(t3, i3, n3) {
        if (!n3) return t3;
        const s3 = t3.indexOf(i3);
        if (-1 === s3 || s3 === t3.length - 1) return t3;
        const e3 = [];
        for (let i4 = 0; i4 < t3.length; i4++) i4 !== s3 && e3.push(t3[i4]);
        return e3.push(t3[s3]), e3;
      })(t2, i2, n2);
      return this.R_ = { Kh: t2, du: i2, fu: n2, pu: e2 }, e2;
    }
    vu(t2, i2) {
      i2 = Jt(i2, 0, this.bo.length - 1);
      const n2 = this.bo.indexOf(t2);
      r(-1 !== n2, "setSeriesOrder: invalid data source"), this.bo.splice(n2, 1), this.bo.splice(i2, 0, t2), this.bo.forEach(((t3, i3) => t3._n(i3))), this.Z_();
      for (const t3 of [this.E_, this.A_]) t3.a_(), t3.vl();
      this.sn.mr();
    }
    Vt() {
      return this.Dt().filter(bi);
    }
    mu() {
      return this.I_;
    }
    wu() {
      return this.V_;
    }
    ol(t2) {
      this.fa.push(new zt(t2));
    }
    _l(t2) {
      this.fa = this.fa.filter(((i2) => i2.Qh() !== t2)), t2.detached && t2.detached(), this.sn.mr();
    }
    gu() {
      return this.fa;
    }
    il(t2, i2) {
      return this.fa.map(((n2) => n2.Qs(t2, i2))).filter(((t3) => null !== t3));
    }
    hu(t2) {
      const i2 = t2.M_();
      if (i2 && i2.length > 0 && !this.ia.Zi()) {
        const i3 = this.ia.Be();
        null !== i3 && t2.b_(i3);
      }
      t2.Nn();
    }
    K_(t2, i2, n2) {
      let s2 = this.O_(i2);
      if (null === s2 && (s2 = this.B_(i2, this.sn.N().overlayPriceScales)), this.bo.splice(n2, 0, t2), !q(i2)) {
        const n3 = this.P_.get(i2) || [];
        n3.push(t2), this.P_.set(i2, n3);
      }
      t2._n(n2), s2.h_(t2), t2.un(s2), this.G_(s2), this.Z_();
    }
    Z_() {
      this.xo = null, this.R_ = null;
    }
    ru() {
      return "left" === this.sn.N().defaultVisiblePriceScaleId ? [this.E_, this.A_] : [this.A_, this.E_];
    }
    z_(t2, i2, n2) {
      i2._e !== n2._e && this.hu(t2);
    }
    B_(t2, i2) {
      const n2 = { visible: true, autoScale: true, ...p(i2) }, s2 = new Mi(t2, n2, this.sn.N().layout, this.sn.N().localization, this.sn.Xi());
      return s2.$o(this.$t()), s2;
    }
  };
  function xi(t2, i2) {
    return null === i2 || (2 === t2.se && 2 !== i2.se || (2 !== i2.se || 2 === t2.se) && (t2.ne !== i2.ne && t2.ne < i2.ne));
  }
  function Ci(t2) {
    return { te: t2.te, ie: t2.ie };
  }
  function yi(t2) {
    return { ne: t2.distance ?? 0, se: t2.hitTestPriority ?? ("marker" === t2.itemType ? 2 : 0), ee: t2.itemType ?? "primitive", Mu: t2.cursorStyle, te: t2.externalId };
  }
  function Pi(t2) {
    return { uu: t2.uu, bu: Ci(t2.Su), Mu: t2.Su.Mu, ee: t2.Su.ee ?? "primitive" };
  }
  function ki(t2, i2, n2, s2) {
    let e2 = null;
    for (const r2 of t2) {
      let t3 = r2.Qs?.(i2, n2, s2) ?? null;
      if (null === t3) {
        const e3 = r2.Tt(s2);
        t3 = null !== e3 && e3.Qs ? e3.Qs(i2, n2) : null;
      }
      if (null !== t3) {
        const i3 = { xu: r2, Su: t3 };
        (null === e2 || xi(i3.Su, e2.Su)) && (e2 = i3);
      }
    }
    return e2;
  }
  function Ti(t2) {
    return void 0 !== t2.jn;
  }
  function Ri(t2, i2, n2) {
    const s2 = [t2, ...t2.Dt()].reverse(), e2 = (function(t3, i3, n3) {
      let s3, e3, r3;
      for (const l2 of t3) {
        const t4 = l2.il?.(i3, n3) ?? [];
        for (const i4 of t4) {
          const t5 = yi(i4);
          h3 = i4.zOrder, a2 = s3?.zOrder, (!a2 || "top" === h3 && "top" !== a2 || "normal" === h3 && "bottom" === a2 || i4.zOrder === s3?.zOrder && void 0 !== e3 && xi(t5, e3) || i4.zOrder === s3?.zOrder && void 0 === e3) && (s3 = i4, e3 = t5, r3 = l2);
        }
      }
      var h3, a2;
      return s3 && r3 && e3 ? { Su: e3, Cu: s3, uu: r3 } : null;
    })(s2, i2, n2);
    if ("top" === e2?.Cu.zOrder) return Pi(e2);
    let r2 = null, h2 = null;
    for (const a2 of s2) {
      if (e2 && e2.uu === a2 && "bottom" !== e2.Cu.zOrder && !e2.Cu.isBackground) return r2 ?? Pi(e2);
      if (Ti(a2)) {
        const s3 = ki(a2.jn(t2), i2, n2, t2);
        if (null !== s3) {
          const t3 = { uu: a2, xu: s3.xu, bu: Ci(s3.Su), Mu: s3.Su.Mu, ee: s3.Su.ee ?? "primitive" };
          (null === r2 || xi(s3.Su, h2)) && (r2 = t3, h2 = s3.Su);
        }
      }
      if (e2 && e2.uu === a2 && "bottom" !== e2.Cu.zOrder && e2.Cu.isBackground) return r2 ?? Pi(e2);
    }
    return null !== r2 ? r2 : e2?.Cu ? Pi(e2) : null;
  }
  var Di = class {
    constructor(t2, i2, n2 = 50) {
      this.Vs = 0, this.Es = 1, this.Bs = 1, this.zs = /* @__PURE__ */ new Map(), this.As = /* @__PURE__ */ new Map(), this.yu = t2, this.Pu = i2, this.Ls = n2;
    }
    ku(t2) {
      const i2 = t2.time, n2 = this.Pu.cacheKey(i2), s2 = this.zs.get(n2);
      if (void 0 !== s2) return s2.Tu;
      if (this.Vs === this.Ls) {
        const t3 = this.As.get(this.Bs);
        this.As.delete(this.Bs), this.zs.delete(h(t3)), this.Bs++, this.Vs--;
      }
      const e2 = this.yu(t2);
      return this.zs.set(n2, { Tu: e2, Ws: this.Es }), this.As.set(this.Es, n2), this.Vs++, this.Es++, e2;
    }
  };
  var Ii = class {
    constructor(t2, i2) {
      r(t2 <= i2, "right should be >= left"), this.Ru = t2, this.Du = i2;
    }
    Na() {
      return this.Ru;
    }
    bi() {
      return this.Du;
    }
    Iu() {
      return this.Du - this.Ru + 1;
    }
    Le(t2) {
      return this.Ru <= t2 && t2 <= this.Du;
    }
    Ze(t2) {
      return this.Ru === t2.Na() && this.Du === t2.bi();
    }
  };
  function Vi(t2, i2) {
    return null === t2 || null === i2 ? t2 === i2 : t2.Ze(i2);
  }
  var Ei = class {
    constructor() {
      this.Vu = /* @__PURE__ */ new Map(), this.zs = null, this.Eu = false;
    }
    Bu(t2) {
      this.Eu = t2, this.zs = null;
    }
    Au(t2, i2) {
      this.zu(i2), this.zs = null;
      for (let n2 = i2; n2 < t2.length; ++n2) {
        const i3 = t2[n2];
        let s2 = this.Vu.get(i3.timeWeight);
        void 0 === s2 && (s2 = [], this.Vu.set(i3.timeWeight, s2)), s2.push({ index: n2, time: i3.time, weight: i3.timeWeight, originalTime: i3.originalTime });
      }
    }
    Lu(t2, i2, n2, s2, e2) {
      const r2 = Math.ceil(i2 / t2);
      return null !== this.zs && this.zs.Ou === r2 && e2 === this.zs.Nu && n2 === this.zs.Fu || (this.zs = { Nu: e2, Fu: n2, Ll: this.Wu(r2, n2, s2), Ou: r2 }), this.zs.Ll;
    }
    zu(t2) {
      if (0 === t2) return void this.Vu.clear();
      const i2 = [];
      this.Vu.forEach(((n2, s2) => {
        t2 <= n2[0].index ? i2.push(s2) : n2.splice(yt(n2, t2, ((i3) => i3.index < t2)), 1 / 0);
      }));
      for (const t3 of i2) this.Vu.delete(t3);
    }
    Wu(t2, i2, n2) {
      let s2 = [];
      const e2 = (t3) => !i2 || n2.has(t3.index);
      for (const i3 of Array.from(this.Vu.keys()).sort(((t3, i4) => i4 - t3))) {
        if (!this.Vu.get(i3)) continue;
        const n3 = s2;
        s2 = [];
        const r2 = n3.length;
        let a2 = 0;
        const l2 = h(this.Vu.get(i3)), o2 = l2.length;
        let _2 = 1 / 0, u2 = -1 / 0;
        for (let i4 = 0; i4 < o2; i4++) {
          const h2 = l2[i4], o3 = h2.index;
          for (; a2 < r2; ) {
            const t3 = n3[a2], i5 = t3.index;
            if (!(i5 < o3 && e2(t3))) {
              _2 = i5;
              break;
            }
            a2++, s2.push(t3), u2 = i5, _2 = 1 / 0;
          }
          if (_2 - o3 >= t2 && o3 - u2 >= t2 && e2(h2)) s2.push(h2), u2 = o3;
          else if (this.Eu) return n3;
        }
        for (; a2 < r2; a2++) e2(n3[a2]) && s2.push(n3[a2]);
      }
      return s2;
    }
  };
  var Bi = class _Bi {
    constructor(t2) {
      this.Hu = t2;
    }
    Uu() {
      return null === this.Hu ? null : new Ii(Math.floor(this.Hu.Na()), Math.ceil(this.Hu.bi()));
    }
    $u() {
      return this.Hu;
    }
    static ju() {
      return new _Bi(null);
    }
  };
  function Ai(t2, i2) {
    return t2.weight > i2.weight ? t2 : i2;
  }
  var zi = class {
    constructor(t2, i2, n2, s2) {
      this.k_ = 0, this.qu = null, this.Yu = [], this.Po = null, this.yo = null, this.Ku = new Ei(), this.Gu = /* @__PURE__ */ new Map(), this.Zu = Bi.ju(), this.Xu = true, this.Ju = new o(), this.Qu = new o(), this.tc = new o(), this.nc = null, this.sc = null, this.ec = /* @__PURE__ */ new Map(), this.rc = -1, this.hc = [], this.ac = 1, this.yn = i2, this.Do = n2, this.lc = i2.rightOffset, this.oc = i2.barSpacing, this.sn = t2, this._c(i2), this.Pu = s2, this.uc(), this.Ku.Bu(i2.uniformDistribution), this.cc(), this.dc();
    }
    N() {
      return this.yn;
    }
    fc(t2) {
      _(this.Do, t2), this.vc(), this.uc();
    }
    vr(t2, i2) {
      _(this.yn, t2), this.yn.fixLeftEdge && this.mc(), this.yn.fixRightEdge && this.wc(), void 0 !== t2.barSpacing && this.sn.gs(t2.barSpacing), void 0 !== t2.rightOffset && this.sn.Ms(t2.rightOffset), this._c(t2), void 0 === t2.minBarSpacing && void 0 === t2.maxBarSpacing || this.sn.gs(t2.barSpacing ?? this.oc), void 0 !== t2.ignoreWhitespaceIndices && t2.ignoreWhitespaceIndices !== this.yn.ignoreWhitespaceIndices && this.dc(), this.vc(), this.uc(), void 0 === t2.enableConflation && void 0 === t2.conflationThresholdFactor || this.cc(), this.tc.p();
    }
    Rn(t2) {
      return this.Yu[t2]?.time ?? null;
    }
    en(t2) {
      return this.Yu[t2] ?? null;
    }
    gc(t2, i2) {
      if (this.Yu.length < 1) return null;
      if (this.Pu.key(t2) > this.Pu.key(this.Yu[this.Yu.length - 1].time)) return i2 ? this.Yu.length - 1 : null;
      const n2 = yt(this.Yu, this.Pu.key(t2), ((t3, i3) => this.Pu.key(t3.time) < i3));
      return this.Pu.key(t2) < this.Pu.key(this.Yu[n2].time) ? i2 ? n2 : null : n2;
    }
    Zi() {
      return 0 === this.k_ || 0 === this.Yu.length || null === this.qu;
    }
    Mc() {
      return this.Yu.length > 0;
    }
    Be() {
      return this.bc(), this.Zu.Uu();
    }
    Sc() {
      return this.bc(), this.Zu.$u();
    }
    xc() {
      const t2 = this.Be();
      if (null === t2) return null;
      const i2 = { from: t2.Na(), to: t2.bi() };
      return this.Cc(i2);
    }
    Cc(t2) {
      const i2 = Math.round(t2.from), n2 = Math.round(t2.to), s2 = a(this.yc()), e2 = a(this.Pc());
      return { from: a(this.en(Math.max(s2, i2))), to: a(this.en(Math.min(e2, n2))) };
    }
    kc(t2) {
      return { from: a(this.gc(t2.from, true)), to: a(this.gc(t2.to, true)) };
    }
    nn() {
      return this.k_;
    }
    H_(t2) {
      if (!isFinite(t2) || t2 <= 0) return;
      if (this.k_ === t2) return;
      const i2 = this.Sc(), n2 = this.k_;
      if (this.k_ = t2, this.Xu = true, this.yn.lockVisibleTimeRangeOnResize && 0 !== n2) {
        const i3 = this.oc * t2 / n2;
        this.oc = i3;
      }
      if (this.yn.fixLeftEdge && null !== i2 && i2.Na() <= 0) {
        const i3 = n2 - t2;
        this.lc -= Math.round(i3 / this.oc) + 1, this.Xu = true;
      }
      this.Tc(), this.Rc();
    }
    jt(t2) {
      if (this.Zi() || !c(t2)) return 0;
      const i2 = this.Dc() + this.lc - t2;
      return this.k_ - (i2 + 0.5) * this.oc - 1;
    }
    Ic(t2, i2) {
      const n2 = this.Dc(), s2 = void 0 === i2 ? 0 : i2.from, e2 = void 0 === i2 ? t2.length : i2.to;
      for (let i3 = s2; i3 < e2; i3++) {
        const s3 = t2[i3].wt, e3 = n2 + this.lc - s3, r2 = this.k_ - (e3 + 0.5) * this.oc - 1;
        t2[i3]._t = r2;
      }
    }
    Vc(t2, i2) {
      const n2 = Math.ceil(this.Ec(t2));
      return i2 && this.yn.ignoreWhitespaceIndices && !this.Bc(n2) ? this.Ac(n2) : n2;
    }
    Ms(t2) {
      this.Xu = true, this.lc = t2, this.Rc(), this.sn.zc(), this.sn.mr();
    }
    ml() {
      return this.oc;
    }
    gs(t2) {
      const i2 = this.oc;
      if (this.Lc(t2), void 0 !== this.yn.rightOffsetPixels && 0 !== i2) {
        const t3 = this.lc * i2 / this.oc;
        this.lc = t3;
      }
      this.Rc(), this.sn.zc(), this.sn.mr();
    }
    Oc() {
      return this.lc;
    }
    Ll() {
      if (this.Zi()) return null;
      if (null !== this.sc) return this.sc;
      const t2 = this.oc, i2 = 5 * (this.sn.N().layout.fontSize + 4) / 8 * (this.yn.tickMarkMaxCharacterLength || 8), n2 = Math.round(i2 / t2), s2 = a(this.Be()), e2 = Math.max(s2.Na(), s2.Na() - n2), r2 = Math.max(s2.bi(), s2.bi() - n2), h2 = this.Ku.Lu(t2, i2, this.yn.ignoreWhitespaceIndices, this.ec, this.rc), l2 = this.yc() + n2, o2 = this.Pc() - n2, _2 = this.Nc(), u2 = this.yn.fixLeftEdge || _2, c2 = this.yn.fixRightEdge || _2;
      let d2 = 0;
      for (const t3 of h2) {
        if (!(e2 <= t3.index && t3.index <= r2)) continue;
        let n3;
        d2 < this.hc.length ? (n3 = this.hc[d2], n3.coord = this.jt(t3.index), n3.label = this.Fc(t3), n3.weight = t3.weight) : (n3 = { needAlignCoordinate: false, coord: this.jt(t3.index), label: this.Fc(t3), weight: t3.weight }, this.hc.push(n3)), this.oc > i2 / 2 && !_2 ? n3.needAlignCoordinate = false : n3.needAlignCoordinate = u2 && t3.index <= l2 || c2 && t3.index >= o2, d2++;
      }
      return this.hc.length = d2, this.sc = this.hc, this.hc;
    }
    Wc() {
      let t2;
      this.Xu = true, this.gs(this.yn.barSpacing), t2 = void 0 !== this.yn.rightOffsetPixels ? this.yn.rightOffsetPixels / this.ml() : this.yn.rightOffset, this.Ms(t2);
    }
    Hc(t2) {
      this.Xu = true, this.qu = t2, this.Rc(), this.mc();
    }
    Uc(t2, i2) {
      const n2 = this.Ec(t2), s2 = this.ml(), e2 = s2 + i2 * (s2 / 10);
      this.gs(e2), this.yn.rightBarStaysOnScroll || this.Ms(this.Oc() + (n2 - this.Ec(t2)));
    }
    u_(t2) {
      this.Po && this.v_(), null === this.yo && null === this.nc && (this.Zi() || (this.yo = t2, this.$c()));
    }
    c_(t2) {
      if (null === this.nc) return;
      const i2 = Jt(this.k_ - t2, 0, this.k_), n2 = Jt(this.k_ - a(this.yo), 0, this.k_);
      0 !== i2 && 0 !== n2 && this.gs(this.nc.ml * i2 / n2);
    }
    d_() {
      null !== this.yo && (this.yo = null, this.jc());
    }
    f_(t2) {
      null === this.Po && null === this.nc && (this.Zi() || (this.Po = t2, this.$c()));
    }
    p_(t2) {
      if (null === this.Po) return;
      const i2 = (this.Po - t2) / this.ml();
      this.lc = a(this.nc).Oc + i2, this.Xu = true, this.Rc();
    }
    v_() {
      null !== this.Po && (this.Po = null, this.jc());
    }
    qc() {
      this.Yc(this.yn.rightOffset);
    }
    Yc(t2, i2 = 400) {
      if (!isFinite(t2)) throw new RangeError("offset is required and must be finite number");
      if (!isFinite(i2) || i2 <= 0) throw new RangeError("animationDuration (optional) must be finite positive number");
      const n2 = this.lc, s2 = performance.now();
      this.sn.ps({ Kc: (t3) => (t3 - s2) / i2 >= 1, Gc: (e2) => {
        const r2 = (e2 - s2) / i2;
        return r2 >= 1 ? t2 : n2 + (t2 - n2) * r2;
      } });
    }
    Pt(t2, i2) {
      this.Xu = true, this.Yu = t2, this.Ku.Au(t2, i2), this.Rc();
    }
    Zc() {
      return this.Ju;
    }
    Xc() {
      return this.Qu;
    }
    Jc() {
      return this.tc;
    }
    Dc() {
      return this.qu || 0;
    }
    Qc(t2, i2) {
      const n2 = t2.Iu(), s2 = i2 && this.yn.rightOffsetPixels || 0;
      this.Lc((this.k_ - s2) / n2), this.lc = t2.bi() - this.Dc(), i2 && (this.lc = s2 ? s2 / this.ml() : this.yn.rightOffset), this.Rc(), this.Xu = true, this.sn.zc(), this.sn.mr();
    }
    td() {
      const t2 = this.yc(), i2 = this.Pc();
      if (null === t2 || null === i2) return;
      const n2 = !this.yn.rightOffsetPixels && this.yn.rightOffset || 0;
      this.Qc(new Ii(t2, i2 + n2), true);
    }
    nd(t2) {
      const i2 = new Ii(t2.from, t2.to);
      this.Qc(i2);
    }
    rn(t2) {
      return void 0 !== this.Do.timeFormatter ? this.Do.timeFormatter(t2.originalTime) : this.Pu.formatHorzItem(t2.time);
    }
    dc() {
      if (!this.yn.ignoreWhitespaceIndices) return;
      this.ec.clear();
      const t2 = this.sn.Jn();
      for (const i2 of t2) for (const t3 of i2.fl()) this.ec.set(t3, true);
      this.rc++;
    }
    sd() {
      return this.ac;
    }
    Sl() {
      const t2 = 1 / (window.devicePixelRatio || 1), i2 = this.yn.minBarSpacing;
      if (i2 >= t2) return [1];
      const n2 = [1];
      let s2 = 2;
      for (; s2 <= 512; ) {
        i2 < t2 / s2 && n2.push(s2), s2 *= 2;
      }
      return n2;
    }
    Nc() {
      const t2 = this.sn.N().handleScroll, i2 = this.sn.N().handleScale;
      return !(t2.horzTouchDrag || t2.mouseWheel || t2.pressedMouseMove || t2.vertTouchDrag || i2.axisDoubleClickReset.time || i2.axisPressedMouseMove.time || i2.mouseWheel || i2.pinch);
    }
    yc() {
      return 0 === this.Yu.length ? null : 0;
    }
    Pc() {
      return 0 === this.Yu.length ? null : this.Yu.length - 1;
    }
    ed(t2) {
      return (this.k_ - 1 - t2) / this.oc;
    }
    Ec(t2) {
      const i2 = this.ed(t2), n2 = this.Dc() + this.lc - i2;
      return Math.round(1e6 * n2) / 1e6;
    }
    Lc(t2) {
      const i2 = this.oc;
      this.oc = t2, this.Tc(), i2 !== this.oc && (this.Xu = true, this.rd(), this.cc());
    }
    bc() {
      if (!this.Xu) return;
      if (this.Xu = false, this.Zi()) return void this.hd(Bi.ju());
      const t2 = this.Dc(), i2 = this.k_ / this.oc, n2 = this.lc + t2, s2 = new Ii(n2 - i2 + 1, n2);
      this.hd(new Bi(s2));
    }
    Tc() {
      const t2 = Jt(this.oc, this.ad(), this.ld());
      this.oc !== t2 && (this.oc = t2, this.Xu = true);
    }
    ld() {
      return this.yn.maxBarSpacing > 0 ? this.yn.maxBarSpacing : 0.5 * this.k_;
    }
    ad() {
      return this.yn.fixLeftEdge && this.yn.fixRightEdge && 0 !== this.Yu.length ? this.k_ / this.Yu.length : this.yn.minBarSpacing;
    }
    cc() {
      if (!this.yn.enableConflation) return void (this.ac = 1);
      const t2 = 1 / (window.devicePixelRatio || 1) * (this.yn.conflationThresholdFactor ?? 1);
      if (this.oc >= t2) return void (this.ac = 1);
      const i2 = t2 / this.oc, n2 = Math.pow(2, Math.floor(Math.log2(i2)));
      this.ac = Math.min(n2, 512);
    }
    Rc() {
      const t2 = this.od();
      null !== t2 && this.lc < t2 && (this.lc = t2, this.Xu = true);
      const i2 = this._d();
      this.lc > i2 && (this.lc = i2, this.Xu = true);
    }
    od() {
      const t2 = this.yc(), i2 = this.qu;
      if (null === t2 || null === i2) return null;
      return t2 - i2 - 1 + (this.yn.fixLeftEdge ? this.k_ / this.oc : Math.min(2, this.Yu.length));
    }
    _d() {
      return this.yn.fixRightEdge ? 0 : this.k_ / this.oc - Math.min(2, this.Yu.length);
    }
    $c() {
      this.nc = { ml: this.ml(), Oc: this.Oc() };
    }
    jc() {
      this.nc = null;
    }
    Fc(t2) {
      let i2 = this.Gu.get(t2.weight);
      return void 0 === i2 && (i2 = new Di(((t3) => this.ud(t3)), this.Pu), this.Gu.set(t2.weight, i2)), i2.ku(t2);
    }
    ud(t2) {
      return this.Pu.formatTickmark(t2, this.Do);
    }
    hd(t2) {
      const i2 = this.Zu;
      this.Zu = t2, Vi(i2.Uu(), this.Zu.Uu()) || this.Ju.p(), Vi(i2.$u(), this.Zu.$u()) || this.Qu.p(), this.rd();
    }
    rd() {
      this.sc = null;
    }
    vc() {
      this.rd(), this.Gu.clear();
    }
    uc() {
      this.Pu.updateFormatter(this.Do);
    }
    mc() {
      if (!this.yn.fixLeftEdge) return;
      const t2 = this.yc();
      if (null === t2) return;
      const i2 = this.Be();
      if (null === i2) return;
      const n2 = i2.Na() - t2;
      if (n2 < 0) {
        const t3 = this.lc - n2 - 1;
        this.Ms(t3);
      }
      this.Tc();
    }
    wc() {
      this.Rc(), this.Tc();
    }
    Bc(t2) {
      return !this.yn.ignoreWhitespaceIndices || (this.ec.get(t2) || false);
    }
    Ac(t2) {
      const i2 = (function* (t3) {
        const i3 = Math.round(t3), n3 = i3 < t3;
        let s2 = 1;
        for (; ; ) n3 ? (yield i3 + s2, yield i3 - s2) : (yield i3 - s2, yield i3 + s2), s2++;
      })(t2), n2 = this.Pc();
      for (; n2; ) {
        const t3 = i2.next().value;
        if (this.ec.get(t3)) return t3;
        if (t3 < 0 || t3 > n2) break;
      }
      return t2;
    }
    _c(t2) {
      if (void 0 !== t2.rightOffsetPixels) {
        const i2 = t2.rightOffsetPixels / (t2.barSpacing || this.oc);
        this.sn.Ms(i2);
      }
    }
  };
  var Li;
  var Oi;
  var Ni;
  var Fi;
  var Wi;
  !(function(t2) {
    t2[t2.OnTouchEnd = 0] = "OnTouchEnd", t2[t2.OnNextTap = 1] = "OnNextTap";
  })(Li || (Li = {}));
  var Hi = class {
    constructor(t2, i2, n2) {
      this.dd = [], this.fd = [], this.pd = null, this.k_ = 0, this.vd = null, this.md = new o(), this.wd = new o(), this.gd = null, this.Md = t2, this.yn = i2, this.Pu = n2, this.Io = new x(this.yn.layout.colorParsers), this.bd = new M(this), this.ia = new zi(this, i2.timeScale, this.yn.localization, n2), this.Ct = new j(this, i2.crosshair), this.Sd = new Xt(i2.crosshair), i2.addDefaultPane && (this.xd(0), this.dd[0].W_(2)), this.Cd = this.yd(0), this.Pd = this.yd(1);
    }
    ka() {
      this.kd(Y.ys());
    }
    mr() {
      this.kd(Y.Cs());
    }
    Ya() {
      this.kd(new Y(1));
    }
    Ta(t2) {
      const i2 = this.Td(t2);
      this.kd(i2);
    }
    cu() {
      return this.vd;
    }
    Rd(t2) {
      if (this.vd?.uu === t2?.uu && this.vd?.bu?.te === t2?.bu?.te && this.vd?.bu?.ie === t2?.bu?.ie && this.vd?.Mu === t2?.Mu && this.vd?.ee === t2?.ee) return;
      const i2 = this.vd;
      this.vd = t2, null !== i2 && this.Ta(i2.uu), null !== t2 && t2.uu !== i2?.uu && this.Ta(t2.uu);
    }
    N() {
      return this.yn;
    }
    vr(t2) {
      _(this.yn, t2), this.dd.forEach(((i2) => i2.L_(t2))), void 0 !== t2.timeScale && this.ia.vr(t2.timeScale), void 0 !== t2.localization && this.ia.fc(t2.localization), (t2.leftPriceScale || t2.rightPriceScale) && this.md.p(), this.Cd = this.yd(0), this.Pd = this.yd(1), this.ka();
    }
    Dd(t2, i2, n2 = 0) {
      const s2 = this.dd[n2];
      if (void 0 === s2) return;
      if ("left" === t2) return _(this.yn, { leftPriceScale: i2 }), s2.L_({ leftPriceScale: i2 }), this.md.p(), void this.ka();
      if ("right" === t2) return _(this.yn, { rightPriceScale: i2 }), s2.L_({ rightPriceScale: i2 }), this.md.p(), void this.ka();
      const e2 = this.Id(t2, n2);
      null !== e2 && (e2.Ft.vr(i2), this.md.p());
    }
    Id(t2, i2) {
      const n2 = this.dd[i2];
      if (void 0 === n2) return null;
      const s2 = n2.O_(t2);
      return null !== s2 ? { Kn: n2, Ft: s2 } : null;
    }
    Bt() {
      return this.ia;
    }
    Gn() {
      return this.dd;
    }
    Vd() {
      return this.Ct;
    }
    Ed() {
      return this.wd;
    }
    Bd(t2, i2) {
      t2.$o(i2), this.zc();
    }
    H_(t2) {
      this.k_ = t2, this.ia.H_(this.k_), this.dd.forEach(((i2) => i2.H_(t2))), this.zc();
    }
    Ad(t2) {
      1 !== this.dd.length && (r(t2 >= 0 && t2 < this.dd.length, "Invalid pane index"), this.dd.splice(t2, 1), this.ka());
    }
    zd(t2, i2) {
      if (this.dd.length < 2) return;
      r(t2 >= 0 && t2 < this.dd.length, "Invalid pane index");
      const n2 = this.dd[t2], s2 = this.dd.reduce(((t3, i3) => t3 + i3.F_()), 0), e2 = this.dd.reduce(((t3, i3) => t3 + i3.$t()), 0), h2 = e2 - 30 * (this.dd.length - 1);
      i2 = Math.min(h2, Math.max(30, i2));
      const a2 = s2 / e2, l2 = n2.$t();
      n2.W_(i2 * a2);
      let o2 = i2 - l2, _2 = this.dd.length - 1;
      for (const t3 of this.dd) if (t3 !== n2) {
        const i3 = Math.min(h2, Math.max(30, t3.$t() - o2 / _2));
        o2 -= t3.$t() - i3, _2 -= 1;
        const n3 = i3 * a2;
        t3.W_(n3);
      }
      this.ka();
    }
    Ld(t2, i2) {
      r(t2 >= 0 && t2 < this.dd.length && i2 >= 0 && i2 < this.dd.length, "Invalid pane index");
      const n2 = this.dd[t2], s2 = this.dd[i2];
      this.dd[t2] = s2, this.dd[i2] = n2, this.ka();
    }
    Od(t2, i2) {
      if (r(t2 >= 0 && t2 < this.dd.length && i2 >= 0 && i2 < this.dd.length, "Invalid pane index"), t2 === i2) return;
      const [n2] = this.dd.splice(t2, 1);
      this.dd.splice(i2, 0, n2), this.ka();
    }
    Q_(t2, i2, n2) {
      t2.Q_(i2, n2);
    }
    tu(t2, i2, n2) {
      t2.tu(i2, n2), this.Ra(), this.kd(this.Nd(t2, 2));
    }
    iu(t2, i2) {
      t2.iu(i2), this.kd(this.Nd(t2, 2));
    }
    nu(t2, i2, n2) {
      i2.Lo() || t2.nu(i2, n2);
    }
    su(t2, i2, n2) {
      i2.Lo() || (t2.su(i2, n2), this.Ra(), this.kd(this.Nd(t2, 2)));
    }
    eu(t2, i2) {
      i2.Lo() || (t2.eu(i2), this.kd(this.Nd(t2, 2)));
    }
    au(t2, i2) {
      t2.au(i2), this.kd(this.Nd(t2, 2));
    }
    Fd(t2) {
      this.ia.u_(t2);
    }
    Wd(t2, i2) {
      const n2 = this.Bt();
      if (n2.Zi() || 0 === i2) return;
      const s2 = n2.nn();
      t2 = Math.max(1, Math.min(t2, s2)), n2.Uc(t2, i2), this.zc();
    }
    Hd(t2) {
      this.Ud(0), this.$d(t2), this.jd();
    }
    qd(t2) {
      this.ia.c_(t2), this.zc();
    }
    Yd() {
      this.ia.d_(), this.mr();
    }
    Ud(t2) {
      this.ia.f_(t2);
    }
    $d(t2) {
      this.ia.p_(t2), this.zc();
    }
    jd() {
      this.ia.v_(), this.mr();
    }
    Jn() {
      return this.fd;
    }
    Wn() {
      return null === this.pd && (this.pd = this.fd.filter(((t2) => t2.It()))), this.pd;
    }
    Pa() {
      this.pd = null;
    }
    Kd(t2, i2, n2, s2, e2) {
      this.Ct.In(t2, i2);
      let r2 = NaN, h2 = this.ia.Vc(t2, true);
      const a2 = this.ia.Be();
      null !== a2 && (h2 = Math.min(Math.max(a2.Na(), h2), a2.bi())), h2 = this.Ct.Fn(h2);
      const l2 = s2.kn(), o2 = l2.zt();
      if (null !== o2 && (r2 = l2.Tn(i2, o2)), r2 = this.Sd.Pl(r2, h2, s2), this.Ct.An(h2, r2, s2), this.Ya(), !e2) {
        const e3 = Ri(s2, t2, i2);
        this.Rd(e3 && { uu: e3.uu, bu: e3.bu, Mu: e3.Mu || null, ee: e3.ee }), this.wd.p(this.Ct.Et(), { x: t2, y: i2 }, n2);
      }
    }
    Gd(t2, i2, n2) {
      const s2 = n2.kn(), e2 = s2.zt(), r2 = s2.Nt(t2, a(e2)), h2 = this.ia.gc(i2, true), l2 = this.ia.jt(a(h2));
      this.Kd(l2, r2, null, n2, true);
    }
    Zd(t2) {
      this.Vd().Ln(), this.Ya(), t2 || this.wd.p(null, null, null);
    }
    Ra() {
      const t2 = this.Ct.Kn();
      if (null !== t2) {
        const i2 = this.Ct.En(), n2 = this.Ct.Bn();
        this.Kd(i2, n2, null, t2);
      }
      this.Ct.Nn();
    }
    Xd(t2, i2, n2) {
      const s2 = this.ia.Rn(0);
      void 0 !== i2 && void 0 !== n2 && this.ia.Pt(i2, n2);
      const e2 = this.ia.Rn(0), r2 = this.ia.Dc(), h2 = this.ia.Be();
      if (null !== h2 && null !== s2 && null !== e2) {
        const i3 = h2.Le(r2), a2 = this.Pu.key(s2) > this.Pu.key(e2), l2 = null !== t2 && t2 > r2 && !a2, o2 = this.ia.N().allowShiftVisibleRangeOnWhitespaceReplacement, _2 = i3 && (!(void 0 === n2) || o2) && this.ia.N().shiftVisibleRangeOnNewBar;
        if (l2 && !_2) {
          const i4 = t2 - r2;
          this.ia.Ms(this.ia.Oc() - i4);
        }
      }
      this.ia.Hc(t2);
    }
    Ea(t2) {
      null !== t2 && t2.ou();
    }
    Ks(t2) {
      if ((function(t3) {
        return t3 instanceof Si;
      })(t2)) return t2;
      const i2 = this.dd.find(((i3) => i3.Dt().includes(t2)));
      return void 0 === i2 ? null : i2;
    }
    zc() {
      this.dd.forEach(((t2) => t2.ou())), this.Ra();
    }
    m() {
      this.dd.forEach(((t2) => t2.m())), this.dd.length = 0, this.yn.localization.priceFormatter = void 0, this.yn.localization.percentageFormatter = void 0, this.yn.localization.timeFormatter = void 0;
    }
    Jd() {
      return this.bd;
    }
    Js() {
      return this.bd.N();
    }
    N_() {
      return this.md;
    }
    Qd(t2, i2) {
      const n2 = this.xd(i2);
      this.tf(t2, n2), this.fd.push(t2), this.Pa(), 1 === this.fd.length ? this.ka() : this.mr();
    }
    if(t2) {
      const i2 = this.Ks(t2), n2 = this.fd.indexOf(t2);
      r(-1 !== n2, "Series not found");
      const s2 = a(i2);
      this.fd.splice(n2, 1), s2.l_(t2), t2.m && t2.m(), this.Pa(), this.ia.dc(), this.nf(s2);
    }
    ya(t2, i2) {
      const n2 = a(this.Ks(t2));
      n2.l_(t2, true), n2.h_(t2, i2, true);
    }
    td() {
      const t2 = Y.Cs();
      t2.us(), this.kd(t2);
    }
    sf(t2) {
      const i2 = Y.Cs();
      i2.fs(t2), this.kd(i2);
    }
    ws() {
      const t2 = Y.Cs();
      t2.ws(), this.kd(t2);
    }
    gs(t2) {
      const i2 = Y.Cs();
      i2.gs(t2), this.kd(i2);
    }
    Ms(t2) {
      const i2 = Y.Cs();
      i2.Ms(t2), this.kd(i2);
    }
    ps(t2) {
      const i2 = Y.Cs();
      i2.ps(t2), this.kd(i2);
    }
    cs() {
      const t2 = Y.Cs();
      t2.cs(), this.kd(t2);
    }
    ef() {
      const t2 = this.yn.defaultVisiblePriceScaleId, i2 = this.yn.leftPriceScale.visible;
      return i2 !== this.yn.rightPriceScale.visible ? i2 ? "left" : "right" : t2;
    }
    rf(t2, i2) {
      r(i2 >= 0, "Index should be greater or equal to 0");
      if (i2 === this.hf(t2)) return;
      const n2 = a(this.Ks(t2));
      n2.l_(t2);
      const s2 = this.xd(i2);
      this.tf(t2, s2);
      let e2 = false;
      0 === n2.kl().length && (e2 = this.nf(n2)), e2 || this.ka();
    }
    af() {
      return this.Pd;
    }
    $() {
      return this.Cd;
    }
    Ut(t2) {
      const i2 = this.Pd, n2 = this.Cd;
      if (i2 === n2) return i2;
      if (t2 = Math.max(0, Math.min(100, Math.round(100 * t2))), null === this.gd || this.gd.ah !== n2 || this.gd.oh !== i2) this.gd = { ah: n2, oh: i2, lf: /* @__PURE__ */ new Map() };
      else {
        const i3 = this.gd.lf.get(t2);
        if (void 0 !== i3) return i3;
      }
      const s2 = this.Io.tt(n2, i2, t2 / 100);
      return this.gd.lf.set(t2, s2), s2;
    }
    _f(t2) {
      return this.dd.indexOf(t2);
    }
    Xi() {
      return this.Io;
    }
    uf() {
      return this.cf();
    }
    cf(t2) {
      const i2 = new Si(this.ia, this);
      this.dd.push(i2);
      const n2 = t2 ?? this.dd.length - 1, s2 = Y.ys();
      return s2.es(n2, { rs: 0, hs: true }), this.kd(s2), i2;
    }
    xd(t2) {
      return r(t2 >= 0, "Index should be greater or equal to 0"), (t2 = Math.min(this.dd.length, t2)) < this.dd.length ? this.dd[t2] : this.cf(t2);
    }
    hf(t2) {
      return this.dd.findIndex(((i2) => i2.Y_().includes(t2)));
    }
    Nd(t2, i2) {
      const n2 = new Y(i2);
      if (null !== t2) {
        const s2 = this.dd.indexOf(t2);
        n2.es(s2, { rs: i2 });
      }
      return n2;
    }
    Td(t2, i2) {
      return void 0 === i2 && (i2 = 2), this.Nd(this.Ks(t2), i2);
    }
    kd(t2) {
      this.Md && this.Md(t2), this.dd.forEach(((t3) => t3.wu().wr().Pt()));
    }
    tf(t2, i2) {
      const n2 = t2.N().priceScaleId, s2 = void 0 !== n2 ? n2 : this.ef();
      i2.h_(t2, s2), q(s2) || t2.vr(t2.N());
    }
    yd(t2) {
      const i2 = this.yn.layout;
      return "gradient" === i2.background.type ? 0 === t2 ? i2.background.topColor : i2.background.bottomColor : i2.background.color;
    }
    nf(t2) {
      return !t2.q_() && 0 === t2.kl().length && this.dd.length > 1 && (this.dd.splice(this._f(t2), 1), this.ka(), true);
    }
  };
  function Ui(t2) {
    if (t2 >= 1) return 0;
    let i2 = 0;
    for (; i2 < 8; i2++) {
      const n2 = Math.round(t2);
      if (Math.abs(n2 - t2) < 1e-8) return i2;
      t2 *= 10;
    }
    return i2;
  }
  function $i(t2) {
    return !u(t2) && !d(t2);
  }
  function ji(t2) {
    return u(t2);
  }
  !(function(t2) {
    t2[t2.Disabled = 0] = "Disabled", t2[t2.Continuous = 1] = "Continuous", t2[t2.OnDataUpdate = 2] = "OnDataUpdate";
  })(Oi || (Oi = {})), (function(t2) {
    t2[t2.LastBar = 0] = "LastBar", t2[t2.LastVisible = 1] = "LastVisible";
  })(Ni || (Ni = {})), (function(t2) {
    t2.Solid = "solid", t2.VerticalGradient = "gradient";
  })(Fi || (Fi = {})), (function(t2) {
    t2[t2.Year = 0] = "Year", t2[t2.Month = 1] = "Month", t2[t2.DayOfMonth = 2] = "DayOfMonth", t2[t2.Time = 3] = "Time", t2[t2.TimeWithSeconds = 4] = "TimeWithSeconds";
  })(Wi || (Wi = {}));
  var qi = (t2) => t2.getUTCFullYear();
  function Yi(t2, i2, n2) {
    return i2.replace(/yyyy/g, ((t3) => Z(qi(t3), 4))(t2)).replace(/yy/g, ((t3) => Z(qi(t3) % 100, 2))(t2)).replace(/MMMM/g, ((t3, i3) => new Date(t3.getUTCFullYear(), t3.getUTCMonth(), 1).toLocaleString(i3, { month: "long" }))(t2, n2)).replace(/MMM/g, ((t3, i3) => new Date(t3.getUTCFullYear(), t3.getUTCMonth(), 1).toLocaleString(i3, { month: "short" }))(t2, n2)).replace(/MM/g, ((t3) => Z(((t4) => t4.getUTCMonth() + 1)(t3), 2))(t2)).replace(/dd/g, ((t3) => Z(((t4) => t4.getUTCDate())(t3), 2))(t2));
  }
  var Ki = class {
    constructor(t2 = "yyyy-MM-dd", i2 = "default") {
      this.df = t2, this.ff = i2;
    }
    ku(t2) {
      return Yi(t2, this.df, this.ff);
    }
  };
  var Gi = class {
    constructor(t2) {
      this.pf = t2 || "%h:%m:%s";
    }
    ku(t2) {
      return this.pf.replace("%h", Z(t2.getUTCHours(), 2)).replace("%m", Z(t2.getUTCMinutes(), 2)).replace("%s", Z(t2.getUTCSeconds(), 2));
    }
  };
  var Zi = { vf: "yyyy-MM-dd", mf: "%h:%m:%s", wf: " ", gf: "default" };
  var Xi = class {
    constructor(t2 = {}) {
      const i2 = { ...Zi, ...t2 };
      this.Mf = new Ki(i2.vf, i2.gf), this.bf = new Gi(i2.mf), this.Sf = i2.wf;
    }
    ku(t2) {
      return `${this.Mf.ku(t2)}${this.Sf}${this.bf.ku(t2)}`;
    }
  };
  function Ji(t2) {
    return 60 * t2 * 60 * 1e3;
  }
  function Qi(t2) {
    return 60 * t2 * 1e3;
  }
  var tn = [{ xf: (nn = 1, 1e3 * nn), Cf: 10 }, { xf: Qi(1), Cf: 20 }, { xf: Qi(5), Cf: 21 }, { xf: Qi(30), Cf: 22 }, { xf: Ji(1), Cf: 30 }, { xf: Ji(3), Cf: 31 }, { xf: Ji(6), Cf: 32 }, { xf: Ji(12), Cf: 33 }];
  var nn;
  function sn(t2, i2) {
    if (t2.getUTCFullYear() !== i2.getUTCFullYear()) return 70;
    if (t2.getUTCMonth() !== i2.getUTCMonth()) return 60;
    if (t2.getUTCDate() !== i2.getUTCDate()) return 50;
    for (let n2 = tn.length - 1; n2 >= 0; --n2) if (Math.floor(i2.getTime() / tn[n2].xf) !== Math.floor(t2.getTime() / tn[n2].xf)) return tn[n2].Cf;
    return 0;
  }
  function en(t2) {
    let i2 = t2;
    if (d(t2) && (i2 = hn(t2)), !$i(i2)) throw new Error("time must be of type BusinessDay");
    const n2 = new Date(Date.UTC(i2.year, i2.month - 1, i2.day, 0, 0, 0, 0));
    return { yf: Math.round(n2.getTime() / 1e3), Pf: i2 };
  }
  function rn(t2) {
    if (!ji(t2)) throw new Error("time must be of type isUTCTimestamp");
    return { yf: t2 };
  }
  function hn(t2) {
    const i2 = new Date(t2);
    if (isNaN(i2.getTime())) throw new Error(`Invalid date string=${t2}, expected format=yyyy-mm-dd`);
    return { day: i2.getUTCDate(), month: i2.getUTCMonth() + 1, year: i2.getUTCFullYear() };
  }
  function an(t2) {
    d(t2.time) && (t2.time = hn(t2.time));
  }
  var ln = class {
    options() {
      return this.yn;
    }
    setOptions(t2) {
      this.yn = t2, this.updateFormatter(t2.localization);
    }
    preprocessData(t2) {
      Array.isArray(t2) ? (function(t3) {
        t3.forEach(an);
      })(t2) : an(t2);
    }
    createConverterToInternalObj(t2) {
      return a((function(t3) {
        return 0 === t3.length ? null : $i(t3[0].time) || d(t3[0].time) ? en : rn;
      })(t2));
    }
    key(t2) {
      return "object" == typeof t2 && "yf" in t2 ? t2.yf : this.key(this.convertHorzItemToInternal(t2));
    }
    cacheKey(t2) {
      const i2 = t2;
      return void 0 === i2.Pf ? new Date(1e3 * i2.yf).getTime() : new Date(Date.UTC(i2.Pf.year, i2.Pf.month - 1, i2.Pf.day)).getTime();
    }
    convertHorzItemToInternal(t2) {
      return ji(i2 = t2) ? rn(i2) : $i(i2) ? en(i2) : en(hn(i2));
      var i2;
    }
    updateFormatter(t2) {
      if (!this.yn) return;
      const i2 = t2.dateFormat;
      this.yn.timeScale.timeVisible ? this.kf = new Xi({ vf: i2, mf: this.yn.timeScale.secondsVisible ? "%h:%m:%s" : "%h:%m", wf: "   ", gf: t2.locale }) : this.kf = new Ki(i2, t2.locale);
    }
    formatHorzItem(t2) {
      const i2 = t2;
      return this.kf.ku(new Date(1e3 * i2.yf));
    }
    formatTickmark(t2, i2) {
      const n2 = (function(t3, i3, n3) {
        switch (t3) {
          case 0:
          case 10:
            return i3 ? n3 ? 4 : 3 : 2;
          case 20:
          case 21:
          case 22:
          case 30:
          case 31:
          case 32:
          case 33:
            return i3 ? 3 : 2;
          case 50:
            return 2;
          case 60:
            return 1;
          case 70:
            return 0;
        }
      })(t2.weight, this.yn.timeScale.timeVisible, this.yn.timeScale.secondsVisible), s2 = this.yn.timeScale;
      if (void 0 !== s2.tickMarkFormatter) {
        const e2 = s2.tickMarkFormatter(t2.originalTime, n2, i2.locale);
        if (null !== e2) return e2;
      }
      return (function(t3, i3, n3) {
        const s3 = {};
        switch (i3) {
          case 0:
            s3.year = "numeric";
            break;
          case 1:
            s3.month = "short";
            break;
          case 2:
            s3.day = "numeric";
            break;
          case 3:
            s3.hour12 = false, s3.hour = "2-digit", s3.minute = "2-digit";
            break;
          case 4:
            s3.hour12 = false, s3.hour = "2-digit", s3.minute = "2-digit", s3.second = "2-digit";
        }
        const e2 = void 0 === t3.Pf ? new Date(1e3 * t3.yf) : new Date(Date.UTC(t3.Pf.year, t3.Pf.month - 1, t3.Pf.day));
        return new Date(e2.getUTCFullYear(), e2.getUTCMonth(), e2.getUTCDate(), e2.getUTCHours(), e2.getUTCMinutes(), e2.getUTCSeconds(), e2.getUTCMilliseconds()).toLocaleString(n3, s3);
      })(t2.time, n2, i2.locale);
    }
    maxTickMarkWeight(t2) {
      let i2 = t2.reduce(Ai, t2[0]).weight;
      return i2 > 30 && i2 < 50 && (i2 = 30), i2;
    }
    fillWeightsForPoints(t2, i2) {
      !(function(t3, i3 = 0) {
        if (0 === t3.length) return;
        let n2 = 0 === i3 ? null : t3[i3 - 1].time.yf, s2 = null !== n2 ? new Date(1e3 * n2) : null, e2 = 0;
        for (let r2 = i3; r2 < t3.length; ++r2) {
          const i4 = t3[r2], h2 = new Date(1e3 * i4.time.yf);
          null !== s2 && (i4.timeWeight = sn(h2, s2)), e2 += i4.time.yf - (n2 || i4.time.yf), n2 = i4.time.yf, s2 = h2;
        }
        if (0 === i3 && t3.length > 1) {
          const i4 = Math.ceil(e2 / (t3.length - 1)), n3 = new Date(1e3 * (t3[0].time.yf - i4));
          t3[0].timeWeight = sn(new Date(1e3 * t3[0].time.yf), n3);
        }
      })(t2, i2);
    }
    static Tf(t2) {
      return _({ localization: { dateFormat: "dd MMM 'yy" } }, t2 ?? {});
    }
  };
  function on(t2) {
    var i2 = t2.width, n2 = t2.height;
    if (i2 < 0) throw new Error("Negative width is not allowed for Size");
    if (n2 < 0) throw new Error("Negative height is not allowed for Size");
    return { width: i2, height: n2 };
  }
  function _n(t2, i2) {
    return t2.width === i2.width && t2.height === i2.height;
  }
  var un = (function() {
    function t2(t3) {
      var i2 = this;
      this._resolutionListener = function() {
        return i2._onResolutionChanged();
      }, this._resolutionMediaQueryList = null, this._observers = [], this._window = t3, this._installResolutionListener();
    }
    return t2.prototype.dispose = function() {
      this._uninstallResolutionListener(), this._window = null;
    }, Object.defineProperty(t2.prototype, "value", { get: function() {
      return this._window.devicePixelRatio;
    }, enumerable: false, configurable: true }), t2.prototype.subscribe = function(t3) {
      var i2 = this, n2 = { next: t3 };
      return this._observers.push(n2), { unsubscribe: function() {
        i2._observers = i2._observers.filter((function(t4) {
          return t4 !== n2;
        }));
      } };
    }, t2.prototype._installResolutionListener = function() {
      if (null !== this._resolutionMediaQueryList) throw new Error("Resolution listener is already installed");
      var t3 = this._window.devicePixelRatio;
      this._resolutionMediaQueryList = this._window.matchMedia("all and (resolution: ".concat(t3, "dppx)")), this._resolutionMediaQueryList.addListener(this._resolutionListener);
    }, t2.prototype._uninstallResolutionListener = function() {
      null !== this._resolutionMediaQueryList && (this._resolutionMediaQueryList.removeListener(this._resolutionListener), this._resolutionMediaQueryList = null);
    }, t2.prototype._reinstallResolutionListener = function() {
      this._uninstallResolutionListener(), this._installResolutionListener();
    }, t2.prototype._onResolutionChanged = function() {
      var t3 = this;
      this._observers.forEach((function(i2) {
        return i2.next(t3._window.devicePixelRatio);
      })), this._reinstallResolutionListener();
    }, t2;
  })();
  var cn = (function() {
    function t2(t3, i2, n2) {
      var s2;
      this._canvasElement = null, this._bitmapSizeChangedListeners = [], this._suggestedBitmapSize = null, this._suggestedBitmapSizeChangedListeners = [], this._devicePixelRatioObservable = null, this._canvasElementResizeObserver = null, this._canvasElement = t3, this._canvasElementClientSize = on({ width: this._canvasElement.clientWidth, height: this._canvasElement.clientHeight }), this._transformBitmapSize = null != i2 ? i2 : function(t4) {
        return t4;
      }, this._allowResizeObserver = null === (s2 = null == n2 ? void 0 : n2.allowResizeObserver) || void 0 === s2 || s2, this._chooseAndInitObserver();
    }
    return t2.prototype.dispose = function() {
      var t3, i2;
      if (null === this._canvasElement) throw new Error("Object is disposed");
      null === (t3 = this._canvasElementResizeObserver) || void 0 === t3 || t3.disconnect(), this._canvasElementResizeObserver = null, null === (i2 = this._devicePixelRatioObservable) || void 0 === i2 || i2.dispose(), this._devicePixelRatioObservable = null, this._suggestedBitmapSizeChangedListeners.length = 0, this._bitmapSizeChangedListeners.length = 0, this._canvasElement = null;
    }, Object.defineProperty(t2.prototype, "canvasElement", { get: function() {
      if (null === this._canvasElement) throw new Error("Object is disposed");
      return this._canvasElement;
    }, enumerable: false, configurable: true }), Object.defineProperty(t2.prototype, "canvasElementClientSize", { get: function() {
      return this._canvasElementClientSize;
    }, enumerable: false, configurable: true }), Object.defineProperty(t2.prototype, "bitmapSize", { get: function() {
      return on({ width: this.canvasElement.width, height: this.canvasElement.height });
    }, enumerable: false, configurable: true }), t2.prototype.resizeCanvasElement = function(t3) {
      this._canvasElementClientSize = on(t3), this.canvasElement.style.width = "".concat(this._canvasElementClientSize.width, "px"), this.canvasElement.style.height = "".concat(this._canvasElementClientSize.height, "px"), this._invalidateBitmapSize();
    }, t2.prototype.subscribeBitmapSizeChanged = function(t3) {
      this._bitmapSizeChangedListeners.push(t3);
    }, t2.prototype.unsubscribeBitmapSizeChanged = function(t3) {
      this._bitmapSizeChangedListeners = this._bitmapSizeChangedListeners.filter((function(i2) {
        return i2 !== t3;
      }));
    }, Object.defineProperty(t2.prototype, "suggestedBitmapSize", { get: function() {
      return this._suggestedBitmapSize;
    }, enumerable: false, configurable: true }), t2.prototype.subscribeSuggestedBitmapSizeChanged = function(t3) {
      this._suggestedBitmapSizeChangedListeners.push(t3);
    }, t2.prototype.unsubscribeSuggestedBitmapSizeChanged = function(t3) {
      this._suggestedBitmapSizeChangedListeners = this._suggestedBitmapSizeChangedListeners.filter((function(i2) {
        return i2 !== t3;
      }));
    }, t2.prototype.applySuggestedBitmapSize = function() {
      if (null !== this._suggestedBitmapSize) {
        var t3 = this._suggestedBitmapSize;
        this._suggestedBitmapSize = null, this._resizeBitmap(t3), this._emitSuggestedBitmapSizeChanged(t3, this._suggestedBitmapSize);
      }
    }, t2.prototype._resizeBitmap = function(t3) {
      var i2 = this.bitmapSize;
      _n(i2, t3) || (this.canvasElement.width = t3.width, this.canvasElement.height = t3.height, this._emitBitmapSizeChanged(i2, t3));
    }, t2.prototype._emitBitmapSizeChanged = function(t3, i2) {
      var n2 = this;
      this._bitmapSizeChangedListeners.forEach((function(s2) {
        return s2.call(n2, t3, i2);
      }));
    }, t2.prototype._suggestNewBitmapSize = function(t3) {
      var i2 = this._suggestedBitmapSize, n2 = on(this._transformBitmapSize(t3, this._canvasElementClientSize)), s2 = _n(this.bitmapSize, n2) ? null : n2;
      null === i2 && null === s2 || null !== i2 && null !== s2 && _n(i2, s2) || (this._suggestedBitmapSize = s2, this._emitSuggestedBitmapSizeChanged(i2, s2));
    }, t2.prototype._emitSuggestedBitmapSizeChanged = function(t3, i2) {
      var n2 = this;
      this._suggestedBitmapSizeChangedListeners.forEach((function(s2) {
        return s2.call(n2, t3, i2);
      }));
    }, t2.prototype._chooseAndInitObserver = function() {
      var t3 = this;
      this._allowResizeObserver ? new Promise((function(t4) {
        var i2 = new ResizeObserver((function(n2) {
          t4(n2.every((function(t5) {
            return "devicePixelContentBoxSize" in t5;
          }))), i2.disconnect();
        }));
        i2.observe(document.body, { box: "device-pixel-content-box" });
      })).catch((function() {
        return false;
      })).then((function(i2) {
        return i2 ? t3._initResizeObserver() : t3._initDevicePixelRatioObservable();
      })) : this._initDevicePixelRatioObservable();
    }, t2.prototype._initDevicePixelRatioObservable = function() {
      var t3 = this;
      if (null !== this._canvasElement) {
        var i2 = dn(this._canvasElement);
        if (null === i2) throw new Error("No window is associated with the canvas");
        this._devicePixelRatioObservable = (function(t4) {
          return new un(t4);
        })(i2), this._devicePixelRatioObservable.subscribe((function() {
          return t3._invalidateBitmapSize();
        })), this._invalidateBitmapSize();
      }
    }, t2.prototype._invalidateBitmapSize = function() {
      var t3, i2;
      if (null !== this._canvasElement) {
        var n2 = dn(this._canvasElement);
        if (null !== n2) {
          var s2 = null !== (i2 = null === (t3 = this._devicePixelRatioObservable) || void 0 === t3 ? void 0 : t3.value) && void 0 !== i2 ? i2 : n2.devicePixelRatio, e2 = this._canvasElement.getClientRects(), r2 = void 0 !== e2[0] ? (function(t4, i3) {
            return on({ width: Math.round(t4.left * i3 + t4.width * i3) - Math.round(t4.left * i3), height: Math.round(t4.top * i3 + t4.height * i3) - Math.round(t4.top * i3) });
          })(e2[0], s2) : on({ width: this._canvasElementClientSize.width * s2, height: this._canvasElementClientSize.height * s2 });
          this._suggestNewBitmapSize(r2);
        }
      }
    }, t2.prototype._initResizeObserver = function() {
      var t3 = this;
      null !== this._canvasElement && (this._canvasElementResizeObserver = new ResizeObserver((function(i2) {
        var n2 = i2.find((function(i3) {
          return i3.target === t3._canvasElement;
        }));
        if (n2 && n2.devicePixelContentBoxSize && n2.devicePixelContentBoxSize[0]) {
          var s2 = n2.devicePixelContentBoxSize[0], e2 = on({ width: s2.inlineSize, height: s2.blockSize });
          t3._suggestNewBitmapSize(e2);
        }
      })), this._canvasElementResizeObserver.observe(this._canvasElement, { box: "device-pixel-content-box" }));
    }, t2;
  })();
  function dn(t2) {
    return t2.ownerDocument.defaultView;
  }
  var fn = (function() {
    function t2(t3, i2, n2) {
      if (0 === i2.width || 0 === i2.height) throw new TypeError("Rendering target could only be created on a media with positive width and height");
      if (this._mediaSize = i2, 0 === n2.width || 0 === n2.height) throw new TypeError("Rendering target could only be created using a bitmap with positive integer width and height");
      this._bitmapSize = n2, this._context = t3;
    }
    return t2.prototype.useMediaCoordinateSpace = function(t3) {
      try {
        return this._context.save(), this._context.setTransform(1, 0, 0, 1, 0, 0), this._context.scale(this._horizontalPixelRatio, this._verticalPixelRatio), t3({ context: this._context, mediaSize: this._mediaSize });
      } finally {
        this._context.restore();
      }
    }, t2.prototype.useBitmapCoordinateSpace = function(t3) {
      try {
        return this._context.save(), this._context.setTransform(1, 0, 0, 1, 0, 0), t3({ context: this._context, mediaSize: this._mediaSize, bitmapSize: this._bitmapSize, horizontalPixelRatio: this._horizontalPixelRatio, verticalPixelRatio: this._verticalPixelRatio });
      } finally {
        this._context.restore();
      }
    }, Object.defineProperty(t2.prototype, "_horizontalPixelRatio", { get: function() {
      return this._bitmapSize.width / this._mediaSize.width;
    }, enumerable: false, configurable: true }), Object.defineProperty(t2.prototype, "_verticalPixelRatio", { get: function() {
      return this._bitmapSize.height / this._mediaSize.height;
    }, enumerable: false, configurable: true }), t2;
  })();
  function pn(t2, i2) {
    var n2 = t2.canvasElementClientSize;
    if (0 === n2.width || 0 === n2.height) return null;
    var s2 = t2.bitmapSize;
    if (0 === s2.width || 0 === s2.height) return null;
    var e2 = t2.canvasElement.getContext("2d", i2);
    return null === e2 ? null : new fn(e2, n2, s2);
  }
  var vn = "undefined" != typeof window;
  function mn() {
    return !!vn && window.navigator.userAgent.toLowerCase().indexOf("firefox") > -1;
  }
  function wn() {
    return !!vn && /iPhone|iPad|iPod/.test(window.navigator.platform);
  }
  function gn(t2, i2) {
    switch (t2) {
      case "custom":
        return void 0 !== i2 ? "custom-object" : "series";
      case "price-line":
        return "custom-price-line";
      case "marker":
        return "series-marker";
      case "primitive":
        return "primitive";
      default:
        return "series";
    }
  }
  function Mn(t2) {
    return t2 + t2 % 2;
  }
  function bn(t2) {
    vn && void 0 !== window.chrome && t2.addEventListener("mousedown", ((t3) => {
      if (1 === t3.button) return t3.preventDefault(), false;
    }));
  }
  var Sn = class {
    constructor(t2, i2, n2) {
      this.Rf = 0, this.Df = null, this.If = { _t: Number.NEGATIVE_INFINITY, ut: Number.POSITIVE_INFINITY }, this.Vf = 0, this.Ef = null, this.Bf = { _t: Number.NEGATIVE_INFINITY, ut: Number.POSITIVE_INFINITY }, this.Af = null, this.zf = false, this.Lf = null, this.Of = null, this.Nf = false, this.Ff = false, this.Wf = false, this.Hf = null, this.Uf = null, this.$f = null, this.jf = null, this.qf = null, this.Yf = null, this.Kf = null, this.Gf = 0, this.Zf = false, this.Xf = false, this.Jf = false, this.Qf = 0, this.tp = null, this.ip = !wn(), this.np = (t3) => {
        this.sp(t3);
      }, this.ep = (t3) => {
        if (this.rp(t3)) {
          const i3 = this.hp(t3);
          if (++this.Vf, this.Ef && this.Vf > 1) {
            const { ap: n3 } = this.lp(yn(t3), this.Bf);
            n3 < 30 && !this.Wf && this.op(i3, this.up._p), this.cp();
          }
        } else {
          const i3 = this.hp(t3);
          if (++this.Rf, this.Df && this.Rf > 1) {
            const { ap: n3 } = this.lp(yn(t3), this.If);
            n3 < 5 && !this.Ff && this.dp(i3, this.up.fp), this.pp();
          }
        }
      }, this.vp = t2, this.up = i2, this.yn = n2, this.mp();
    }
    m() {
      null !== this.Hf && (this.Hf(), this.Hf = null), null !== this.Uf && (this.Uf(), this.Uf = null), null !== this.jf && (this.jf(), this.jf = null), null !== this.qf && (this.qf(), this.qf = null), null !== this.Yf && (this.Yf(), this.Yf = null), null !== this.$f && (this.$f(), this.$f = null), this.wp(), this.pp();
    }
    gp(t2) {
      this.jf && this.jf();
      const i2 = this.Mp.bind(this);
      if (this.jf = () => {
        this.vp.removeEventListener("mousemove", i2);
      }, this.vp.addEventListener("mousemove", i2), this.rp(t2)) return;
      const n2 = this.hp(t2);
      this.dp(n2, this.up.bp), this.ip = true;
    }
    pp() {
      null !== this.Df && clearTimeout(this.Df), this.Rf = 0, this.Df = null, this.If = { _t: Number.NEGATIVE_INFINITY, ut: Number.POSITIVE_INFINITY };
    }
    cp() {
      null !== this.Ef && clearTimeout(this.Ef), this.Vf = 0, this.Ef = null, this.Bf = { _t: Number.NEGATIVE_INFINITY, ut: Number.POSITIVE_INFINITY };
    }
    Mp(t2) {
      if (this.Jf || null !== this.Of) return;
      if (this.rp(t2)) return;
      const i2 = this.hp(t2);
      this.dp(i2, this.up.Sp), this.ip = true;
    }
    xp(t2) {
      const i2 = kn(t2.changedTouches, a(this.tp));
      if (null === i2) return;
      if (this.Qf = Pn(t2), null !== this.Kf) return;
      if (this.Xf) return;
      this.Zf = true;
      const n2 = this.lp(yn(i2), a(this.Of)), { Cp: s2, yp: e2, ap: r2 } = n2;
      if (this.Nf || !(r2 < 5)) {
        if (!this.Nf) {
          const t3 = 0.5 * s2, i3 = e2 >= t3 && !this.yn.Pp(), n3 = t3 > e2 && !this.yn.kp();
          i3 || n3 || (this.Xf = true), this.Nf = true, this.Wf = true, this.wp(), this.cp();
        }
        if (!this.Xf) {
          const n3 = this.hp(t2, i2);
          this.op(n3, this.up.Tp), Cn(t2);
        }
      }
    }
    Rp(t2) {
      if (0 !== t2.button) return;
      const i2 = this.lp(yn(t2), a(this.Lf)), { ap: n2 } = i2;
      if (n2 >= 5 && (this.Ff = true, this.pp()), this.Ff) {
        const i3 = this.hp(t2);
        this.dp(i3, this.up.Dp);
      }
    }
    lp(t2, i2) {
      const n2 = Math.abs(i2._t - t2._t), s2 = Math.abs(i2.ut - t2.ut);
      return { Cp: n2, yp: s2, ap: n2 + s2 };
    }
    Ip(t2) {
      let i2 = kn(t2.changedTouches, a(this.tp));
      if (null === i2 && 0 === t2.touches.length && (i2 = t2.changedTouches[0]), null === i2) return;
      this.tp = null, this.Qf = Pn(t2), this.wp(), this.Of = null, this.Yf && (this.Yf(), this.Yf = null);
      const n2 = this.hp(t2, i2);
      if (this.op(n2, this.up.Vp), ++this.Vf, this.Ef && this.Vf > 1) {
        const { ap: t3 } = this.lp(yn(i2), this.Bf);
        t3 < 30 && !this.Wf && this.op(n2, this.up._p), this.cp();
      } else this.Wf || (this.op(n2, this.up.Ep), this.up.Ep && Cn(t2));
      0 === this.Vf && Cn(t2), 0 === t2.touches.length && this.zf && (this.zf = false, Cn(t2));
    }
    sp(t2) {
      if (0 !== t2.button) return;
      const i2 = this.hp(t2);
      if (this.Lf = null, this.Jf = false, this.qf && (this.qf(), this.qf = null), mn()) {
        this.vp.ownerDocument.documentElement.removeEventListener("mouseleave", this.np);
      }
      if (!this.rp(t2)) if (this.dp(i2, this.up.Bp), ++this.Rf, this.Df && this.Rf > 1) {
        const { ap: n2 } = this.lp(yn(t2), this.If);
        n2 < 5 && !this.Ff && this.dp(i2, this.up.fp), this.pp();
      } else this.Ff || this.dp(i2, this.up.Ap);
    }
    wp() {
      null !== this.Af && (clearTimeout(this.Af), this.Af = null);
    }
    zp(t2) {
      if (null !== this.tp) return;
      const i2 = t2.changedTouches[0];
      this.tp = i2.identifier, this.Qf = Pn(t2);
      const n2 = this.vp.ownerDocument.documentElement;
      this.Wf = false, this.Nf = false, this.Xf = false, this.Of = yn(i2), this.Yf && (this.Yf(), this.Yf = null);
      {
        const i3 = this.xp.bind(this), s3 = this.Ip.bind(this);
        this.Yf = () => {
          n2.removeEventListener("touchmove", i3), n2.removeEventListener("touchend", s3);
        }, n2.addEventListener("touchmove", i3, { passive: false }), n2.addEventListener("touchend", s3, { passive: false }), this.wp(), this.Af = setTimeout(this.Lp.bind(this, t2), 240);
      }
      const s2 = this.hp(t2, i2);
      this.op(s2, this.up.Op), this.Ef || (this.Vf = 0, this.Ef = setTimeout(this.cp.bind(this), 500), this.Bf = yn(i2));
    }
    Np(t2) {
      if (0 !== t2.button) return;
      const i2 = this.vp.ownerDocument.documentElement;
      mn() && i2.addEventListener("mouseleave", this.np), this.Ff = false, this.Lf = yn(t2), this.qf && (this.qf(), this.qf = null);
      {
        const t3 = this.Rp.bind(this), n3 = this.sp.bind(this);
        this.qf = () => {
          i2.removeEventListener("mousemove", t3), i2.removeEventListener("mouseup", n3);
        }, i2.addEventListener("mousemove", t3), i2.addEventListener("mouseup", n3);
      }
      if (this.Jf = true, this.rp(t2)) return;
      const n2 = this.hp(t2);
      this.dp(n2, this.up.Fp), this.Df || (this.Rf = 0, this.Df = setTimeout(this.pp.bind(this), 500), this.If = yn(t2));
    }
    mp() {
      this.vp.addEventListener("mouseenter", this.gp.bind(this)), this.vp.addEventListener("touchcancel", this.wp.bind(this));
      {
        const t2 = this.vp.ownerDocument, i2 = (t3) => {
          this.up.Wp && (t3.composed && this.vp.contains(t3.composedPath()[0]) || t3.target && this.vp.contains(t3.target) || this.up.Wp());
        };
        this.Uf = () => {
          t2.removeEventListener("touchstart", i2);
        }, this.Hf = () => {
          t2.removeEventListener("mousedown", i2);
        }, t2.addEventListener("mousedown", i2), t2.addEventListener("touchstart", i2, { passive: true });
      }
      wn() && (this.$f = () => {
        this.vp.removeEventListener("dblclick", this.ep);
      }, this.vp.addEventListener("dblclick", this.ep)), this.vp.addEventListener("mouseleave", this.Hp.bind(this)), this.vp.addEventListener("touchstart", this.zp.bind(this), { passive: true }), bn(this.vp), this.vp.addEventListener("mousedown", this.Np.bind(this)), this.Up(), this.vp.addEventListener("touchmove", (() => {
      }), { passive: false });
    }
    Up() {
      void 0 === this.up.$p && void 0 === this.up.jp && void 0 === this.up.qp || (this.vp.addEventListener("touchstart", ((t2) => this.Yp(t2.touches)), { passive: true }), this.vp.addEventListener("touchmove", ((t2) => {
        if (2 === t2.touches.length && null !== this.Kf && void 0 !== this.up.jp) {
          const i2 = xn(t2.touches[0], t2.touches[1]) / this.Gf;
          this.up.jp(this.Kf, i2), Cn(t2);
        }
      }), { passive: false }), this.vp.addEventListener("touchend", ((t2) => {
        this.Yp(t2.touches);
      })));
    }
    Yp(t2) {
      1 === t2.length && (this.Zf = false), 2 !== t2.length || this.Zf || this.zf ? this.Kp() : this.Gp(t2);
    }
    Gp(t2) {
      const i2 = this.vp.getBoundingClientRect() || { left: 0, top: 0 };
      this.Kf = { _t: (t2[0].clientX - i2.left + (t2[1].clientX - i2.left)) / 2, ut: (t2[0].clientY - i2.top + (t2[1].clientY - i2.top)) / 2 }, this.Gf = xn(t2[0], t2[1]), void 0 !== this.up.$p && this.up.$p(), this.wp();
    }
    Kp() {
      null !== this.Kf && (this.Kf = null, void 0 !== this.up.qp && this.up.qp());
    }
    Hp(t2) {
      if (this.jf && this.jf(), this.rp(t2)) return;
      if (!this.ip) return;
      const i2 = this.hp(t2);
      this.dp(i2, this.up.Zp), this.ip = !wn();
    }
    Lp(t2) {
      const i2 = kn(t2.touches, a(this.tp));
      if (null === i2) return;
      const n2 = this.hp(t2, i2);
      this.op(n2, this.up.Xp), this.Wf = true, this.zf = true;
    }
    rp(t2) {
      return t2.sourceCapabilities && void 0 !== t2.sourceCapabilities.firesTouchEvents ? t2.sourceCapabilities.firesTouchEvents : Pn(t2) < this.Qf + 500;
    }
    op(t2, i2) {
      i2 && i2.call(this.up, t2);
    }
    dp(t2, i2) {
      i2 && i2.call(this.up, t2);
    }
    hp(t2, i2) {
      const n2 = i2 || t2, s2 = this.vp.getBoundingClientRect() || { left: 0, top: 0 };
      return { clientX: n2.clientX, clientY: n2.clientY, pageX: n2.pageX, pageY: n2.pageY, screenX: n2.screenX, screenY: n2.screenY, localX: n2.clientX - s2.left, localY: n2.clientY - s2.top, ctrlKey: t2.ctrlKey, altKey: t2.altKey, shiftKey: t2.shiftKey, metaKey: t2.metaKey, Jp: !t2.type.startsWith("mouse") && "contextmenu" !== t2.type && "click" !== t2.type, Qp: t2.type, tv: n2.target, xu: t2.view, iv: () => {
        "touchstart" !== t2.type && Cn(t2);
      } };
    }
  };
  function xn(t2, i2) {
    const n2 = t2.clientX - i2.clientX, s2 = t2.clientY - i2.clientY;
    return Math.sqrt(n2 * n2 + s2 * s2);
  }
  function Cn(t2) {
    t2.cancelable && t2.preventDefault();
  }
  function yn(t2) {
    return { _t: t2.pageX, ut: t2.pageY };
  }
  function Pn(t2) {
    return t2.timeStamp || performance.now();
  }
  function kn(t2, i2) {
    for (let n2 = 0; n2 < t2.length; ++n2) if (t2[n2].identifier === i2) return t2[n2];
    return null;
  }
  var Tn = class {
    constructor(t2, i2, n2) {
      this.nv = null, this.sv = null, this.ev = true, this.rv = null, this.hv = t2, this.av = t2.lv()[i2], this.ov = t2.lv()[n2], this._v = document.createElement("tr"), this._v.style.height = "1px", this.uv = document.createElement("td"), this.uv.style.position = "relative", this.uv.style.padding = "0", this.uv.style.margin = "0", this.uv.setAttribute("colspan", "3"), this.cv(), this._v.appendChild(this.uv), this.ev = this.hv.N().layout.panes.enableResize, this.ev ? this.dv() : (this.nv = null, this.sv = null);
    }
    m() {
      null !== this.sv && this.sv.m();
    }
    fv() {
      return this._v;
    }
    pv() {
      return on({ width: this.av.pv().width, height: 1 });
    }
    vv() {
      return on({ width: this.av.vv().width, height: 1 * window.devicePixelRatio });
    }
    mv(t2, i2, n2) {
      const s2 = this.vv();
      t2.fillStyle = this.hv.N().layout.panes.separatorColor, t2.fillRect(i2, n2, s2.width, s2.height);
    }
    Pt() {
      this.cv(), this.hv.N().layout.panes.enableResize !== this.ev && (this.ev = this.hv.N().layout.panes.enableResize, this.ev ? this.dv() : (null !== this.nv && (this.uv.removeChild(this.nv.wv), this.uv.removeChild(this.nv.gv), this.nv = null), null !== this.sv && (this.sv.m(), this.sv = null)));
    }
    dv() {
      const t2 = document.createElement("div"), i2 = t2.style;
      i2.position = "fixed", i2.display = "none", i2.zIndex = "49", i2.top = "0", i2.left = "0", i2.width = "100%", i2.height = "100%", i2.cursor = "row-resize", this.uv.appendChild(t2);
      const n2 = document.createElement("div"), s2 = n2.style;
      s2.position = "absolute", s2.zIndex = "50", s2.top = "-4px", s2.height = "9px", s2.width = "100%", s2.backgroundColor = "", s2.cursor = "row-resize", this.uv.appendChild(n2);
      const e2 = { bp: this.Mv.bind(this), Zp: this.bv.bind(this), Fp: this.Sv.bind(this), Op: this.Sv.bind(this), Dp: this.xv.bind(this), Tp: this.xv.bind(this), Bp: this.Cv.bind(this), Vp: this.Cv.bind(this) };
      this.sv = new Sn(n2, e2, { Pp: () => false, kp: () => true }), this.nv = { gv: n2, wv: t2 };
    }
    cv() {
      this.uv.style.background = this.hv.N().layout.panes.separatorColor;
    }
    Mv(t2) {
      null !== this.nv && (this.nv.gv.style.backgroundColor = this.hv.N().layout.panes.separatorHoverColor);
    }
    bv(t2) {
      null !== this.nv && null === this.rv && (this.nv.gv.style.backgroundColor = "");
    }
    Sv(t2) {
      if (null === this.nv) return;
      const i2 = this.av.yv().F_() + this.ov.yv().F_(), n2 = i2 / (this.av.pv().height + this.ov.pv().height), s2 = 30 * n2;
      i2 <= 2 * s2 || (this.rv = { Pv: t2.pageY, kv: this.av.yv().F_(), Tv: i2 - s2, Rv: i2, Dv: n2, Iv: s2 }, this.nv.wv.style.display = "block");
    }
    xv(t2) {
      const i2 = this.rv;
      if (null === i2) return;
      const n2 = (t2.pageY - i2.Pv) * i2.Dv, s2 = Jt(i2.kv + n2, i2.Iv, i2.Tv);
      this.av.yv().W_(s2), this.ov.yv().W_(i2.Rv - s2), this.hv.Qt().ka();
    }
    Cv(t2) {
      null !== this.rv && null !== this.nv && (this.rv = null, this.nv.wv.style.display = "none");
    }
  };
  function Rn(t2, i2) {
    return t2.Vv - i2.Vv;
  }
  function Dn(t2, i2, n2) {
    const s2 = (t2.Vv - i2.Vv) / (t2.wt - i2.wt);
    return Math.sign(s2) * Math.min(Math.abs(s2), n2);
  }
  var In = class {
    constructor(t2, i2, n2, s2) {
      this.Ev = null, this.Bv = null, this.Av = null, this.zv = null, this.Lv = null, this.Ov = 0, this.Nv = 0, this.Fv = t2, this.Wv = i2, this.Hv = n2, this.Ps = s2;
    }
    Uv(t2, i2) {
      if (null !== this.Ev) {
        if (this.Ev.wt === i2) return void (this.Ev.Vv = t2);
        if (Math.abs(this.Ev.Vv - t2) < this.Ps) return;
      }
      this.zv = this.Av, this.Av = this.Bv, this.Bv = this.Ev, this.Ev = { wt: i2, Vv: t2 };
    }
    me(t2, i2) {
      if (null === this.Ev || null === this.Bv) return;
      if (i2 - this.Ev.wt > 50) return;
      let n2 = 0;
      const s2 = Dn(this.Ev, this.Bv, this.Wv), e2 = Rn(this.Ev, this.Bv), r2 = [s2], h2 = [e2];
      if (n2 += e2, null !== this.Av) {
        const t3 = Dn(this.Bv, this.Av, this.Wv);
        if (Math.sign(t3) === Math.sign(s2)) {
          const i3 = Rn(this.Bv, this.Av);
          if (r2.push(t3), h2.push(i3), n2 += i3, null !== this.zv) {
            const t4 = Dn(this.Av, this.zv, this.Wv);
            if (Math.sign(t4) === Math.sign(s2)) {
              const i4 = Rn(this.Av, this.zv);
              r2.push(t4), h2.push(i4), n2 += i4;
            }
          }
        }
      }
      let a2 = 0;
      for (let t3 = 0; t3 < r2.length; ++t3) a2 += h2[t3] / n2 * r2[t3];
      Math.abs(a2) < this.Fv || (this.Lv = { Vv: t2, wt: i2 }, this.Nv = a2, this.Ov = (function(t3, i3) {
        const n3 = Math.log(i3);
        return Math.log(1 * n3 / -t3) / n3;
      })(Math.abs(a2), this.Hv));
    }
    Gc(t2) {
      const i2 = a(this.Lv), n2 = t2 - i2.wt;
      return i2.Vv + this.Nv * (Math.pow(this.Hv, n2) - 1) / Math.log(this.Hv);
    }
    Kc(t2) {
      return null === this.Lv || this.$v(t2) === this.Ov;
    }
    $v(t2) {
      const i2 = t2 - a(this.Lv).wt;
      return Math.min(i2, this.Ov);
    }
  };
  var Vn = class {
    constructor(t2, i2) {
      this.jv = void 0, this.qv = void 0, this.Yv = void 0, this.vn = false, this.Kv = t2, this.Gv = i2, this.Zv();
    }
    Pt() {
      this.Zv();
    }
    Xv() {
      this.jv && this.Kv.removeChild(this.jv), this.qv && this.Kv.removeChild(this.qv), this.jv = void 0, this.qv = void 0;
    }
    Jv() {
      return this.vn !== this.Qv() || this.Yv !== this.tm();
    }
    tm() {
      return this.Gv.Qt().Xi().J(this.Gv.N().layout.textColor) > 160 ? "dark" : "light";
    }
    Qv() {
      return this.Gv.N().layout.attributionLogo;
    }
    im() {
      const t2 = new URL(location.href);
      return t2.hostname ? "&utm_source=" + t2.hostname + t2.pathname : "";
    }
    Zv() {
      this.Jv() && (this.Xv(), this.vn = this.Qv(), this.vn && (this.Yv = this.tm(), this.qv = document.createElement("style"), this.qv.innerText = "a#tv-attr-logo{--fill:#131722;--stroke:#fff;position:absolute;left:10px;bottom:10px;height:19px;width:35px;margin:0;padding:0;border:0;z-index:3;}a#tv-attr-logo[data-dark]{--fill:#D1D4DC;--stroke:#131722;}", this.jv = document.createElement("a"), this.jv.href = `https://www.tradingview.com/?utm_medium=lwc-link&utm_campaign=lwc-chart${this.im()}`, this.jv.title = "Charting by TradingView", this.jv.id = "tv-attr-logo", this.jv.target = "_blank", this.jv.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="35" height="19" fill="none"><g fill-rule="evenodd" clip-path="url(#a)" clip-rule="evenodd"><path fill="var(--stroke)" d="M2 0H0v10h6v9h21.4l.5-1.3 6-15 1-2.7H23.7l-.5 1.3-.2.6a5 5 0 0 0-7-.9V0H2Zm20 17h4l5.2-13 .8-2h-7l-1 2.5-.2.5-1.5 3.8-.3.7V17Zm-.8-10a3 3 0 0 0 .7-2.7A3 3 0 1 0 16.8 7h4.4ZM14 7V2H2v6h6v9h4V7h2Z"/><path fill="var(--fill)" d="M14 2H2v6h6v9h6V2Zm12 15h-7l6-15h7l-6 15Zm-7-9a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z"/></g><defs><clipPath id="a"><path fill="var(--stroke)" d="M0 0h35v19H0z"/></clipPath></defs></svg>', this.jv.toggleAttribute("data-dark", "dark" === this.Yv), this.Kv.appendChild(this.qv), this.Kv.appendChild(this.jv)));
    }
  };
  function En(t2, i2) {
    const n2 = a(t2.ownerDocument).createElement("canvas");
    t2.appendChild(n2);
    const s2 = new cn(n2, (e2 = { options: { allowResizeObserver: true }, transform: (t3, i3) => ({ width: Math.max(t3.width, i3.width), height: Math.max(t3.height, i3.height) }) }).transform, e2.options);
    var e2;
    return s2.resizeCanvasElement(i2), s2;
  }
  function Bn(t2) {
    t2.width = 1, t2.height = 1, t2.getContext("2d")?.clearRect(0, 0, 1, 1);
  }
  function An(t2, i2, n2, s2) {
    t2.qh && t2.qh(i2, n2, s2);
  }
  function zn(t2, i2, n2, s2) {
    t2.st(i2, n2, s2);
  }
  function Ln(t2, i2, n2, s2) {
    On(t2(n2, s2), i2, s2);
  }
  function On(t2, i2, n2) {
    for (const s2 of t2) {
      const t3 = s2.Tt(n2);
      null !== t3 && i2(t3);
    }
  }
  function Nn(t2, i2) {
    return (n2) => {
      if (!(function(t3) {
        return void 0 !== t3.Ft;
      })(n2)) return [];
      return (n2.Ft()?.pl() ?? "") !== i2 ? [] : n2.Qa?.(t2) ?? [];
    };
  }
  function Fn(t2, i2, n2, s2) {
    if (!t2.length) return;
    let e2 = 0;
    const r2 = t2[0].$t(s2, true);
    let h2 = 1 === i2 ? n2 / 2 - (t2[0].Hi() - r2 / 2) : t2[0].Hi() - r2 / 2 - n2 / 2;
    h2 = Math.max(0, h2);
    for (let r3 = 1; r3 < t2.length; r3++) {
      const a2 = t2[r3], l2 = t2[r3 - 1], o2 = l2.$t(s2, false), _2 = a2.Hi(), u2 = l2.Hi();
      if (1 === i2 ? _2 > u2 - o2 : _2 < u2 + o2) {
        const s3 = u2 - o2 * i2;
        a2.Ui(s3);
        const r4 = s3 - i2 * o2 / 2;
        if ((1 === i2 ? r4 < 0 : r4 > n2) && h2 > 0) {
          const s4 = 1 === i2 ? -1 - r4 : r4 - n2, a3 = Math.min(s4, h2);
          for (let n3 = e2; n3 < t2.length; n3++) t2[n3].Ui(t2[n3].Hi() + i2 * a3);
          h2 -= a3;
        }
      } else e2 = r3, h2 = 1 === i2 ? u2 - o2 - _2 : _2 - (u2 + o2);
    }
  }
  var Wn = class {
    constructor(t2, i2, n2, s2) {
      this.Ki = null, this.nm = null, this.sm = false, this.rm = new it(200), this.hm = null, this.am = 0, this.lm = false, this.om = () => {
        this.lm || this.yt._m().Qt().mr();
      }, this.um = () => {
        this.lm || this.yt._m().Qt().mr();
      }, this.yt = t2, this.yn = i2, this.Ro = i2.layout, this.bd = n2, this.dm = "left" === s2, this.fm = Nn("normal", s2), this.pm = Nn("top", s2), this.vm = Nn("bottom", s2), this.uv = document.createElement("div"), this.uv.style.height = "100%", this.uv.style.overflow = "hidden", this.uv.style.width = "25px", this.uv.style.left = "0", this.uv.style.position = "relative", this.wm = En(this.uv, on({ width: 16, height: 16 })), this.wm.subscribeSuggestedBitmapSizeChanged(this.om);
      const e2 = this.wm.canvasElement;
      e2.style.position = "absolute", e2.style.zIndex = "1", e2.style.left = "0", e2.style.top = "0", this.gm = En(this.uv, on({ width: 16, height: 16 })), this.gm.subscribeSuggestedBitmapSizeChanged(this.um);
      const r2 = this.gm.canvasElement;
      r2.style.position = "absolute", r2.style.zIndex = "2", r2.style.left = "0", r2.style.top = "0";
      const h2 = { Fp: this.Sv.bind(this), Op: this.Sv.bind(this), Dp: this.xv.bind(this), Tp: this.xv.bind(this), Wp: this.Mm.bind(this), Bp: this.Cv.bind(this), Vp: this.Cv.bind(this), fp: this.bm.bind(this), _p: this.bm.bind(this), bp: this.Sm.bind(this), Zp: this.bv.bind(this) };
      this.sv = new Sn(this.gm.canvasElement, h2, { Pp: () => !this.yn.handleScroll.vertTouchDrag, kp: () => true });
    }
    m() {
      this.sv.m(), this.gm.unsubscribeSuggestedBitmapSizeChanged(this.um), Bn(this.gm.canvasElement), this.gm.dispose(), this.wm.unsubscribeSuggestedBitmapSizeChanged(this.om), Bn(this.wm.canvasElement), this.wm.dispose(), null !== this.Ki && this.Ki.__().u(this), this.Ki = null;
    }
    fv() {
      return this.uv;
    }
    P() {
      return this.Ro.fontSize;
    }
    xm() {
      const t2 = this.bd.N();
      return this.hm !== t2.k && (this.rm.Os(), this.hm = t2.k), t2;
    }
    Cm() {
      if (null === this.Ki) return 0;
      let t2 = 0;
      const i2 = this.xm(), n2 = a(this.wm.canvasElement.getContext("2d", { colorSpace: this.yt._m().N().layout.colorSpace }));
      n2.save();
      const s2 = this.Ki.Ll();
      n2.font = this.ym(), s2.length > 0 && (t2 = Math.max(this.rm.Ii(n2, s2[0].eo), this.rm.Ii(n2, s2[s2.length - 1].eo)));
      const e2 = this.Pm();
      for (let i3 = e2.length; i3--; ) {
        const s3 = this.rm.Ii(n2, e2[i3].ri());
        s3 > t2 && (t2 = s3);
      }
      const r2 = this.Ki.zt();
      if (null !== r2 && null !== this.nm && (2 !== (h2 = this.yn.crosshair).mode && h2.horzLine.visible && h2.horzLine.labelVisible)) {
        const i3 = this.Ki.Tn(1, r2), s3 = this.Ki.Tn(this.nm.height - 2, r2);
        t2 = Math.max(t2, this.rm.Ii(n2, this.Ki.Ji(Math.floor(Math.min(i3, s3)) + 0.11111111111111, r2)), this.rm.Ii(n2, this.Ki.Ji(Math.ceil(Math.max(i3, s3)) - 0.11111111111111, r2)));
      }
      var h2;
      n2.restore();
      const l2 = t2 || 34;
      return Mn(Math.ceil(i2.S + i2.C + i2.V + i2.B + 5 + l2));
    }
    km(t2) {
      null !== this.nm && _n(this.nm, t2) || (this.nm = t2, this.lm = true, this.wm.resizeCanvasElement(t2), this.gm.resizeCanvasElement(t2), this.lm = false, this.uv.style.width = `${t2.width}px`, this.uv.style.height = `${t2.height}px`);
    }
    Tm() {
      return a(this.nm).width;
    }
    un(t2) {
      this.Ki !== t2 && (null !== this.Ki && this.Ki.__().u(this), this.Ki = t2, t2.__().i(this.wo.bind(this), this));
    }
    Ft() {
      return this.Ki;
    }
    Os() {
      const t2 = this.yt.yv();
      this.yt._m().Qt().au(t2, a(this.Ft()));
    }
    Rm(t2) {
      if (null === this.nm) return;
      const i2 = { colorSpace: this.yt._m().N().layout.colorSpace };
      if (1 !== t2) {
        this.Dm(), this.wm.applySuggestedBitmapSize();
        const t3 = pn(this.wm, i2);
        null !== t3 && (t3.useBitmapCoordinateSpace(((t4) => {
          this.Im(t4), this.Vm(t4);
        })), this.yt.Em(t3, this.vm), this.Bm(t3), this.yt.Em(t3, this.fm), this.Am(t3));
      }
      this.gm.applySuggestedBitmapSize();
      const n2 = pn(this.gm, i2);
      null !== n2 && (n2.useBitmapCoordinateSpace((({ context: t3, bitmapSize: i3 }) => {
        t3.clearRect(0, 0, i3.width, i3.height);
      })), this.zm(n2), this.yt.Em(n2, this.pm));
    }
    vv() {
      return this.wm.bitmapSize;
    }
    mv(t2, i2, n2, s2) {
      const e2 = this.vv();
      if (e2.width > 0 && e2.height > 0 && (t2.drawImage(this.wm.canvasElement, i2, n2), s2)) {
        const s3 = this.gm.canvasElement;
        t2.drawImage(s3, i2, n2);
      }
    }
    Pt() {
      this.Ki?.Ll();
    }
    Sv(t2) {
      if (null === this.Ki || this.Ki.Zi() || !this.yn.handleScale.axisPressedMouseMove.price) return;
      const i2 = this.yt._m().Qt(), n2 = this.yt.yv();
      this.sm = true, i2.Q_(n2, this.Ki, t2.localY);
    }
    xv(t2) {
      if (null === this.Ki || !this.yn.handleScale.axisPressedMouseMove.price) return;
      const i2 = this.yt._m().Qt(), n2 = this.yt.yv(), s2 = this.Ki;
      i2.tu(n2, s2, t2.localY);
    }
    Mm() {
      if (null === this.Ki || !this.yn.handleScale.axisPressedMouseMove.price) return;
      const t2 = this.yt._m().Qt(), i2 = this.yt.yv(), n2 = this.Ki;
      this.sm && (this.sm = false, t2.iu(i2, n2));
    }
    Cv(t2) {
      if (null === this.Ki || !this.yn.handleScale.axisPressedMouseMove.price) return;
      const i2 = this.yt._m().Qt(), n2 = this.yt.yv();
      this.sm = false, i2.iu(n2, this.Ki);
    }
    bm(t2) {
      this.yn.handleScale.axisDoubleClickReset.price && this.Os();
    }
    Sm(t2) {
      if (null === this.Ki) return;
      !this.yt._m().Qt().N().handleScale.axisPressedMouseMove.price || this.Ki.je() || this.Ki.No() || this.Lm(1);
    }
    bv(t2) {
      this.Lm(0);
    }
    Pm() {
      const t2 = [], i2 = null === this.Ki ? void 0 : this.Ki;
      return ((n2) => {
        for (let s2 = 0; s2 < n2.length; ++s2) {
          const e2 = n2[s2].qn(this.yt.yv(), i2);
          for (let i3 = 0; i3 < e2.length; i3++) t2.push(e2[i3]);
        }
      })(this.yt.yv().Dt()), t2;
    }
    Im({ context: t2, bitmapSize: i2 }) {
      const { width: n2, height: s2 } = i2, e2 = this.yt.yv().Qt(), r2 = e2.$(), h2 = e2.af();
      r2 === h2 ? E(t2, 0, 0, n2, s2, r2) : z(t2, 0, 0, n2, s2, r2, h2);
    }
    Vm({ context: t2, bitmapSize: i2, horizontalPixelRatio: n2 }) {
      if (null === this.nm || null === this.Ki || !this.Ki.N().borderVisible) return;
      t2.fillStyle = this.Ki.N().borderColor;
      const s2 = Math.max(1, Math.floor(this.xm().S * n2));
      let e2;
      e2 = this.dm ? i2.width - s2 : 0, t2.fillRect(e2, 0, s2, i2.height);
    }
    Bm(t2) {
      if (null === this.nm || null === this.Ki) return;
      const i2 = this.Ki.Ll(), n2 = this.Ki.N(), s2 = this.xm(), e2 = this.dm ? this.nm.width - s2.C : 0;
      n2.borderVisible && n2.ticksVisible && t2.useBitmapCoordinateSpace((({ context: t3, horizontalPixelRatio: r2, verticalPixelRatio: h2 }) => {
        t3.fillStyle = n2.borderColor;
        const a2 = Math.max(1, Math.floor(h2)), l2 = Math.floor(0.5 * h2), o2 = Math.round(s2.C * r2);
        t3.beginPath();
        for (const n3 of i2) t3.rect(Math.floor(e2 * r2), Math.round(n3.Vl * h2) - l2, o2, a2);
        t3.fill();
      })), t2.useMediaCoordinateSpace((({ context: t3 }) => {
        t3.font = this.ym(), t3.fillStyle = n2.textColor ?? this.Ro.textColor, t3.textAlign = this.dm ? "right" : "left", t3.textBaseline = "middle";
        const r2 = this.dm ? Math.round(e2 - s2.V) : Math.round(e2 + s2.C + s2.V), h2 = i2.map(((i3) => this.rm.Di(t3, i3.eo)));
        for (let n3 = i2.length; n3--; ) {
          const s3 = i2[n3];
          t3.fillText(s3.eo, r2, s3.Vl + h2[n3]);
        }
      }));
    }
    Dm() {
      if (null === this.nm || null === this.Ki) return;
      let t2 = this.nm.height / 2;
      const i2 = [], n2 = this.Ki.Dt().slice(), s2 = this.yt.yv(), e2 = this.xm();
      this.Ki === s2.Zs() && this.yt.yv().Dt().forEach(((t3) => {
        s2.Gs(t3) && n2.push(t3);
      }));
      const r2 = this.Ki.kl()[0], h2 = this.Ki;
      n2.forEach(((n3) => {
        const e3 = n3.qn(s2, h2);
        e3.forEach(((t3) => {
          t3.$i() && null === t3.Wi() && (t3.Ui(null), i2.push(t3));
        })), r2 === n3 && e3.length > 0 && (t2 = e3[0].Bi());
      }));
      this.Ki.N().alignLabels && this.Om(i2, e2, t2);
    }
    Om(t2, i2, n2) {
      if (null === this.nm) return;
      const s2 = t2.filter(((t3) => t3.Bi() <= n2)), e2 = t2.filter(((t3) => t3.Bi() > n2));
      s2.sort(((t3, i3) => i3.Bi() - t3.Bi())), s2.length && e2.length && e2.push(s2[0]), e2.sort(((t3, i3) => t3.Bi() - i3.Bi()));
      for (const n3 of t2) {
        const t3 = Math.floor(n3.$t(i2) / 2), s3 = n3.Bi();
        s3 > -t3 && s3 < t3 && n3.Ui(t3), s3 > this.nm.height - t3 && s3 < this.nm.height + t3 && n3.Ui(this.nm.height - t3);
      }
      Fn(s2, 1, this.nm.height, i2), Fn(e2, -1, this.nm.height, i2);
    }
    Am(t2) {
      if (null === this.nm) return;
      const i2 = this.Pm(), n2 = this.xm(), s2 = this.dm ? "right" : "left";
      i2.forEach(((i3) => {
        if (i3.ji()) {
          i3.Tt(a(this.Ki)).st(t2, n2, this.rm, s2);
        }
      }));
    }
    zm(t2) {
      if (null === this.nm || null === this.Ki) return;
      const i2 = this.yt._m().Qt(), n2 = [], s2 = this.yt.yv(), e2 = i2.Vd().qn(s2, this.Ki);
      e2.length && n2.push(e2);
      const r2 = this.xm(), h2 = this.dm ? "right" : "left";
      n2.forEach(((i3) => {
        i3.forEach(((i4) => {
          i4.Tt(a(this.Ki)).st(t2, r2, this.rm, h2);
        }));
      }));
    }
    Lm(t2) {
      this.uv.style.cursor = 1 === t2 ? "ns-resize" : "default";
    }
    wo() {
      const t2 = this.Cm();
      this.am < t2 && this.yt._m().Qt().ka(), this.am = t2;
    }
    ym() {
      return g(this.Ro.fontSize, this.Ro.fontFamily);
    }
  };
  function Hn(t2, i2) {
    return t2.Xa?.(i2) ?? [];
  }
  function Un(t2, i2) {
    return t2.jn?.(i2) ?? [];
  }
  function $n(t2, i2) {
    return t2.cn?.(i2) ?? [];
  }
  function jn(t2, i2) {
    return t2.qa?.(i2) ?? [];
  }
  var qn = class _qn {
    constructor(t2, i2) {
      this.nm = on({ width: 0, height: 0 }), this.Nm = null, this.Fm = null, this.Wm = null, this.Hm = null, this.Um = false, this.$m = new o(), this.jm = new o(), this.qm = 0, this.Ym = false, this.Km = null, this.Gm = false, this.Zm = null, this.Xm = null, this.lm = false, this.om = () => {
        this.lm || null === this.Jm || this.sn().mr();
      }, this.um = () => {
        this.lm || null === this.Jm || this.sn().mr();
      }, this.Gv = t2, this.Jm = i2, this.Jm.mu().i(this.Qm.bind(this), this, true), this.tw = document.createElement("td"), this.tw.style.padding = "0", this.tw.style.position = "relative";
      const n2 = document.createElement("div");
      n2.style.width = "100%", n2.style.height = "100%", n2.style.position = "relative", n2.style.overflow = "hidden", this.iw = document.createElement("td"), this.iw.style.padding = "0", this.nw = document.createElement("td"), this.nw.style.padding = "0", this.tw.appendChild(n2), this.wm = En(n2, on({ width: 16, height: 16 })), this.wm.subscribeSuggestedBitmapSizeChanged(this.om);
      const s2 = this.wm.canvasElement;
      s2.style.position = "absolute", s2.style.zIndex = "1", s2.style.left = "0", s2.style.top = "0", this.gm = En(n2, on({ width: 16, height: 16 })), this.gm.subscribeSuggestedBitmapSizeChanged(this.um);
      const e2 = this.gm.canvasElement;
      e2.style.position = "absolute", e2.style.zIndex = "2", e2.style.left = "0", e2.style.top = "0", this._v = document.createElement("tr"), this._v.appendChild(this.iw), this._v.appendChild(this.tw), this._v.appendChild(this.nw), this.sw(), this.sv = new Sn(this.gm.canvasElement, this, { Pp: () => null === this.Km && !this.Gv.N().handleScroll.vertTouchDrag, kp: () => null === this.Km && !this.Gv.N().handleScroll.horzTouchDrag });
    }
    m() {
      null !== this.Nm && this.Nm.m(), null !== this.Fm && this.Fm.m(), this.Wm = null, this.gm.unsubscribeSuggestedBitmapSizeChanged(this.um), Bn(this.gm.canvasElement), this.gm.dispose(), this.wm.unsubscribeSuggestedBitmapSizeChanged(this.om), Bn(this.wm.canvasElement), this.wm.dispose(), null !== this.Jm && (this.Jm.mu().u(this), this.Jm.m()), this.sv.m();
    }
    yv() {
      return a(this.Jm);
    }
    ew(t2) {
      null !== this.Jm && this.Jm.mu().u(this), this.Jm = t2, null !== this.Jm && this.Jm.mu().i(_qn.prototype.Qm.bind(this), this, true), this.sw(), this.Gv.lv().indexOf(this) === this.Gv.lv().length - 1 ? (this.Wm = this.Wm ?? new Vn(this.tw, this.Gv), this.Wm.Pt()) : (this.Wm?.Xv(), this.Wm = null);
    }
    _m() {
      return this.Gv;
    }
    fv() {
      return this._v;
    }
    sw() {
      if (null !== this.Jm && (this.rw(), 0 !== this.sn().Jn().length)) {
        if (null !== this.Nm) {
          const t2 = this.Jm.X_();
          this.Nm.un(a(t2));
        }
        if (null !== this.Fm) {
          const t2 = this.Jm.J_();
          this.Fm.un(a(t2));
        }
      }
    }
    hw() {
      null !== this.Nm && this.Nm.Pt(), null !== this.Fm && this.Fm.Pt();
    }
    F_() {
      return null !== this.Jm ? this.Jm.F_() : 0;
    }
    W_(t2) {
      this.Jm && this.Jm.W_(t2);
    }
    bp(t2) {
      if (!this.Jm) return;
      this.aw();
      const i2 = t2.localX, n2 = t2.localY;
      this.lw(i2, n2, t2);
    }
    Fp(t2) {
      this.aw(), this.ow(), this.lw(t2.localX, t2.localY, t2);
    }
    Sp(t2) {
      if (!this.Jm) return;
      this.aw();
      const i2 = t2.localX, n2 = t2.localY;
      this.lw(i2, n2, t2);
    }
    Ap(t2) {
      null !== this.Jm && (this.aw(), this.lw(t2.localX, t2.localY, t2), this._w(t2));
    }
    fp(t2) {
      null !== this.Jm && this.uw(this.jm, t2);
    }
    _p(t2) {
      this.fp(t2);
    }
    Dp(t2) {
      this.aw(), this.cw(t2), this.lw(t2.localX, t2.localY, t2);
    }
    Bp(t2) {
      null !== this.Jm && (this.aw(), this.Ym = false, this.dw(t2));
    }
    Ep(t2) {
      null !== this.Jm && this._w(t2);
    }
    Xp(t2) {
      if (this.Ym = true, null === this.Km) {
        const i2 = { x: t2.localX, y: t2.localY };
        this.fw(i2, i2, t2);
      }
    }
    Zp(t2) {
      null !== this.Jm && (this.aw(), this.Jm.Qt().Rd(null), this.pw());
    }
    mw() {
      return this.$m;
    }
    ww() {
      return this.jm;
    }
    $p() {
      this.qm = 1, this.sn().cs();
    }
    jp(t2, i2) {
      if (!this.Gv.N().handleScale.pinch) return;
      const n2 = 5 * (i2 - this.qm);
      this.qm = i2, this.sn().Wd(t2._t, n2);
    }
    Op(t2) {
      this.Ym = false, this.Gm = null !== this.Km, this.ow();
      const i2 = this.sn().Vd();
      null !== this.Km && i2.It() && (this.Zm = { x: i2.ni(), y: i2.si() }, this.Km = { x: t2.localX, y: t2.localY });
    }
    Tp(t2) {
      if (null === this.Jm) return;
      const i2 = t2.localX, n2 = t2.localY;
      if (null === this.Km) this.cw(t2);
      else {
        this.Gm = false;
        const s2 = a(this.Zm), e2 = s2.x + (i2 - this.Km.x), r2 = s2.y + (n2 - this.Km.y);
        this.lw(e2, r2, t2);
      }
    }
    Vp(t2) {
      0 === this._m().N().trackingMode.exitMode && (this.Gm = true), this.gw(), this.dw(t2);
    }
    Qs(t2, i2) {
      const n2 = this.Jm;
      return null === n2 ? null : Ri(n2, t2, i2);
    }
    Mw(t2, i2) {
      a("left" === i2 ? this.Nm : this.Fm).km(on({ width: t2, height: this.nm.height }));
    }
    pv() {
      return this.nm;
    }
    km(t2) {
      _n(this.nm, t2) || (this.nm = t2, this.lm = true, this.wm.resizeCanvasElement(t2), this.gm.resizeCanvasElement(t2), this.lm = false, this.tw.style.width = t2.width + "px", this.tw.style.height = t2.height + "px");
    }
    bw() {
      const t2 = a(this.Jm);
      t2.G_(t2.X_()), t2.G_(t2.J_());
      for (const i2 of t2.kl()) if (t2.Gs(i2)) {
        const n2 = i2.Ft();
        null !== n2 && t2.G_(n2), i2.Nn();
      }
      for (const i2 of t2.gu()) i2.Nn();
    }
    vv() {
      return this.wm.bitmapSize;
    }
    mv(t2, i2, n2, s2) {
      const e2 = this.vv();
      if (e2.width > 0 && e2.height > 0 && (t2.drawImage(this.wm.canvasElement, i2, n2), s2)) {
        const s3 = this.gm.canvasElement;
        null !== t2 && t2.drawImage(s3, i2, n2);
      }
    }
    Rm(t2) {
      if (0 === t2) return;
      if (null === this.Jm) return;
      t2 > 1 && this.bw(), null !== this.Nm && this.Nm.Rm(t2), null !== this.Fm && this.Fm.Rm(t2);
      const i2 = { colorSpace: this.Gv.N().layout.colorSpace };
      if (1 !== t2) {
        this.wm.applySuggestedBitmapSize();
        const t3 = pn(this.wm, i2);
        null !== t3 && (t3.useBitmapCoordinateSpace(((t4) => {
          this.Im(t4);
        })), this.Jm && (this.Sw(t3, Hn), this.xw(t3), this.Sw(t3, Un), this.Sw(t3, $n)));
      }
      this.gm.applySuggestedBitmapSize();
      const n2 = pn(this.gm, i2);
      null !== n2 && (n2.useBitmapCoordinateSpace((({ context: t3, bitmapSize: i3 }) => {
        t3.clearRect(0, 0, i3.width, i3.height);
      })), this.Cw(n2), this.Sw(n2, jn), this.Sw(n2, $n));
    }
    yw() {
      return this.Nm;
    }
    Pw() {
      return this.Fm;
    }
    Em(t2, i2) {
      this.Sw(t2, i2);
    }
    Qm() {
      null !== this.Jm && this.Jm.mu().u(this), this.Jm = null;
    }
    _w(t2) {
      this.uw(this.$m, t2);
    }
    uw(t2, i2) {
      const n2 = i2.localX, s2 = i2.localY;
      t2.v() && t2.p(this.sn().Bt().Vc(n2), { x: n2, y: s2 }, i2);
    }
    Im({ context: t2, bitmapSize: i2 }) {
      const { width: n2, height: s2 } = i2, e2 = this.sn(), r2 = e2.$(), h2 = e2.af();
      r2 === h2 ? E(t2, 0, 0, n2, s2, h2) : z(t2, 0, 0, n2, s2, r2, h2);
    }
    xw(t2) {
      const i2 = a(this.Jm), n2 = i2.wu().wr().Tt(i2);
      null !== n2 && n2.st(t2, false);
    }
    Cw(t2) {
      this.kw(t2, Un, zn, this.sn().Vd());
    }
    Sw(t2, i2) {
      const n2 = a(this.Jm), s2 = i2 === Un ? this.Tw() : null, e2 = null === s2 ? null : this.Rw(s2, n2), r2 = n2.gu();
      if (null === e2 || null === s2) {
        const s3 = n2._u();
        return this.Dw(t2, i2, An, r2, s3), void this.Dw(t2, i2, zn, r2, s3);
      }
      const h2 = n2.Dt(), l2 = (t3) => t3 === s2 ? e2.Za : void 0;
      this.Dw(t2, i2, An, r2, h2, l2), this.Dw(t2, i2, zn, r2, h2, l2), this.kw(t2, i2, An, s2, e2.qa), this.kw(t2, i2, zn, s2, e2.qa);
    }
    Dw(t2, i2, n2, s2, e2, r2) {
      for (const e3 of s2) this.kw(t2, i2, n2, e3);
      if (void 0 !== r2) for (const s3 of e2) this.kw(t2, i2, n2, s3, r2(s3));
      else for (const s3 of e2) this.kw(t2, i2, n2, s3);
    }
    Tw() {
      const t2 = a(this.Jm), i2 = t2.Qt().cu()?.uu;
      if (!t2.Qt().N().hoveredSeriesOnTop || void 0 === i2) return null;
      for (const n2 of t2.Dt()) if (n2 === i2) return n2;
      return null;
    }
    Rw(t2, i2) {
      const n2 = t2.Ga?.(i2) ?? null;
      return null === n2 || 0 === n2.qa.length ? null : n2;
    }
    kw(t2, i2, n2, s2, e2) {
      const r2 = a(this.Jm), h2 = r2.Qt().cu(), l2 = null !== h2 && h2.uu === s2, o2 = null !== h2 && l2 && void 0 !== h2.bu ? h2.bu.ie : void 0, _2 = (i3) => n2(i3, t2, l2, o2);
      void 0 === e2 ? Ln(i2, _2, s2, r2) : On(e2, _2, r2);
    }
    rw() {
      if (null === this.Jm) return;
      const t2 = this.Gv, i2 = this.Jm.X_().N().visible, n2 = this.Jm.J_().N().visible;
      i2 || null === this.Nm || (this.iw.removeChild(this.Nm.fv()), this.Nm.m(), this.Nm = null), n2 || null === this.Fm || (this.nw.removeChild(this.Fm.fv()), this.Fm.m(), this.Fm = null);
      const s2 = t2.Qt().Jd();
      i2 && null === this.Nm && (this.Nm = new Wn(this, t2.N(), s2, "left"), this.iw.appendChild(this.Nm.fv())), n2 && null === this.Fm && (this.Fm = new Wn(this, t2.N(), s2, "right"), this.nw.appendChild(this.Fm.fv()));
    }
    Iw(t2) {
      return t2.Jp && this.Ym || null !== this.Km;
    }
    lw(t2, i2, n2) {
      t2 = Math.max(0, Math.min(t2, this.nm.width - 1)), i2 = Math.max(0, Math.min(i2, this.nm.height - 1)), this.sn().Kd(t2, i2, n2, a(this.Jm));
    }
    pw() {
      this.sn().Zd();
    }
    gw() {
      this.Gm && (this.Km = null, this.pw());
    }
    fw(t2, i2, n2) {
      this.Km = t2, this.Gm = false, this.lw(i2.x, i2.y, n2);
      const s2 = this.sn().Vd();
      this.Zm = { x: s2.ni(), y: s2.si() };
    }
    sn() {
      return this.Gv.Qt();
    }
    dw(t2) {
      if (!this.Um) return;
      const i2 = this.sn(), n2 = this.yv();
      if (i2.eu(n2, n2.kn()), this.Hm = null, this.Um = false, i2.jd(), null !== this.Xm) {
        const t3 = performance.now(), n3 = i2.Bt();
        this.Xm.me(n3.Oc(), t3), this.Xm.Kc(t3) || i2.ps(this.Xm);
      }
    }
    aw() {
      this.Km = null;
    }
    ow() {
      if (!this.Jm) return;
      if (this.sn().cs(), document.activeElement !== document.body && document.activeElement !== document.documentElement) a(document.activeElement).blur();
      else {
        const t2 = document.getSelection();
        null !== t2 && t2.removeAllRanges();
      }
      !this.Jm.kn().Zi() && this.sn().Bt().Zi();
    }
    cw(t2) {
      if (null === this.Jm) return;
      const i2 = this.sn(), n2 = i2.Bt();
      if (n2.Zi()) return;
      const s2 = this.Gv.N(), e2 = s2.handleScroll, r2 = s2.kineticScroll;
      if ((!e2.pressedMouseMove || t2.Jp) && (!e2.horzTouchDrag && !e2.vertTouchDrag || !t2.Jp)) return;
      const h2 = this.Jm.kn(), a2 = performance.now();
      if (null !== this.Hm || this.Iw(t2) || (this.Hm = { x: t2.clientX, y: t2.clientY, yf: a2, Vw: t2.localX, Ew: t2.localY }), null !== this.Hm && !this.Um && (this.Hm.x !== t2.clientX || this.Hm.y !== t2.clientY)) {
        if (t2.Jp && r2.touch || !t2.Jp && r2.mouse) {
          const t3 = n2.ml();
          this.Xm = new In(0.2 / t3, 7 / t3, 0.997, 15 / t3), this.Xm.Uv(n2.Oc(), this.Hm.yf);
        } else this.Xm = null;
        h2.Zi() || i2.nu(this.Jm, h2, t2.localY), i2.Ud(t2.localX), this.Um = true;
      }
      this.Um && (h2.Zi() || i2.su(this.Jm, h2, t2.localY), i2.$d(t2.localX), null !== this.Xm && this.Xm.Uv(n2.Oc(), a2));
    }
  };
  var Yn = class {
    constructor(t2, i2, n2, s2, e2) {
      this.xt = true, this.nm = on({ width: 0, height: 0 }), this.om = () => this.Rm(3), this.dm = "left" === t2, this.bd = n2.Jd, this.yn = i2, this.Bw = s2, this.Aw = e2, this.uv = document.createElement("div"), this.uv.style.width = "25px", this.uv.style.height = "100%", this.uv.style.overflow = "hidden", this.wm = En(this.uv, on({ width: 16, height: 16 })), this.wm.subscribeSuggestedBitmapSizeChanged(this.om);
    }
    m() {
      this.wm.unsubscribeSuggestedBitmapSizeChanged(this.om), Bn(this.wm.canvasElement), this.wm.dispose();
    }
    fv() {
      return this.uv;
    }
    pv() {
      return this.nm;
    }
    km(t2) {
      _n(this.nm, t2) || (this.nm = t2, this.wm.resizeCanvasElement(t2), this.uv.style.width = `${t2.width}px`, this.uv.style.height = `${t2.height}px`, this.xt = true);
    }
    Rm(t2) {
      if (t2 < 3 && !this.xt) return;
      if (0 === this.nm.width || 0 === this.nm.height) return;
      this.xt = false, this.wm.applySuggestedBitmapSize();
      const i2 = pn(this.wm, { colorSpace: this.yn.layout.colorSpace });
      null !== i2 && i2.useBitmapCoordinateSpace(((t3) => {
        this.Im(t3), this.Vm(t3);
      }));
    }
    vv() {
      return this.wm.bitmapSize;
    }
    mv(t2, i2, n2) {
      const s2 = this.vv();
      s2.width > 0 && s2.height > 0 && t2.drawImage(this.wm.canvasElement, i2, n2);
    }
    Vm({ context: t2, bitmapSize: i2, horizontalPixelRatio: n2, verticalPixelRatio: s2 }) {
      if (!this.Bw()) return;
      t2.fillStyle = this.yn.timeScale.borderColor;
      const e2 = Math.floor(this.bd.N().S * n2), r2 = Math.floor(this.bd.N().S * s2), h2 = this.dm ? i2.width - e2 : 0;
      t2.fillRect(h2, 0, e2, r2);
    }
    Im({ context: t2, bitmapSize: i2 }) {
      E(t2, 0, 0, i2.width, i2.height, this.Aw());
    }
  };
  function Kn(t2) {
    return (i2) => i2.tl?.(t2) ?? [];
  }
  var Gn = Kn("normal");
  var Zn = Kn("top");
  var Xn = Kn("bottom");
  var Jn = class {
    constructor(t2, i2) {
      this.zw = null, this.Lw = null, this.M = null, this.Ow = false, this.nm = on({ width: 0, height: 0 }), this.Nw = new o(), this.rm = new it(5), this.lm = false, this.om = () => {
        this.lm || this.Gv.Qt().mr();
      }, this.um = () => {
        this.lm || this.Gv.Qt().mr();
      }, this.Gv = t2, this.Pu = i2, this.yn = t2.N().layout, this.jv = document.createElement("tr"), this.Fw = document.createElement("td"), this.Fw.style.padding = "0", this.Ww = document.createElement("td"), this.Ww.style.padding = "0", this.uv = document.createElement("td"), this.uv.style.height = "25px", this.uv.style.padding = "0", this.Hw = document.createElement("div"), this.Hw.style.width = "100%", this.Hw.style.height = "100%", this.Hw.style.position = "relative", this.Hw.style.overflow = "hidden", this.uv.appendChild(this.Hw), this.wm = En(this.Hw, on({ width: 16, height: 16 })), this.wm.subscribeSuggestedBitmapSizeChanged(this.om);
      const n2 = this.wm.canvasElement;
      n2.style.position = "absolute", n2.style.zIndex = "1", n2.style.left = "0", n2.style.top = "0", this.gm = En(this.Hw, on({ width: 16, height: 16 })), this.gm.subscribeSuggestedBitmapSizeChanged(this.um);
      const s2 = this.gm.canvasElement;
      s2.style.position = "absolute", s2.style.zIndex = "2", s2.style.left = "0", s2.style.top = "0", this.jv.appendChild(this.Fw), this.jv.appendChild(this.uv), this.jv.appendChild(this.Ww), this.Uw(), this.Gv.Qt().N_().i(this.Uw.bind(this), this), this.sv = new Sn(this.gm.canvasElement, this, { Pp: () => true, kp: () => !this.Gv.N().handleScroll.horzTouchDrag });
    }
    m() {
      this.sv.m(), null !== this.zw && this.zw.m(), null !== this.Lw && this.Lw.m(), this.gm.unsubscribeSuggestedBitmapSizeChanged(this.um), Bn(this.gm.canvasElement), this.gm.dispose(), this.wm.unsubscribeSuggestedBitmapSizeChanged(this.om), Bn(this.wm.canvasElement), this.wm.dispose();
    }
    fv() {
      return this.jv;
    }
    $w() {
      return this.zw;
    }
    jw() {
      return this.Lw;
    }
    Fp(t2) {
      if (this.Ow) return;
      this.Ow = true;
      const i2 = this.Gv.Qt();
      !i2.Bt().Zi() && this.Gv.N().handleScale.axisPressedMouseMove.time && i2.Fd(t2.localX);
    }
    Op(t2) {
      this.Fp(t2);
    }
    Wp() {
      const t2 = this.Gv.Qt();
      !t2.Bt().Zi() && this.Ow && (this.Ow = false, this.Gv.N().handleScale.axisPressedMouseMove.time && t2.Yd());
    }
    Dp(t2) {
      const i2 = this.Gv.Qt();
      !i2.Bt().Zi() && this.Gv.N().handleScale.axisPressedMouseMove.time && i2.qd(t2.localX);
    }
    Tp(t2) {
      this.Dp(t2);
    }
    Bp() {
      this.Ow = false;
      const t2 = this.Gv.Qt();
      t2.Bt().Zi() && !this.Gv.N().handleScale.axisPressedMouseMove.time || t2.Yd();
    }
    Vp() {
      this.Bp();
    }
    fp() {
      this.Gv.N().handleScale.axisDoubleClickReset.time && this.Gv.Qt().ws();
    }
    _p() {
      this.fp();
    }
    bp() {
      this.Gv.Qt().N().handleScale.axisPressedMouseMove.time && this.Lm(1);
    }
    Zp() {
      this.Lm(0);
    }
    pv() {
      return this.nm;
    }
    qw() {
      return this.Nw;
    }
    Yw(t2, i2, n2) {
      _n(this.nm, t2) || (this.nm = t2, this.lm = true, this.wm.resizeCanvasElement(t2), this.gm.resizeCanvasElement(t2), this.lm = false, this.uv.style.width = `${t2.width}px`, this.uv.style.height = `${t2.height}px`, this.Nw.p(t2)), null !== this.zw && this.zw.km(on({ width: i2, height: t2.height })), null !== this.Lw && this.Lw.km(on({ width: n2, height: t2.height }));
    }
    Kw() {
      const t2 = this.Gw();
      return Math.ceil(t2.S + t2.C + t2.P + t2.A + t2.I + t2.Zw);
    }
    Pt() {
      this.Gv.Qt().Bt().Ll();
    }
    vv() {
      return this.wm.bitmapSize;
    }
    mv(t2, i2, n2, s2) {
      const e2 = this.vv();
      if (e2.width > 0 && e2.height > 0 && (t2.drawImage(this.wm.canvasElement, i2, n2), s2)) {
        const s3 = this.gm.canvasElement;
        t2.drawImage(s3, i2, n2);
      }
    }
    Rm(t2) {
      if (0 === t2) return;
      const i2 = { colorSpace: this.yn.colorSpace };
      if (1 !== t2) {
        this.wm.applySuggestedBitmapSize();
        const n3 = pn(this.wm, i2);
        null !== n3 && (n3.useBitmapCoordinateSpace(((t3) => {
          this.Im(t3), this.Vm(t3), this.Xw(n3, Xn);
        })), this.Bm(n3), this.Xw(n3, Gn)), null !== this.zw && this.zw.Rm(t2), null !== this.Lw && this.Lw.Rm(t2);
      }
      this.gm.applySuggestedBitmapSize();
      const n2 = pn(this.gm, i2);
      null !== n2 && (n2.useBitmapCoordinateSpace((({ context: t3, bitmapSize: i3 }) => {
        t3.clearRect(0, 0, i3.width, i3.height);
      })), this.Jw([...this.Gv.Qt().Jn(), this.Gv.Qt().Vd()], n2), this.Xw(n2, Zn));
    }
    Xw(t2, i2) {
      const n2 = this.Gv.Qt().Jn();
      for (const s2 of n2) Ln(i2, ((i3) => An(i3, t2, false, void 0)), s2, void 0);
      for (const s2 of n2) Ln(i2, ((i3) => zn(i3, t2, false, void 0)), s2, void 0);
    }
    Im({ context: t2, bitmapSize: i2 }) {
      E(t2, 0, 0, i2.width, i2.height, this.Gv.Qt().af());
    }
    Vm({ context: t2, bitmapSize: i2, verticalPixelRatio: n2 }) {
      if (this.Gv.N().timeScale.borderVisible) {
        t2.fillStyle = this.Qw();
        const s2 = Math.max(1, Math.floor(this.Gw().S * n2));
        t2.fillRect(0, 0, i2.width, s2);
      }
    }
    Bm(t2) {
      const i2 = this.Gv.Qt().Bt(), n2 = i2.Ll();
      if (!n2 || 0 === n2.length) return;
      const s2 = this.Pu.maxTickMarkWeight(n2), e2 = this.Gw(), r2 = i2.N();
      r2.borderVisible && r2.ticksVisible && t2.useBitmapCoordinateSpace((({ context: t3, horizontalPixelRatio: i3, verticalPixelRatio: s3 }) => {
        t3.strokeStyle = this.Qw(), t3.fillStyle = this.Qw();
        const r3 = Math.max(1, Math.floor(i3)), h2 = Math.floor(0.5 * i3);
        t3.beginPath();
        const a2 = Math.round(e2.C * s3);
        for (let s4 = n2.length; s4--; ) {
          const e3 = Math.round(n2[s4].coord * i3);
          t3.rect(e3 - h2, 0, r3, a2);
        }
        t3.fill();
      })), t2.useMediaCoordinateSpace((({ context: t3 }) => {
        const i3 = e2.S + e2.C + e2.A + e2.P / 2;
        t3.textAlign = "center", t3.textBaseline = "middle", t3.fillStyle = this.H(), t3.font = this.ym();
        for (const e3 of n2) if (e3.weight < s2) {
          const n3 = e3.needAlignCoordinate ? this.tg(t3, e3.coord, e3.label) : e3.coord;
          t3.fillText(e3.label, n3, i3);
        }
        this.Gv.N().timeScale.allowBoldLabels && (t3.font = this.ig());
        for (const e3 of n2) if (e3.weight >= s2) {
          const n3 = e3.needAlignCoordinate ? this.tg(t3, e3.coord, e3.label) : e3.coord;
          t3.fillText(e3.label, n3, i3);
        }
      }));
    }
    tg(t2, i2, n2) {
      const s2 = this.rm.Ii(t2, n2), e2 = s2 / 2, r2 = Math.floor(i2 - e2) + 0.5;
      return r2 < 0 ? i2 += Math.abs(0 - r2) : r2 + s2 > this.nm.width && (i2 -= Math.abs(this.nm.width - (r2 + s2))), i2;
    }
    Jw(t2, i2) {
      const n2 = this.Gw();
      for (const s2 of t2) for (const t3 of s2.dn()) t3.Tt().st(i2, n2);
    }
    Qw() {
      return this.Gv.N().timeScale.borderColor;
    }
    H() {
      return this.yn.textColor;
    }
    F() {
      return this.yn.fontSize;
    }
    ym() {
      return g(this.F(), this.yn.fontFamily);
    }
    ig() {
      return g(this.F(), this.yn.fontFamily, "bold");
    }
    Gw() {
      null === this.M && (this.M = { S: 1, L: NaN, A: NaN, I: NaN, tn: NaN, C: 5, P: NaN, k: "", Qi: new it(), Zw: 0 });
      const t2 = this.M, i2 = this.ym();
      if (t2.k !== i2) {
        const n2 = this.F();
        t2.P = n2, t2.k = i2, t2.A = 3 * n2 / 12, t2.I = 3 * n2 / 12, t2.tn = 9 * n2 / 12, t2.L = 0, t2.Zw = 4 * n2 / 12, t2.Qi.Os();
      }
      return this.M;
    }
    Lm(t2) {
      this.uv.style.cursor = 1 === t2 ? "ew-resize" : "default";
    }
    Uw() {
      const t2 = this.Gv.Qt(), i2 = t2.N();
      i2.leftPriceScale.visible || null === this.zw || (this.Fw.removeChild(this.zw.fv()), this.zw.m(), this.zw = null), i2.rightPriceScale.visible || null === this.Lw || (this.Ww.removeChild(this.Lw.fv()), this.Lw.m(), this.Lw = null);
      const n2 = { Jd: this.Gv.Qt().Jd() }, s2 = () => i2.leftPriceScale.borderVisible && t2.Bt().N().borderVisible, e2 = () => t2.af();
      i2.leftPriceScale.visible && null === this.zw && (this.zw = new Yn("left", i2, n2, s2, e2), this.Fw.appendChild(this.zw.fv())), i2.rightPriceScale.visible && null === this.Lw && (this.Lw = new Yn("right", i2, n2, s2, e2), this.Ww.appendChild(this.Lw.fv()));
    }
  };
  var Qn = !!vn && !!navigator.userAgentData && navigator.userAgentData.brands.some(((t2) => t2.brand.includes("Chromium"))) && !!vn && (navigator?.userAgentData?.platform ? "Windows" === navigator.userAgentData.platform : navigator.userAgent.toLowerCase().indexOf("win") >= 0);
  var ts = class {
    constructor(t2, i2, n2) {
      var s2;
      this.ng = [], this.sg = [], this.eg = 0, this.oo = 0, this.k_ = 0, this.rg = 0, this.hg = 0, this.ag = null, this.lg = false, this.$m = new o(), this.jm = new o(), this.wd = new o(), this.og = null, this._g = null, this.Kv = t2, this.yn = i2, this.Pu = n2, this.jv = document.createElement("div"), this.jv.classList.add("tv-lightweight-charts"), this.jv.style.overflow = "hidden", this.jv.style.direction = "ltr", this.jv.style.width = "100%", this.jv.style.height = "100%", (s2 = this.jv).style.userSelect = "none", s2.style.webkitUserSelect = "none", s2.style.msUserSelect = "none", s2.style.MozUserSelect = "none", s2.style.webkitTapHighlightColor = "transparent", this.ug = document.createElement("table"), this.ug.setAttribute("cellspacing", "0"), this.jv.appendChild(this.ug), this.cg = this.dg.bind(this), is(this.yn) && this.fg(true), this.sn = new Hi(this.Md.bind(this), this.yn, n2), this.Qt().Ed().i(this.pg.bind(this), this), this.vg = new Jn(this, this.Pu), this.ug.appendChild(this.vg.fv());
      const e2 = i2.autoSize && this.mg();
      let r2 = this.yn.width, h2 = this.yn.height;
      if (e2 || 0 === r2 || 0 === h2) {
        const i3 = t2.getBoundingClientRect();
        r2 = r2 || i3.width, h2 = h2 || i3.height;
      }
      this.wg(r2, h2), this.gg(), t2.appendChild(this.jv), this.Mg(), this.sn.Bt().Jc().i(this.sn.ka.bind(this.sn), this), this.sn.N_().i(this.sn.ka.bind(this.sn), this);
    }
    Qt() {
      return this.sn;
    }
    N() {
      return this.yn;
    }
    lv() {
      return this.ng;
    }
    bg() {
      return this.vg;
    }
    m() {
      this.fg(false), 0 !== this.eg && window.cancelAnimationFrame(this.eg), this.sn.Ed().u(this), this.sn.Bt().Jc().u(this), this.sn.N_().u(this), this.sn.m();
      for (const t2 of this.ng) this.ug.removeChild(t2.fv()), t2.mw().u(this), t2.ww().u(this), t2.m();
      this.ng = [];
      for (const t2 of this.sg) this.Sg(t2);
      this.sg = [], a(this.vg).m(), null !== this.jv.parentElement && this.jv.parentElement.removeChild(this.jv), this.wd.m(), this.$m.m(), this.jm.m(), this.xg();
    }
    wg(t2, i2, n2 = false) {
      if (this.oo === i2 && this.k_ === t2) return;
      const s2 = (function(t3) {
        const i3 = Math.floor(t3.width), n3 = Math.floor(t3.height);
        return on({ width: i3 - i3 % 2, height: n3 - n3 % 2 });
      })(on({ width: t2, height: i2 }));
      this.oo = s2.height, this.k_ = s2.width;
      const e2 = this.oo + "px", r2 = this.k_ + "px";
      if (this.Cg() || (a(this.jv).style.height = e2, a(this.jv).style.width = r2), this.ug.style.height = e2, this.ug.style.width = r2, n2) {
        0 !== this.eg && (window.cancelAnimationFrame(this.eg), this.eg = 0), this.lg = false;
        const t3 = Y.ys();
        null !== this.ag && (t3.Ss(this.ag), this.ag = null), this.yg(t3, performance.now());
      } else this.sn.ka();
    }
    Rm(t2) {
      void 0 === t2 && (t2 = Y.ys());
      for (let i2 = 0; i2 < this.ng.length; i2++) this.ng[i2].Rm(t2._s(i2).rs);
      this.yn.timeScale.visible && this.vg.Rm(t2.ls());
    }
    vr(t2) {
      const i2 = is(this.yn);
      this.sn.vr(t2);
      const n2 = is(this.yn);
      n2 !== i2 && this.fg(n2), t2.layout?.panes && this.Pg(), this.Mg(), this.kg(t2);
    }
    mw() {
      return this.$m;
    }
    ww() {
      return this.jm;
    }
    Ed() {
      return this.wd;
    }
    Tg(t2 = false) {
      null !== this.ag && (this.yg(this.ag, performance.now()), this.ag = null);
      const i2 = this.Rg(null), n2 = document.createElement("canvas");
      n2.width = i2.width, n2.height = i2.height;
      const s2 = a(n2.getContext("2d"));
      return this.Rg(s2, t2), n2;
    }
    Dg(t2) {
      if ("left" === t2 && !this.Ig()) return 0;
      if ("right" === t2 && !this.Vg()) return 0;
      if (0 === this.ng.length) return 0;
      return a("left" === t2 ? this.ng[0].yw() : this.ng[0].Pw()).Tm();
    }
    Cg() {
      return this.yn.autoSize && null !== this.og;
    }
    gv() {
      return this.jv;
    }
    Eg(t2) {
      this._g = t2, this._g ? this.gv().style.setProperty("cursor", t2) : this.gv().style.removeProperty("cursor");
    }
    Bg() {
      return this._g;
    }
    Ag(t2) {
      return h(this.ng[t2]).pv();
    }
    Pg() {
      this.sg.forEach(((t2) => {
        t2.Pt();
      }));
    }
    kg(t2) {
      (void 0 !== t2.autoSize || !this.og || void 0 === t2.width && void 0 === t2.height) && (t2.autoSize && !this.og && this.mg(), false === t2.autoSize && null !== this.og && this.xg(), t2.autoSize || void 0 === t2.width && void 0 === t2.height || this.wg(t2.width || this.k_, t2.height || this.oo));
    }
    Rg(t2, i2) {
      let n2 = 0, s2 = 0;
      const e2 = this.ng[0], r2 = (n3, s3) => {
        let e3 = 0;
        for (let r3 = 0; r3 < this.ng.length; r3++) {
          const h3 = this.ng[r3], l2 = a("left" === n3 ? h3.yw() : h3.Pw()), o2 = l2.vv();
          if (null !== t2 && l2.mv(t2, s3, e3, i2), e3 += o2.height, r3 < this.ng.length - 1) {
            const i3 = this.sg[r3], n4 = i3.vv();
            null !== t2 && i3.mv(t2, s3, e3), e3 += n4.height;
          }
        }
      };
      if (this.Ig()) {
        r2("left", 0);
        n2 += a(e2.yw()).vv().width;
      }
      for (let e3 = 0; e3 < this.ng.length; e3++) {
        const r3 = this.ng[e3], h3 = r3.vv();
        if (null !== t2 && r3.mv(t2, n2, s2, i2), s2 += h3.height, e3 < this.ng.length - 1) {
          const i3 = this.sg[e3], r4 = i3.vv();
          null !== t2 && i3.mv(t2, n2, s2), s2 += r4.height;
        }
      }
      if (n2 += e2.vv().width, this.Vg()) {
        r2("right", n2);
        n2 += a(e2.Pw()).vv().width;
      }
      const h2 = (i3, n3, s3) => {
        a("left" === i3 ? this.vg.$w() : this.vg.jw()).mv(a(t2), n3, s3);
      };
      if (this.yn.timeScale.visible) {
        const n3 = this.vg.vv();
        if (null !== t2) {
          let r3 = 0;
          this.Ig() && (h2("left", r3, s2), r3 = a(e2.yw()).vv().width), this.vg.mv(t2, r3, s2, i2), r3 += n3.width, this.Vg() && h2("right", r3, s2);
        }
        s2 += n3.height;
      }
      return on({ width: n2, height: s2 });
    }
    zg() {
      let t2 = 0, i2 = 0, n2 = 0;
      for (const s3 of this.ng) this.Ig() && (i2 = Math.max(i2, a(s3.yw()).Cm(), this.yn.leftPriceScale.minimumWidth)), this.Vg() && (n2 = Math.max(n2, a(s3.Pw()).Cm(), this.yn.rightPriceScale.minimumWidth)), t2 += s3.F_();
      i2 = Mn(i2), n2 = Mn(n2);
      const s2 = this.k_, e2 = this.oo, r2 = Math.max(s2 - i2 - n2, 0), h2 = 1 * this.sg.length, l2 = this.yn.timeScale.visible;
      let o2 = l2 ? Math.max(this.vg.Kw(), this.yn.timeScale.minimumHeight) : 0;
      var _2;
      o2 = (_2 = o2) + _2 % 2;
      const u2 = h2 + o2, c2 = e2 < u2 ? 0 : e2 - u2, d2 = c2 / t2;
      let f2 = 0;
      const p2 = window.devicePixelRatio || 1;
      for (let t3 = 0; t3 < this.ng.length; ++t3) {
        const s3 = this.ng[t3];
        s3.ew(this.sn.Gn()[t3]);
        let e3 = 0, h3 = 0;
        h3 = t3 === this.ng.length - 1 ? Math.ceil((c2 - f2) * p2) / p2 : Math.round(s3.F_() * d2 * p2) / p2, e3 = Math.max(h3, 2), f2 += e3, s3.km(on({ width: r2, height: e3 })), this.Ig() && s3.Mw(i2, "left"), this.Vg() && s3.Mw(n2, "right"), s3.yv() && this.sn.Bd(s3.yv(), e3);
      }
      this.vg.Yw(on({ width: l2 ? r2 : 0, height: o2 }), l2 ? i2 : 0, l2 ? n2 : 0), this.sn.H_(r2), this.rg !== i2 && (this.rg = i2), this.hg !== n2 && (this.hg = n2);
    }
    fg(t2) {
      t2 ? this.jv.addEventListener("wheel", this.cg, { passive: false }) : this.jv.removeEventListener("wheel", this.cg);
    }
    Lg(t2) {
      switch (t2.deltaMode) {
        case t2.DOM_DELTA_PAGE:
          return 120;
        case t2.DOM_DELTA_LINE:
          return 32;
      }
      return Qn ? 1 / window.devicePixelRatio : 1;
    }
    dg(t2) {
      if (!(0 !== t2.deltaX && this.yn.handleScroll.mouseWheel || 0 !== t2.deltaY && this.yn.handleScale.mouseWheel)) return;
      const i2 = this.Lg(t2), n2 = i2 * t2.deltaX / 100, s2 = -i2 * t2.deltaY / 100;
      if (t2.cancelable && t2.preventDefault(), 0 !== s2 && this.yn.handleScale.mouseWheel) {
        const i3 = Math.sign(s2) * Math.min(1, Math.abs(s2)), n3 = t2.clientX - this.jv.getBoundingClientRect().left;
        this.Qt().Wd(n3, i3);
      }
      0 !== n2 && this.yn.handleScroll.mouseWheel && this.Qt().Hd(-80 * n2);
    }
    yg(t2, i2) {
      const n2 = t2.ls();
      3 === n2 && this.Og(), 3 !== n2 && 2 !== n2 || (this.Ng(t2), this.Fg(t2, i2), this.vg.Pt(), this.ng.forEach(((t3) => {
        t3.hw();
      })), 3 === this.ag?.ls() && (this.ag.Ss(t2), this.Og(), this.Ng(this.ag), this.Fg(this.ag, i2), t2 = this.ag, this.ag = null)), this.Rm(t2);
    }
    Fg(t2, i2) {
      for (const n2 of t2.bs()) this.xs(n2, i2);
    }
    Ng(t2) {
      const i2 = this.sn.Gn();
      for (let n2 = 0; n2 < i2.length; n2++) t2._s(n2).hs && i2[n2].lu();
    }
    xs(t2, i2) {
      const n2 = this.sn.Bt();
      switch (t2.ds) {
        case 0:
          n2.td();
          break;
        case 1:
          n2.nd(t2.Wt);
          break;
        case 2:
          n2.gs(t2.Wt);
          break;
        case 3:
          n2.Ms(t2.Wt);
          break;
        case 4:
          n2.Wc();
          break;
        case 5:
          t2.Wt.Kc(i2) || n2.Ms(t2.Wt.Gc(i2));
      }
    }
    Md(t2) {
      null !== this.ag ? this.ag.Ss(t2) : this.ag = t2, this.lg || (this.lg = true, this.eg = window.requestAnimationFrame(((t3) => {
        if (this.lg = false, this.eg = 0, null !== this.ag) {
          const i2 = this.ag;
          this.ag = null, this.yg(i2, t3);
          for (const n2 of i2.bs()) if (5 === n2.ds && !n2.Wt.Kc(t3)) {
            this.Qt().ps(n2.Wt);
            break;
          }
        }
      })));
    }
    Og() {
      this.gg();
    }
    Sg(t2) {
      this.ug.removeChild(t2.fv()), t2.m();
    }
    gg() {
      const t2 = this.sn.Gn(), i2 = t2.length, n2 = this.ng.length;
      for (let t3 = i2; t3 < n2; t3++) {
        const t4 = h(this.ng.pop());
        this.ug.removeChild(t4.fv()), t4.mw().u(this), t4.ww().u(this), t4.m();
        const i3 = this.sg.pop();
        void 0 !== i3 && this.Sg(i3);
      }
      for (let s2 = n2; s2 < i2; s2++) {
        const i3 = new qn(this, t2[s2]);
        if (i3.mw().i(this.Wg.bind(this, i3), this), i3.ww().i(this.Hg.bind(this, i3), this), this.ng.push(i3), s2 > 0) {
          const t3 = new Tn(this, s2 - 1, s2);
          this.sg.push(t3), this.ug.insertBefore(t3.fv(), this.vg.fv());
        }
        this.ug.insertBefore(i3.fv(), this.vg.fv());
      }
      for (let n3 = 0; n3 < i2; n3++) {
        const i3 = t2[n3], s2 = this.ng[n3];
        s2.yv() !== i3 ? s2.ew(i3) : s2.sw();
      }
      this.Mg(), this.zg();
    }
    Ug(t2, i2, n2, s2) {
      const e2 = /* @__PURE__ */ new Map();
      if (null !== t2) {
        this.sn.Jn().forEach(((i3) => {
          const n3 = i3.Un().Hn(t2);
          null !== n3 && e2.set(i3, n3);
        }));
      }
      let r2;
      if (null !== t2) {
        const i3 = this.sn.Bt().en(t2)?.originalTime;
        void 0 !== i3 && (r2 = i3);
      }
      const h2 = this.Qt().cu(), a2 = this.$g(s2), l2 = (function(t3, i3) {
        const n3 = null !== t3 && t3.uu instanceof Kt ? t3.uu : void 0, s3 = t3?.bu?.te, e3 = void 0 !== i3 && -1 !== i3 ? i3 : void 0;
        return null === t3 || void 0 === t3.ee ? { jg: n3, qg: s3 } : { jg: n3, qg: s3, Yg: { ds: t3.ee, Kg: (r3 = t3.uu, h3 = t3.ee, r3 instanceof Si ? "pane-primitive" : "marker" === h3 || "primitive" === h3 ? "series-primitive" : "series"), Gg: gn(t3.ee, s3), Y_: n3, Zg: s3, Xg: e3 } };
        var r3, h3;
      })(h2, a2);
      return { Qr: r2, $n: t2 ?? void 0, Jg: i2 ?? void 0, Xg: -1 !== a2 ? a2 : void 0, jg: l2.jg, Qg: e2, qg: l2.qg, Yg: l2.Yg, tM: n2 ?? void 0 };
    }
    $g(t2) {
      let i2 = -1;
      if (t2) i2 = this.ng.indexOf(t2);
      else {
        const t3 = this.Qt().Vd().Kn();
        null !== t3 && (i2 = this.Qt().Gn().indexOf(t3));
      }
      return i2;
    }
    Wg(t2, i2, n2, s2) {
      this.$m.p((() => this.Ug(i2, n2, s2, t2)));
    }
    Hg(t2, i2, n2, s2) {
      this.jm.p((() => this.Ug(i2, n2, s2, t2)));
    }
    pg(t2, i2, n2) {
      this.Eg(this.Qt().cu()?.Mu ?? null), this.wd.p((() => this.Ug(t2, i2, n2)));
    }
    Mg() {
      const t2 = this.yn.timeScale.visible ? "" : "none";
      this.vg.fv().style.display = t2;
    }
    Ig() {
      return this.ng[0].yv().X_().N().visible;
    }
    Vg() {
      return this.ng[0].yv().J_().N().visible;
    }
    mg() {
      return "ResizeObserver" in window && (this.og = new ResizeObserver(((t2) => {
        const i2 = t2[t2.length - 1];
        if (!i2) return;
        const n2 = i2.contentRect.width, s2 = i2.contentRect.height;
        this.wg(n2, s2, true);
      })), this.og.observe(this.Kv, { box: "border-box" }), true);
    }
    xg() {
      null !== this.og && this.og.disconnect(), this.og = null;
    }
  };
  function is(t2) {
    return Boolean(t2.handleScroll.mouseWheel || t2.handleScale.mouseWheel);
  }
  function ns(t2) {
    return void 0 === t2.open && void 0 === t2.value;
  }
  function ss(t2) {
    return (function(t3) {
      return void 0 !== t3.open;
    })(t2) || (function(t3) {
      return void 0 !== t3.value;
    })(t2);
  }
  function es(t2, i2, n2, s2) {
    const e2 = n2.value, r2 = { $n: i2, wt: t2, Wt: [e2, e2, e2, e2], Qr: s2 };
    return void 0 !== n2.color && (r2.R = n2.color), r2;
  }
  function rs(t2, i2, n2, s2) {
    const e2 = n2.value, r2 = { $n: i2, wt: t2, Wt: [e2, e2, e2, e2], Qr: s2 };
    return void 0 !== n2.lineColor && (r2.vt = n2.lineColor), void 0 !== n2.topColor && (r2.ah = n2.topColor), void 0 !== n2.bottomColor && (r2.oh = n2.bottomColor), r2;
  }
  function hs(t2, i2, n2, s2) {
    const e2 = n2.value, r2 = { $n: i2, wt: t2, Wt: [e2, e2, e2, e2], Qr: s2 };
    return void 0 !== n2.topLineColor && (r2._h = n2.topLineColor), void 0 !== n2.bottomLineColor && (r2.uh = n2.bottomLineColor), void 0 !== n2.topFillColor1 && (r2.dh = n2.topFillColor1), void 0 !== n2.topFillColor2 && (r2.fh = n2.topFillColor2), void 0 !== n2.bottomFillColor1 && (r2.ph = n2.bottomFillColor1), void 0 !== n2.bottomFillColor2 && (r2.mh = n2.bottomFillColor2), r2;
  }
  function as(t2, i2, n2, s2) {
    const e2 = { $n: i2, wt: t2, Wt: [n2.open, n2.high, n2.low, n2.close], Qr: s2 };
    return void 0 !== n2.color && (e2.R = n2.color), e2;
  }
  function ls(t2, i2, n2, s2) {
    const e2 = { $n: i2, wt: t2, Wt: [n2.open, n2.high, n2.low, n2.close], Qr: s2 };
    return void 0 !== n2.color && (e2.R = n2.color), void 0 !== n2.borderColor && (e2.Ht = n2.borderColor), void 0 !== n2.wickColor && (e2.hh = n2.wickColor), e2;
  }
  function os(t2, i2, n2, s2, e2) {
    const r2 = h(e2)(n2), a2 = Math.max(...r2), l2 = Math.min(...r2), o2 = r2[r2.length - 1], _2 = [o2, a2, l2, o2], { time: u2, color: c2, ...d2 } = n2;
    return { $n: i2, wt: t2, Wt: _2, Qr: s2, ue: d2, R: c2 };
  }
  function _s(t2) {
    return void 0 !== t2.Wt;
  }
  function us(t2, i2) {
    return void 0 !== i2.customValues && (t2.iM = i2.customValues), t2;
  }
  function cs(t2) {
    return (i2, n2, s2, e2, r2, h2) => (function(t3, i3) {
      return i3 ? i3(t3) : ns(t3);
    })(s2, h2) ? us({ wt: i2, $n: n2, Qr: e2 }, s2) : us(t2(i2, n2, s2, e2, r2), s2);
  }
  function ds(t2) {
    return { Candlestick: cs(ls), Bar: cs(as), Area: cs(rs), Baseline: cs(hs), Histogram: cs(es), Line: cs(es), Custom: cs(os) }[t2];
  }
  function fs(t2) {
    return { $n: 0, nM: /* @__PURE__ */ new Map(), Oa: t2 };
  }
  function ps(t2, i2) {
    if (void 0 !== t2 && 0 !== t2.length) return { sM: i2.key(t2[0].wt), eM: i2.key(t2[t2.length - 1].wt) };
  }
  function vs(t2) {
    let i2;
    return t2.forEach(((t3) => {
      void 0 === i2 && (i2 = t3.Qr);
    })), h(i2);
  }
  var ms = class {
    constructor(t2) {
      this.rM = /* @__PURE__ */ new Map(), this.hM = /* @__PURE__ */ new Map(), this.aM = /* @__PURE__ */ new Map(), this.lM = [], this.Pu = t2;
    }
    m() {
      this.rM.clear(), this.hM.clear(), this.aM.clear(), this.lM = [];
    }
    oM(t2, i2) {
      let n2 = 0 !== this.rM.size, s2 = false;
      const e2 = this.hM.get(t2);
      if (void 0 !== e2) if (1 === this.hM.size) n2 = false, s2 = true, this.rM.clear();
      else for (const i3 of this.lM) i3.pointData.nM.delete(t2) && (s2 = true);
      let r2 = [];
      if (0 !== i2.length) {
        const n3 = i2.map(((t3) => t3.time)), e3 = this.Pu.createConverterToInternalObj(i2), h3 = ds(t2.bh()), a2 = t2.ul(), l2 = t2.cl();
        r2 = i2.map(((i3, r3) => {
          const o2 = e3(i3.time), _2 = this.Pu.key(o2);
          let u2 = this.rM.get(_2);
          void 0 === u2 && (u2 = fs(o2), this.rM.set(_2, u2), s2 = true);
          const c2 = h3(o2, u2.$n, i3, n3[r3], a2, l2);
          return u2.nM.set(t2, c2), c2;
        }));
      }
      n2 && this._M(), this.uM(t2, r2);
      let h2 = -1;
      if (s2) {
        const t3 = [];
        this.rM.forEach(((i3) => {
          t3.push({ timeWeight: 0, time: i3.Oa, pointData: i3, originalTime: vs(i3.nM) });
        })), t3.sort(((t4, i3) => this.Pu.key(t4.time) - this.Pu.key(i3.time))), h2 = this.cM(t3);
      }
      return this.dM(t2, h2, (function(t3, i3, n3) {
        const s3 = ps(t3, n3), e3 = ps(i3, n3);
        if (void 0 !== s3 && void 0 !== e3) return { fM: false, Va: s3.eM >= e3.eM && s3.sM >= e3.sM };
      })(this.hM.get(t2), e2, this.Pu));
    }
    if(t2) {
      return this.oM(t2, []);
    }
    pM(t2, i2, n2) {
      if (n2 && t2.Fa()) throw new Error("Historical updates are not supported when conflation is enabled. Conflation requires data to be processed in order.");
      const s2 = i2;
      !(function(t3) {
        void 0 === t3.Qr && (t3.Qr = t3.time);
      })(s2), this.Pu.preprocessData(i2);
      const e2 = this.Pu.createConverterToInternalObj([i2])(i2.time), r2 = this.aM.get(t2);
      if (!n2 && void 0 !== r2 && this.Pu.key(e2) < this.Pu.key(r2)) throw new Error(`Cannot update oldest data, last time=${r2}, new time=${e2}`);
      let h2 = this.rM.get(this.Pu.key(e2));
      if (n2 && void 0 === h2) throw new Error("Cannot update non-existing data point when historicalUpdate is true");
      const a2 = void 0 === h2;
      void 0 === h2 && (h2 = fs(e2), this.rM.set(this.Pu.key(e2), h2));
      const l2 = ds(t2.bh()), o2 = t2.ul(), _2 = t2.cl(), u2 = l2(e2, h2.$n, i2, s2.Qr, o2, _2), c2 = !n2 && !a2 && void 0 !== r2 && this.Pu.key(e2) === this.Pu.key(r2);
      h2.nM.set(t2, u2), n2 ? this.vM(t2, u2, h2.$n) : c2 && t2.Fa() && _s(u2) ? (t2.Rr(u2), this.mM(t2, u2)) : this.mM(t2, u2);
      const d2 = { Va: _s(u2), fM: n2 };
      if (!a2) return this.dM(t2, -1, d2);
      const f2 = { timeWeight: 0, time: h2.Oa, pointData: h2, originalTime: vs(h2.nM) }, p2 = yt(this.lM, this.Pu.key(f2.time), ((t3, i3) => this.Pu.key(t3.time) < i3));
      this.lM.splice(p2, 0, f2);
      for (let t3 = p2; t3 < this.lM.length; ++t3) ws(this.lM[t3].pointData, t3);
      return this.Pu.fillWeightsForPoints(this.lM, p2), this.dM(t2, p2, d2);
    }
    wM(t2, i2) {
      const n2 = this.hM.get(t2);
      if (void 0 === n2 || i2 <= 0) return [[], this.gM()];
      i2 = Math.min(i2, n2.length);
      const s2 = n2.splice(-i2).reverse();
      0 === n2.length ? this.aM.delete(t2) : this.aM.set(t2, n2[n2.length - 1].wt);
      for (const i3 of s2) {
        const n3 = this.rM.get(this.Pu.key(i3.wt));
        if (n3 && (n3.nM.delete(t2), 0 === n3.nM.size)) {
          this.rM.delete(this.Pu.key(n3.Oa)), this.lM.splice(n3.$n, 1);
          for (let t3 = n3.$n; t3 < this.lM.length; ++t3) ws(this.lM[t3].pointData, t3);
        }
      }
      return [s2, this.dM(t2, this.lM.length - 1, { fM: false, Va: false })];
    }
    mM(t2, i2) {
      let n2 = this.hM.get(t2);
      void 0 === n2 && (n2 = [], this.hM.set(t2, n2));
      const s2 = 0 !== n2.length ? n2[n2.length - 1] : null;
      null === s2 || this.Pu.key(i2.wt) > this.Pu.key(s2.wt) ? _s(i2) && n2.push(i2) : _s(i2) ? n2[n2.length - 1] = i2 : n2.splice(-1, 1), this.aM.set(t2, i2.wt);
    }
    vM(t2, i2, n2) {
      const s2 = this.hM.get(t2);
      if (void 0 === s2) return;
      const e2 = yt(s2, n2, ((t3, i3) => t3.$n < i3));
      _s(i2) ? s2[e2] = i2 : s2.splice(e2, 1);
    }
    uM(t2, i2) {
      0 !== i2.length ? (this.hM.set(t2, i2.filter(_s)), this.aM.set(t2, i2[i2.length - 1].wt)) : (this.hM.delete(t2), this.aM.delete(t2));
    }
    _M() {
      for (const t2 of this.lM) 0 === t2.pointData.nM.size && this.rM.delete(this.Pu.key(t2.time));
    }
    cM(t2) {
      let i2 = -1;
      for (let n2 = 0; n2 < this.lM.length && n2 < t2.length; ++n2) {
        const s2 = this.lM[n2], e2 = t2[n2];
        if (this.Pu.key(s2.time) !== this.Pu.key(e2.time)) {
          i2 = n2;
          break;
        }
        e2.timeWeight = s2.timeWeight, ws(e2.pointData, n2);
      }
      if (-1 === i2 && this.lM.length !== t2.length && (i2 = Math.min(this.lM.length, t2.length)), -1 === i2) return -1;
      for (let n2 = i2; n2 < t2.length; ++n2) ws(t2[n2].pointData, n2);
      return this.Pu.fillWeightsForPoints(t2, i2), this.lM = t2, i2;
    }
    MM() {
      if (0 === this.hM.size) return null;
      let t2 = 0;
      return this.hM.forEach(((i2) => {
        0 !== i2.length && (t2 = Math.max(t2, i2[i2.length - 1].$n));
      })), t2;
    }
    dM(t2, i2, n2) {
      const s2 = this.gM();
      if (-1 !== i2) this.hM.forEach(((i3, e2) => {
        s2.Y_.set(e2, { ue: i3, bM: e2 === t2 ? n2 : void 0 });
      })), this.hM.has(t2) || s2.Y_.set(t2, { ue: [], bM: n2 }), s2.Bt.SM = this.lM, s2.Bt.xM = i2;
      else {
        const i3 = this.hM.get(t2);
        s2.Y_.set(t2, { ue: i3 || [], bM: n2 });
      }
      return s2;
    }
    gM() {
      return { Y_: /* @__PURE__ */ new Map(), Bt: { Dc: this.MM() } };
    }
  };
  function ws(t2, i2) {
    t2.$n = i2, t2.nM.forEach(((t3) => {
      t3.$n = i2;
    }));
  }
  function gs(t2, i2) {
    return t2._t < i2;
  }
  function Ms(t2, i2) {
    return i2 < t2._t;
  }
  function bs(t2, i2, n2, s2) {
    return yt(t2, i2, gs, n2, s2);
  }
  function Ss(t2, i2, n2, s2) {
    return Pt(t2, i2, Ms, n2, s2);
  }
  function xs(t2, i2, n2) {
    return { ne: t2, se: i2, ee: n2 };
  }
  function Cs(t2, i2, n2, s2) {
    return t2 >= i2 - s2 && t2 <= n2 + s2;
  }
  function ys(t2, i2, n2, s2, e2, r2) {
    const h2 = e2 - n2, a2 = r2 - s2;
    if (0 === h2 && 0 === a2) return Math.hypot(t2 - n2, i2 - s2);
    const l2 = ((t2 - n2) * h2 + (i2 - s2) * a2) / (h2 * h2 + a2 * a2), o2 = Math.max(0, Math.min(1, l2)), _2 = n2 + h2 * o2, u2 = s2 + a2 * o2;
    return Math.hypot(t2 - _2, i2 - u2);
  }
  var Ps = [0, 0];
  function ks(t2, i2, n2) {
    return void 0 === i2 || i2.wt !== t2.wt - 1 ? t2._t - n2 / 2 : (i2._t + t2._t) / 2;
  }
  function Ts(t2, i2, n2) {
    return void 0 === i2 || i2.wt !== t2.wt + 1 ? t2._t + n2 / 2 : (t2._t + i2._t) / 2;
  }
  function Rs(t2, i2, n2, s2, e2, r2, h2) {
    if (null === i2 || i2.from >= i2.to || 0 === t2.length) return null;
    const a2 = e2 / 2 + r2, l2 = bs(t2, n2 - a2, i2.from, i2.to), o2 = Ss(t2, n2 + a2, l2, i2.to);
    if (l2 >= o2) return null;
    let _2 = Number.POSITIVE_INFINITY;
    for (let a3 = l2; a3 < o2; a3++) {
      const l3 = t2[a3], o3 = a3 > i2.from ? t2[a3 - 1] : void 0, u2 = a3 < i2.to - 1 ? t2[a3 + 1] : void 0, c2 = ks(l3, o3, e2) - r2, d2 = Ts(l3, u2, e2) + r2;
      if (n2 < c2 || n2 > d2) continue;
      h2(l3, Ps);
      const f2 = Ps[0], p2 = Ps[1], v2 = Math.min(f2, p2), m2 = Math.max(f2, p2), w2 = v2 - r2, g2 = m2 + r2;
      if (s2 >= v2 && s2 <= m2) _2 = Math.min(_2, 0);
      else if (s2 >= w2 && s2 <= g2) {
        const t3 = Math.min(Math.abs(s2 - v2), Math.abs(m2 - s2));
        _2 = Math.min(_2, t3);
      }
    }
    return Number.isFinite(_2) ? xs(_2, 0, "series-range") : null;
  }
  function Ds(t2, i2) {
    return t2.wt < i2;
  }
  function Is(t2, i2) {
    return i2 < t2.wt;
  }
  function Vs(t2, i2, n2) {
    const s2 = i2.Na(), e2 = i2.bi(), r2 = yt(t2, s2, Ds), h2 = Pt(t2, e2, Is);
    if (!n2) return { from: r2, to: h2 };
    let a2 = r2, l2 = h2;
    return r2 > 0 && r2 < t2.length && t2[r2].wt >= s2 && (a2 = r2 - 1), h2 > 0 && h2 < t2.length && t2[h2 - 1].wt <= e2 && (l2 = h2 + 1), { from: a2, to: l2 };
  }
  var Es = class {
    constructor(t2, i2, n2) {
      this.CM = true, this.yM = true, this.PM = true, this.kM = [], this.TM = null, this.RM = -1, this.ae = t2, this.le = i2, this.DM = n2;
    }
    Pt(t2) {
      this.CM = true, "data" === t2 && (this.yM = true), "options" === t2 && (this.PM = true);
    }
    Tt() {
      return this.ae.It() ? (this.IM(), null === this.TM ? null : this.VM) : null;
    }
    Qs(t2, i2) {
      return this.ae.It() ? (this.IM(), null === this.TM ? null : this.EM(t2, i2)) : null;
    }
    EM(t2, i2) {
      return null;
    }
    BM() {
      this.kM = this.kM.map(((t2) => ({ ...t2, ...this.ae.Sa().Sh(t2.wt) })));
    }
    AM() {
      this.TM = null;
    }
    IM() {
      const t2 = this.le.Bt(), i2 = t2.N().enableConflation ? t2.sd() : 0;
      i2 !== this.RM && (this.yM = true, this.RM = i2), this.yM && (this.zM(), this.yM = false), this.PM && (this.BM(), this.PM = false), this.CM && (this.LM(), this.CM = false);
    }
    LM() {
      const t2 = this.ae.Ft(), i2 = this.le.Bt();
      if (this.AM(), i2.Zi() || t2.Zi()) return;
      const n2 = i2.Be();
      if (null === n2) return;
      if (0 === this.ae.Un().Th()) return;
      const s2 = this.ae.zt();
      null !== s2 && (this.TM = Vs(this.kM, n2, this.DM), this.OM(t2, i2, s2.Wt), this.NM());
    }
  };
  var Bs = class {
    constructor(t2, i2) {
      this.FM = t2, this.Ki = i2;
    }
    st(t2, i2, n2) {
      this.FM.draw(t2, this.Ki, i2, n2);
    }
  };
  function As(t2) {
    switch (t2) {
      case "point":
        return 2;
      case "range":
        return 0;
      default:
        return 1;
    }
  }
  var zs = class extends Es {
    constructor(t2, i2, n2) {
      super(t2, i2, false), this.Yh = n2, this.FM = this.Yh.renderer(), this.VM = new Bs(this.FM, ((t3) => this.WM(t3)));
    }
    get Ma() {
      return this.Yh.conflationReducer;
    }
    Ha(t2) {
      return this.Yh.priceValueBuilder(t2);
    }
    dl(t2) {
      return this.Yh.isWhitespace(t2);
    }
    EM(t2, i2) {
      const n2 = this.FM.hitTest?.(t2, i2, ((t3) => this.WM(t3)));
      if (null != n2) return { ne: (s2 = n2).distance, se: As(s2.type), ee: "custom", Mu: s2.cursorStyle, te: s2.objectId, ie: s2.hitTestData };
      var s2;
      const e2 = Rs(this.kM, this.TM, t2, i2, this.le.Bt().ml(), this.ae.N().hitTestTolerance, ((t3, i3) => {
        const n3 = t3.HM;
        let s3 = NaN, e3 = NaN;
        if (void 0 !== n3 && !this.Yh.isWhitespace(n3)) for (const t4 of this.Yh.priceValueBuilder(n3)) {
          const i4 = this.WM(t4);
          null !== i4 && (s3 = Number.isNaN(s3) ? i4 : Math.min(s3, i4), e3 = Number.isNaN(e3) ? i4 : Math.max(e3, i4));
        }
        i3[0] = s3, i3[1] = e3;
      }));
      return null === e2 ? null : { ...e2, ee: "custom" };
    }
    zM() {
      const t2 = this.ae.Sa();
      this.kM = this.ae.Ua().Eh().map(((i2) => ({ wt: i2.$n, _t: NaN, ...t2.Sh(i2.$n), HM: i2.ue })));
    }
    OM(t2, i2) {
      i2.Ic(this.kM, m(this.TM));
    }
    NM() {
      this.Yh.update({ bars: this.kM.map(Ls), barSpacing: this.le.Bt().ml(), visibleRange: this.TM, conflationFactor: this.le.Bt().sd() }, this.ae.N());
    }
    WM(t2) {
      const i2 = this.ae.zt();
      return null === i2 ? null : this.ae.Ft().Nt(t2, i2.Wt);
    }
  };
  function Ls(t2) {
    return { x: t2._t, time: t2.wt, originalData: t2.HM, barColor: t2.sh };
  }
  var Os = { color: "#2196f3" };
  var Ns = (t2, i2, n2) => {
    const s2 = l(n2);
    return new zs(t2, i2, s2);
  };
  function Fs(t2) {
    const i2 = { value: t2.Wt[3], time: t2.Qr };
    return void 0 !== t2.iM && (i2.customValues = t2.iM), i2;
  }
  function Ws(t2) {
    const i2 = Fs(t2);
    return void 0 !== t2.R && (i2.color = t2.R), i2;
  }
  function Hs(t2) {
    const i2 = Fs(t2);
    return void 0 !== t2.vt && (i2.lineColor = t2.vt), void 0 !== t2.ah && (i2.topColor = t2.ah), void 0 !== t2.oh && (i2.bottomColor = t2.oh), i2;
  }
  function Us(t2) {
    const i2 = Fs(t2);
    return void 0 !== t2._h && (i2.topLineColor = t2._h), void 0 !== t2.uh && (i2.bottomLineColor = t2.uh), void 0 !== t2.dh && (i2.topFillColor1 = t2.dh), void 0 !== t2.fh && (i2.topFillColor2 = t2.fh), void 0 !== t2.ph && (i2.bottomFillColor1 = t2.ph), void 0 !== t2.mh && (i2.bottomFillColor2 = t2.mh), i2;
  }
  function $s(t2) {
    const i2 = { open: t2.Wt[0], high: t2.Wt[1], low: t2.Wt[2], close: t2.Wt[3], time: t2.Qr };
    return void 0 !== t2.iM && (i2.customValues = t2.iM), i2;
  }
  function js(t2) {
    const i2 = $s(t2);
    return void 0 !== t2.R && (i2.color = t2.R), i2;
  }
  function qs(t2) {
    const i2 = $s(t2), { R: n2, Ht: s2, hh: e2 } = t2;
    return void 0 !== n2 && (i2.color = n2), void 0 !== s2 && (i2.borderColor = s2), void 0 !== e2 && (i2.wickColor = e2), i2;
  }
  function Ys(t2) {
    return { Area: Hs, Line: Ws, Baseline: Us, Histogram: Ws, Bar: js, Candlestick: qs, Custom: Ks }[t2];
  }
  function Ks(t2) {
    const i2 = t2.Qr;
    return { ...t2.ue, time: i2 };
  }
  var Gs = { vertLine: { color: "#9598A1", width: 1, style: 3, visible: true, labelVisible: true, labelBackgroundColor: "#131722" }, horzLine: { color: "#9598A1", width: 1, style: 3, visible: true, labelVisible: true, labelBackgroundColor: "#131722" }, mode: 1, doNotSnapToHiddenSeriesIndices: false };
  var Zs = { vertLines: { color: "#D6DCDE", style: 0, visible: true }, horzLines: { color: "#D6DCDE", style: 0, visible: true } };
  var Xs = { background: { type: "solid", color: "#FFFFFF" }, textColor: "#191919", fontSize: 12, fontFamily: w, panes: { enableResize: true, separatorColor: "#E0E3EB", separatorHoverColor: "rgba(178, 181, 189, 0.2)" }, attributionLogo: true, colorSpace: "srgb", colorParsers: [] };
  var Js = { autoScale: true, mode: 0, invertScale: false, alignLabels: true, borderVisible: true, borderColor: "#2B2B43", entireTextOnly: false, visible: false, ticksVisible: false, scaleMargins: { bottom: 0.1, top: 0.2 }, minimumWidth: 0, ensureEdgeTickMarksVisible: false, tickMarkDensity: 2.5 };
  var Qs = { rightOffset: 0, barSpacing: 6, minBarSpacing: 0.5, maxBarSpacing: 0, fixLeftEdge: false, fixRightEdge: false, lockVisibleTimeRangeOnResize: false, rightBarStaysOnScroll: false, borderVisible: true, borderColor: "#2B2B43", visible: true, timeVisible: false, secondsVisible: true, shiftVisibleRangeOnNewBar: true, allowShiftVisibleRangeOnWhitespaceReplacement: false, ticksVisible: false, uniformDistribution: false, minimumHeight: 0, allowBoldLabels: true, ignoreWhitespaceIndices: false, enableConflation: false, conflationThresholdFactor: 1, precomputeConflationOnInit: false, precomputeConflationPriority: "background" };
  function te() {
    return { addDefaultPane: true, hoveredSeriesOnTop: true, width: 0, height: 0, autoSize: false, layout: Xs, crosshair: Gs, grid: Zs, overlayPriceScales: { ...Js }, leftPriceScale: { ...Js, visible: false }, rightPriceScale: { ...Js, visible: true }, defaultVisiblePriceScaleId: "right", timeScale: Qs, localization: { locale: vn ? navigator.language : "", dateFormat: "dd MMM 'yy" }, handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: true }, handleScale: { axisPressedMouseMove: { time: true, price: true }, axisDoubleClickReset: { time: true, price: true }, mouseWheel: true, pinch: true }, kineticScroll: { mouse: false, touch: true }, trackingMode: { exitMode: 1 } };
  }
  var ie = class {
    constructor(t2, i2, n2) {
      this.hv = t2, this.UM = i2, this.$M = n2 ?? 0;
    }
    applyOptions(t2) {
      this.hv.Qt().Dd(this.UM, t2, this.$M);
    }
    options() {
      return this.Ki().N();
    }
    width() {
      return q(this.UM) ? this.hv.Dg(this.UM) : 0;
    }
    setVisibleRange(t2) {
      this.setAutoScale(false), this.Ki().Go(new dt(t2.from, t2.to));
    }
    getVisibleRange() {
      let t2, i2, n2 = this.Ki().ar();
      if (null === n2) return null;
      if (this.Ki().ho()) {
        const s2 = this.Ki().S_(), e2 = Ui(s2);
        n2 = ci(n2, this.Ki().lo()), t2 = Number((Math.round(n2.Je() / s2) * s2).toFixed(e2)), i2 = Number((Math.round(n2.Qe() / s2) * s2).toFixed(e2));
      } else t2 = n2.Je(), i2 = n2.Qe();
      return { from: t2, to: i2 };
    }
    setAutoScale(t2) {
      this.applyOptions({ autoScale: t2 });
    }
    Ki() {
      return a(this.hv.Qt().Id(this.UM, this.$M)).Ft;
    }
  };
  var ne = class {
    constructor(t2, i2, n2, s2) {
      this.hv = t2, this.yt = n2, this.jM = i2, this.qM = s2;
    }
    getHeight() {
      return this.yt.$t();
    }
    setHeight(t2) {
      const i2 = this.hv.Qt(), n2 = i2._f(this.yt);
      i2.zd(n2, t2);
    }
    getStretchFactor() {
      return this.yt.F_();
    }
    setStretchFactor(t2) {
      this.yt.W_(t2), this.hv.Qt().ka();
    }
    paneIndex() {
      return this.hv.Qt()._f(this.yt);
    }
    moveTo(t2) {
      const i2 = this.paneIndex();
      i2 !== t2 && (r(t2 >= 0 && t2 < this.hv.lv().length, "Invalid pane index"), this.hv.Qt().Od(i2, t2));
    }
    getSeries() {
      return this.yt.Y_().map(((t2) => this.jM(t2))) ?? [];
    }
    getHTMLElement() {
      const t2 = this.hv.lv();
      return t2 && 0 !== t2.length && t2[this.paneIndex()] ? t2[this.paneIndex()].fv() : null;
    }
    attachPrimitive(t2) {
      this.yt.ol(t2), t2.attached && t2.attached({ chart: this.qM, requestUpdate: () => this.yt.Qt().ka() });
    }
    detachPrimitive(t2) {
      this.yt._l(t2);
    }
    priceScale(t2) {
      if (null === this.yt.O_(t2)) throw new Error(`Cannot find price scale with id: ${t2}`);
      return new ie(this.hv, t2, this.paneIndex());
    }
    setPreserveEmptyPane(t2) {
      this.yt.j_(t2);
    }
    preserveEmptyPane() {
      return this.yt.q_();
    }
    addCustomSeries(t2, i2 = {}, n2 = 0) {
      return this.qM.addCustomSeries(t2, i2, n2);
    }
    addSeries(t2, i2 = {}) {
      return this.qM.addSeries(t2, i2, this.paneIndex());
    }
  };
  var se = { color: "#FF0000", price: 0, lineStyle: 2, lineWidth: 1, lineVisible: true, axisLabelVisible: true, title: "", axisLabelColor: "", axisLabelTextColor: "" };
  var ee = class {
    constructor(t2) {
      this._r = t2;
    }
    applyOptions(t2) {
      this._r.vr(t2);
    }
    options() {
      return this._r.N();
    }
    YM() {
      return this._r;
    }
  };
  var re = class {
    constructor(t2, i2, n2, s2, e2, r2) {
      this.KM = new o(), this.ae = t2, this.GM = i2, this.ZM = n2, this.Pu = e2, this.qM = s2, this.XM = r2;
    }
    m() {
      this.KM.m();
    }
    priceFormatter() {
      return this.ae.sl();
    }
    priceToCoordinate(t2) {
      const i2 = this.ae.zt();
      return null === i2 ? null : this.ae.Ft().Nt(t2, i2.Wt);
    }
    coordinateToPrice(t2) {
      const i2 = this.ae.zt();
      return null === i2 ? null : this.ae.Ft().Tn(t2, i2.Wt);
    }
    barsInLogicalRange(t2) {
      if (null === t2) return null;
      const i2 = new Bi(new Ii(t2.from, t2.to)).Uu(), n2 = this.ae.Un();
      if (n2.Zi()) return null;
      const s2 = n2.Hn(i2.Na(), 1), e2 = n2.Hn(i2.bi(), -1), r2 = a(n2.Rh()), h2 = a(n2.Qn());
      if (null !== s2 && null !== e2 && s2.$n > e2.$n) return { barsBefore: t2.from - r2, barsAfter: h2 - t2.to };
      const l2 = { barsBefore: null === s2 || s2.$n === r2 ? t2.from - r2 : s2.$n - r2, barsAfter: null === e2 || e2.$n === h2 ? h2 - t2.to : h2 - e2.$n };
      return null !== s2 && null !== e2 && (l2.from = s2.Qr, l2.to = e2.Qr), l2;
    }
    setData(t2) {
      this.Pu, this.ae.bh(), this.GM.JM(this.ae, t2), this.QM("full");
    }
    update(t2, i2 = false) {
      this.ae.bh(), this.GM.tb(this.ae, t2, i2), this.QM("update");
    }
    pop(t2 = 1) {
      const i2 = this.GM.ib(this.ae, t2);
      0 !== i2.length && this.QM("update");
      const n2 = Ys(this.seriesType());
      return i2.map(((t3) => n2(t3)));
    }
    dataByIndex(t2, i2) {
      const n2 = this.ae.Un().Hn(t2, i2);
      if (null === n2) return null;
      return Ys(this.seriesType())(n2);
    }
    data() {
      const t2 = Ys(this.seriesType());
      return this.ae.Un().Eh().map(((i2) => t2(i2)));
    }
    subscribeDataChanged(t2) {
      this.KM.i(t2);
    }
    unsubscribeDataChanged(t2) {
      this.KM._(t2);
    }
    applyOptions(t2) {
      this.ae.vr(t2);
    }
    options() {
      return p(this.ae.N());
    }
    priceScale() {
      return this.ZM.priceScale(this.ae.Ft().pl(), this.getPane().paneIndex());
    }
    createPriceLine(t2) {
      const i2 = _(p(se), t2), n2 = this.ae.Ba(i2);
      return new ee(n2);
    }
    removePriceLine(t2) {
      this.ae.Aa(t2.YM());
    }
    priceLines() {
      return this.ae.za().map(((t2) => new ee(t2)));
    }
    seriesType() {
      return this.ae.bh();
    }
    lastValueData(t2) {
      const i2 = this.ae.Ae(t2);
      return i2.ze ? { noData: true } : { noData: false, price: i2.gt, color: i2.R };
    }
    attachPrimitive(t2) {
      this.ae.ol(t2), t2.attached && t2.attached({ chart: this.qM, series: this, requestUpdate: () => this.ae.Qt().ka(), horzScaleBehavior: this.Pu });
    }
    detachPrimitive(t2) {
      this.ae._l(t2), t2.detached && t2.detached(), this.ae.Qt().ka();
    }
    getPane() {
      const t2 = this.ae, i2 = a(this.ae.Qt().Ks(t2));
      return this.XM(i2);
    }
    moveToPane(t2) {
      this.ae.Qt().rf(this.ae, t2);
    }
    seriesOrder() {
      const t2 = this.ae.Qt().Ks(this.ae);
      return null === t2 ? -1 : t2.Y_().indexOf(this.ae);
    }
    setSeriesOrder(t2) {
      const i2 = this.ae.Qt().Ks(this.ae);
      null !== i2 && i2.vu(this.ae, t2);
    }
    QM(t2) {
      this.KM.v() && this.KM.p(t2);
    }
  };
  var he = class {
    constructor(t2, i2, n2) {
      this.nb = new o(), this.Qu = new o(), this.Nw = new o(), this.sn = t2, this.ia = t2.Bt(), this.vg = i2, this.ia.Zc().i(this.sb.bind(this)), this.ia.Xc().i(this.eb.bind(this)), this.vg.qw().i(this.rb.bind(this)), this.Pu = n2;
    }
    m() {
      this.ia.Zc().u(this), this.ia.Xc().u(this), this.vg.qw().u(this), this.nb.m(), this.Qu.m(), this.Nw.m();
    }
    scrollPosition() {
      return this.ia.Oc();
    }
    scrollToPosition(t2, i2) {
      i2 ? this.ia.Yc(t2, 1e3) : this.sn.Ms(t2);
    }
    scrollToRealTime() {
      this.ia.qc();
    }
    getVisibleRange() {
      const t2 = this.ia.xc();
      return null === t2 ? null : { from: t2.from.originalTime, to: t2.to.originalTime };
    }
    setVisibleRange(t2) {
      const i2 = { from: this.Pu.convertHorzItemToInternal(t2.from), to: this.Pu.convertHorzItemToInternal(t2.to) }, n2 = this.ia.kc(i2);
      this.sn.sf(n2);
    }
    getVisibleLogicalRange() {
      const t2 = this.ia.Sc();
      return null === t2 ? null : { from: t2.Na(), to: t2.bi() };
    }
    setVisibleLogicalRange(t2) {
      r(t2.from <= t2.to, "The from index cannot be after the to index."), this.sn.sf(t2);
    }
    resetTimeScale() {
      this.sn.ws();
    }
    fitContent() {
      this.sn.td();
    }
    logicalToCoordinate(t2) {
      const i2 = this.sn.Bt();
      return i2.Zi() ? null : i2.jt(t2);
    }
    coordinateToLogical(t2) {
      return this.ia.Zi() ? null : this.ia.Vc(t2);
    }
    timeToIndex(t2, i2) {
      const n2 = this.Pu.convertHorzItemToInternal(t2);
      return this.ia.gc(n2, i2);
    }
    timeToCoordinate(t2) {
      const i2 = this.timeToIndex(t2, false);
      return null === i2 ? null : this.ia.jt(i2);
    }
    coordinateToTime(t2) {
      const i2 = this.sn.Bt(), n2 = i2.Vc(t2), s2 = i2.en(n2);
      return null === s2 ? null : s2.originalTime;
    }
    width() {
      return this.vg.pv().width;
    }
    height() {
      return this.vg.pv().height;
    }
    subscribeVisibleTimeRangeChange(t2) {
      this.nb.i(t2);
    }
    unsubscribeVisibleTimeRangeChange(t2) {
      this.nb._(t2);
    }
    subscribeVisibleLogicalRangeChange(t2) {
      this.Qu.i(t2);
    }
    unsubscribeVisibleLogicalRangeChange(t2) {
      this.Qu._(t2);
    }
    subscribeSizeChange(t2) {
      this.Nw.i(t2);
    }
    unsubscribeSizeChange(t2) {
      this.Nw._(t2);
    }
    applyOptions(t2) {
      this.ia.vr(t2);
    }
    options() {
      return { ...p(this.ia.N()), barSpacing: this.ia.ml() };
    }
    sb() {
      this.nb.v() && this.nb.p(this.getVisibleRange());
    }
    eb() {
      this.Qu.v() && this.Qu.p(this.getVisibleLogicalRange());
    }
    rb(t2) {
      this.Nw.p(t2.width, t2.height);
    }
  };
  function ae(t2) {
    return (function(t3) {
      if (f(t3.handleScale)) {
        const i3 = t3.handleScale;
        t3.handleScale = { axisDoubleClickReset: { time: i3, price: i3 }, axisPressedMouseMove: { time: i3, price: i3 }, mouseWheel: i3, pinch: i3 };
      } else if (void 0 !== t3.handleScale) {
        const { axisPressedMouseMove: i3, axisDoubleClickReset: n2 } = t3.handleScale;
        f(i3) && (t3.handleScale.axisPressedMouseMove = { time: i3, price: i3 }), f(n2) && (t3.handleScale.axisDoubleClickReset = { time: n2, price: n2 });
      }
      const i2 = t3.handleScroll;
      f(i2) && (t3.handleScroll = { horzTouchDrag: i2, vertTouchDrag: i2, mouseWheel: i2, pressedMouseMove: i2 });
    })(t2), t2;
  }
  var le = class {
    constructor(t2, i2, n2) {
      this.hb = /* @__PURE__ */ new Map(), this.ab = /* @__PURE__ */ new Map(), this.lb = new o(), this.ob = new o(), this._b = new o(), this.dd = /* @__PURE__ */ new WeakMap(), this.ub = new ms(i2);
      const s2 = void 0 === n2 ? p(te()) : _(p(te()), ae(n2));
      this.cb = i2, this.hv = new ts(t2, s2, i2), this.hv.mw().i(((t3) => {
        this.lb.v() && this.lb.p(this.fb(t3()));
      }), this), this.hv.ww().i(((t3) => {
        this.ob.v() && this.ob.p(this.fb(t3()));
      }), this), this.hv.Ed().i(((t3) => {
        this._b.v() && this._b.p(this.fb(t3()));
      }), this);
      const e2 = this.hv.Qt();
      this.pb = new he(e2, this.hv.bg(), this.cb);
    }
    remove() {
      this.hv.mw().u(this), this.hv.ww().u(this), this.hv.Ed().u(this), this.pb.m(), this.hv.m(), this.hb.clear(), this.ab.clear(), this.lb.m(), this.ob.m(), this._b.m(), this.ub.m();
    }
    resize(t2, i2, n2) {
      this.autoSizeActive() || this.hv.wg(t2, i2, n2);
    }
    addCustomSeries(t2, i2 = {}, n2 = 0) {
      const s2 = ((t3) => ({ type: "Custom", isBuiltIn: false, defaultOptions: { ...Os, ...t3.defaultOptions() }, mb: Ns, wb: t3 }))(l(t2));
      return this.gb(s2, i2, n2);
    }
    addSeries(t2, i2 = {}, n2 = 0) {
      return this.gb(t2, i2, n2);
    }
    removeSeries(t2) {
      const i2 = h(this.hb.get(t2)), n2 = this.ub.if(i2);
      this.hv.Qt().if(i2), this.Mb(n2), this.hb.delete(t2), this.ab.delete(i2);
    }
    JM(t2, i2) {
      this.Mb(this.ub.oM(t2, i2));
    }
    tb(t2, i2, n2) {
      this.Mb(this.ub.pM(t2, i2, n2));
    }
    ib(t2, i2) {
      const [n2, s2] = this.ub.wM(t2, i2);
      return 0 !== n2.length && this.Mb(s2), n2;
    }
    subscribeClick(t2) {
      this.lb.i(t2);
    }
    unsubscribeClick(t2) {
      this.lb._(t2);
    }
    subscribeCrosshairMove(t2) {
      this._b.i(t2);
    }
    unsubscribeCrosshairMove(t2) {
      this._b._(t2);
    }
    subscribeDblClick(t2) {
      this.ob.i(t2);
    }
    unsubscribeDblClick(t2) {
      this.ob._(t2);
    }
    priceScale(t2, i2 = 0) {
      return new ie(this.hv, t2, i2);
    }
    timeScale() {
      return this.pb;
    }
    applyOptions(t2) {
      this.hv.vr(ae(t2));
    }
    options() {
      return this.hv.N();
    }
    takeScreenshot(t2 = false, i2 = false) {
      let n2, s2;
      try {
        i2 || (n2 = this.hv.Qt().N().crosshair.mode, this.hv.vr({ crosshair: { mode: 2 } })), s2 = this.hv.Tg(t2);
      } finally {
        i2 || void 0 === n2 || this.hv.Qt().vr({ crosshair: { mode: n2 } });
      }
      return s2;
    }
    addPane(t2 = false) {
      const i2 = this.hv.Qt().uf();
      return i2.j_(t2), this.bb(i2);
    }
    removePane(t2) {
      this.hv.Qt().Ad(t2);
    }
    swapPanes(t2, i2) {
      this.hv.Qt().Ld(t2, i2);
    }
    autoSizeActive() {
      return this.hv.Cg();
    }
    chartElement() {
      return this.hv.gv();
    }
    panes() {
      return this.hv.Qt().Gn().map(((t2) => this.bb(t2)));
    }
    paneSize(t2 = 0) {
      const i2 = this.hv.Ag(t2);
      return { height: i2.height, width: i2.width };
    }
    setCrosshairPosition(t2, i2, n2) {
      const s2 = this.hb.get(n2);
      if (void 0 === s2) return;
      const e2 = this.hv.Qt().Ks(s2);
      null !== e2 && this.hv.Qt().Gd(t2, i2, e2);
    }
    clearCrosshairPosition() {
      this.hv.Qt().Zd(true);
    }
    horzBehaviour() {
      return this.cb;
    }
    gb(i2, n2 = {}, s2 = 0) {
      r(void 0 !== i2.mb), (function(t2) {
        if (void 0 === t2 || "custom" === t2.type) return;
        const i3 = t2;
        void 0 !== i3.minMove && void 0 === i3.precision && (i3.precision = Ui(i3.minMove));
      })(n2.priceFormat), "Candlestick" === i2.type && (function(t2) {
        void 0 !== t2.borderColor && (t2.borderUpColor = t2.borderColor, t2.borderDownColor = t2.borderColor), void 0 !== t2.wickColor && (t2.wickUpColor = t2.wickColor, t2.wickDownColor = t2.wickColor);
      })(n2);
      const e2 = _(p(t), p(i2.defaultOptions), n2), h2 = i2.mb, a2 = new Kt(this.hv.Qt(), i2.type, e2, h2, i2.wb);
      this.hv.Qt().Qd(a2, s2);
      const l2 = new re(a2, this, this, this, this.cb, ((t2) => this.bb(t2)));
      return this.hb.set(l2, a2), this.ab.set(a2, l2), l2;
    }
    Mb(t2) {
      const i2 = this.hv.Qt();
      for (const i3 of t2.Y_.keys()) i3.Ia();
      i2.Xd(t2.Bt.Dc, t2.Bt.SM, t2.Bt.xM), t2.Y_.forEach(((t3, i3) => i3.ht(t3.ue, t3.bM))), i2.Bt().dc(), i2.zc();
    }
    Sb(t2) {
      return h(this.ab.get(t2));
    }
    xb(t2) {
      return void 0 !== t2 && this.ab.has(t2) ? this.Sb(t2) : void 0;
    }
    fb(t2) {
      const i2 = /* @__PURE__ */ new Map();
      t2.Qg.forEach(((t3, n3) => {
        const s3 = n3.bh(), e2 = Ys(s3)(t3);
        if ("Custom" !== s3) r(ss(e2));
        else {
          const t4 = n3.cl();
          r(!t4 || false === t4(e2));
        }
        i2.set(this.Sb(n3), e2);
      }));
      const n2 = this.xb(t2.jg), s2 = void 0 === t2.Yg ? void 0 : { type: t2.Yg.ds, sourceKind: t2.Yg.Kg, objectKind: t2.Yg.Gg, series: this.xb(t2.Yg.Y_), objectId: t2.Yg.Zg, paneIndex: t2.Yg.Xg };
      return { time: t2.Qr, logical: t2.$n, point: t2.Jg, paneIndex: t2.Xg, hoveredInfo: s2, hoveredSeries: n2, hoveredObjectId: t2.qg, seriesData: i2, sourceEvent: t2.tM };
    }
    bb(t2) {
      let i2 = this.dd.get(t2);
      return i2 || (i2 = new ne(this.hv, ((t3) => this.Sb(t3)), t2, this), this.dd.set(t2, i2)), i2;
    }
  };
  function oe(t2) {
    if (d(t2)) {
      const i2 = document.getElementById(t2);
      return r(null !== i2, `Cannot find element in DOM with id=${t2}`), i2;
    }
    return t2;
  }
  function _e(t2, i2, n2) {
    const s2 = oe(t2), e2 = new le(s2, i2, n2);
    return i2.setOptions(e2.options()), e2;
  }
  function ue(t2, i2) {
    return _e(t2, new ln(), ln.Tf(i2));
  }
  function de(t2, i2, n2, s2) {
    return Math.hypot(n2 - t2, s2 - i2);
  }
  function fe(t2, i2, n2, s2, e2, r2, h2, a2 = 0) {
    if (0 === i2.length || s2.from >= i2.length || s2.to <= 0) return;
    const { context: l2, horizontalPixelRatio: o2, verticalPixelRatio: _2 } = t2, u2 = i2[s2.from];
    let c2 = r2(t2, u2), d2 = u2;
    if (s2.to - s2.from < 2) {
      const i3 = e2 / 2;
      l2.beginPath();
      const n3 = { _t: u2._t - i3, ut: u2.ut }, s3 = { _t: u2._t + i3, ut: u2.ut };
      l2.moveTo(n3._t * o2, n3.ut * _2), l2.lineTo(s3._t * o2, s3.ut * _2), h2(t2, c2, n3, s3);
    } else {
      const e3 = a2 > 0;
      let f2 = 0;
      const p2 = (i3, n3) => {
        if (h2(t2, c2, d2, n3), l2.beginPath(), c2 = i3, d2 = n3, e3) {
          const t3 = f2 % a2;
          l2.lineDashOffset = t3, f2 = t3;
        }
      };
      let v2 = d2;
      l2.beginPath(), l2.moveTo(u2._t * o2, u2.ut * _2);
      for (let h3 = s2.from + 1; h3 < s2.to; ++h3) {
        v2 = i2[h3];
        const s3 = v2._t * o2, a3 = v2.ut * _2, u3 = r2(t2, v2);
        switch (n2) {
          case 0:
            if (l2.lineTo(s3, a3), e3) {
              const t3 = i2[h3 - 1], n3 = t3._t * o2, e4 = t3.ut * _2;
              f2 += de(n3, e4, s3, a3);
            }
            break;
          case 1: {
            const t3 = i2[h3 - 1], n3 = t3.ut * _2;
            l2.lineTo(s3, n3), e3 && (f2 += Math.abs(v2._t - t3._t) * o2), u3 !== c2 && (p2(u3, v2), l2.lineTo(s3, n3)), l2.lineTo(s3, a3), e3 && (f2 += Math.abs(v2.ut - t3.ut) * _2);
            break;
          }
          case 2: {
            const [t3, n3] = we(i2, h3 - 1, h3), r3 = t3._t * o2, u4 = t3.ut * _2, c3 = n3._t * o2, d3 = n3.ut * _2;
            if (l2.bezierCurveTo(r3, u4, c3, d3, s3, a3), e3) {
              const t4 = i2[h3 - 1], n4 = t4._t * o2, e4 = t4.ut * _2, l3 = de(n4, e4, s3, a3), p3 = de(n4, e4, r3, u4) + de(r3, u4, c3, d3) + de(c3, d3, s3, a3);
              f2 += (l3 + p3) / 2;
            }
            break;
          }
        }
        1 !== n2 && u3 !== c2 && (p2(u3, v2), l2.moveTo(s3, a3));
      }
      (d2 !== v2 || d2 === v2 && 1 === n2) && h2(t2, c2, d2, v2), e3 && (l2.lineDashOffset = 0);
    }
  }
  var pe = 6;
  function ve(t2, i2) {
    return { _t: t2._t - i2._t, ut: t2.ut - i2.ut };
  }
  function me(t2, i2) {
    return { _t: t2._t / i2, ut: t2.ut / i2 };
  }
  function we(t2, i2, n2) {
    const s2 = Math.max(0, i2 - 1), e2 = Math.min(t2.length - 1, n2 + 1);
    var r2, h2;
    return [(r2 = t2[i2], h2 = me(ve(t2[n2], t2[s2]), pe), { _t: r2._t + h2._t, ut: r2.ut + h2.ut }), ve(t2[n2], me(ve(t2[e2], t2[i2]), pe))];
  }
  function ge(t2, i2) {
    const n2 = t2.context;
    n2.strokeStyle = i2, n2.stroke();
  }
  var Me = class extends y {
    constructor() {
      super(...arguments), this.rt = null;
    }
    ht(t2) {
      this.rt = t2;
    }
    et(t2) {
      if (null === this.rt) return;
      const { ot: i2, lt: n2, Cb: e2, yb: r2, ct: h2, Gt: a2, Pb: l2 } = this.rt;
      if (null === n2) return;
      const o2 = t2.context;
      o2.lineCap = "butt", o2.lineWidth = h2 * t2.verticalPixelRatio;
      const _2 = s(o2, a2);
      o2.lineJoin = "round";
      const u2 = this.kb.bind(this), c2 = (function(t3) {
        return t3.reduce(((t4, i3) => t4 + i3), 0);
      })(_2);
      void 0 !== r2 && fe(t2, i2, r2, n2, e2, u2, ge, c2), l2 && (function(t3, i3, n3, s2, e3) {
        if (s2.to - s2.from <= 0) return;
        const { horizontalPixelRatio: r3, verticalPixelRatio: h3, context: a3 } = t3;
        let l3 = null;
        const o3 = Math.max(1, Math.floor(r3)) % 2 / 2, _3 = n3 * h3 + o3;
        for (let n4 = s2.to - 1; n4 >= s2.from; --n4) {
          const s3 = i3[n4];
          if (s3) {
            const i4 = e3(t3, s3);
            i4 !== l3 && (null !== l3 && a3.fill(), a3.beginPath(), a3.fillStyle = i4, l3 = i4);
            const n5 = Math.round(s3._t * r3) + o3, u3 = s3.ut * h3;
            a3.moveTo(n5, u3), a3.arc(n5, u3, _3, 0, 2 * Math.PI);
          }
        }
        a3.fill();
      })(t2, i2, l2, n2, u2);
    }
  };
  var be = class extends Me {
    kb(t2, i2) {
      return i2.vt;
    }
  };
  function Se(t2, i2, n2, s2, e2) {
    const r2 = 1 - e2;
    return r2 * r2 * r2 * t2 + 3 * r2 * r2 * e2 * i2 + 3 * r2 * e2 * e2 * n2 + e2 * e2 * e2 * s2;
  }
  function xe(t2, i2, n2, s2, e2) {
    if (2 === n2) {
      const [n3, r2] = we(s2, e2 - 1, e2);
      return [Math.min(t2._t, i2._t, n3._t, r2._t), Math.max(t2._t, i2._t, n3._t, r2._t)];
    }
    return [Math.min(t2._t, i2._t), Math.max(t2._t, i2._t)];
  }
  function Ce(t2, i2, n2, s2, e2, r2, h2, a2) {
    switch (e2) {
      case 1: {
        const e3 = ys(t2, i2, n2._t, n2.ut, s2._t, n2.ut), r3 = ys(t2, i2, s2._t, n2.ut, s2._t, s2.ut), h3 = Math.min(e3, r3);
        return h3 <= a2 ? h3 : null;
      }
      case 2: {
        const [e3, l2] = we(r2, h2 - 1, h2), o2 = (function(t3, i3, n3) {
          let s3 = Number.POSITIVE_INFINITY, e4 = n3[0];
          for (let r3 = 1; r3 <= 12; r3++) {
            const h3 = r3 / 12, a3 = { _t: Se(n3[0]._t, n3[1]._t, n3[2]._t, n3[3]._t, h3), ut: Se(n3[0].ut, n3[1].ut, n3[2].ut, n3[3].ut, h3) };
            s3 = Math.min(s3, ys(t3, i3, e4._t, e4.ut, a3._t, a3.ut)), e4 = a3;
          }
          return s3;
        })(t2, i2, [n2, e3, l2, s2]);
        return o2 <= a2 ? o2 : null;
      }
      default: {
        const e3 = ys(t2, i2, n2._t, n2.ut, s2._t, s2.ut);
        return e3 <= a2 ? e3 : null;
      }
    }
  }
  var ye = class extends Es {
    constructor(t2, i2) {
      super(t2, i2, true);
    }
    OM(t2, i2, n2) {
      i2.Ic(this.kM, m(this.TM)), t2.Jo(this.kM, n2, m(this.TM));
    }
    Tb(t2, i2) {
      return { wt: t2, gt: i2, _t: NaN, ut: NaN };
    }
    zM() {
      const t2 = this.ae.Sa();
      this.kM = this.ae.Ua().Eh().map(((i2) => {
        let n2;
        if ((i2.Gr ?? 1) > 1) {
          const t3 = i2.Wt[1], s2 = i2.Wt[2], e2 = i2.Wt[3];
          n2 = Math.abs(t3 - e2) > Math.abs(s2 - e2) ? t3 : s2;
        } else n2 = i2.Wt[3];
        return this.Rb(i2.$n, n2, t2);
      }));
    }
  };
  var Pe = class extends ye {
    EM(t2, i2) {
      const n2 = this.ae.N();
      return (function(t3, i3, n3, s2, e2, r2, h2, a2 = 0, l2 = 0) {
        if (null === i3 || i3.from >= i3.to || 0 === t3.length) return null;
        const o2 = Math.max(r2 / 2, h2 ?? 0) + l2;
        let _2 = Number.POSITIVE_INFINITY;
        if (void 0 !== h2) {
          const e3 = h2 + l2, r3 = bs(t3, n3 - e3, i3.from, i3.to), a3 = Ss(t3, n3 + e3, r3, i3.to);
          for (let i4 = r3; i4 < a3; i4++) {
            const e4 = t3[i4];
            if (!Cs(n3, e4._t, e4._t, h2 + l2)) continue;
            const r4 = Math.hypot(n3 - e4._t, s2 - e4.ut);
            r4 <= h2 + l2 && (_2 = Math.min(_2, r4));
          }
        }
        if (i3.to - i3.from < 2) {
          const e3 = t3[i3.from], r3 = Math.max(a2 / 2, o2), h3 = ys(n3, s2, e3._t - r3, e3.ut, e3._t + r3, e3.ut);
          return h3 <= o2 && (_2 = Math.min(_2, h3)), Number.isFinite(_2) ? xs(_2, 2, "series-point") : null;
        }
        let u2 = Number.POSITIVE_INFINITY;
        const c2 = bs(t3, n3 - o2, i3.from, i3.to), d2 = Ss(t3, n3 + o2, c2, i3.to), f2 = Math.max(i3.from + 1, c2), p2 = Math.min(i3.to, d2 + 1);
        for (let i4 = f2; i4 < p2; i4++) {
          const r3 = t3[i4 - 1], h3 = t3[i4], [a3, l3] = xe(r3, h3, e2, t3, i4);
          if (!Cs(n3, a3, l3, o2)) continue;
          const _3 = Ce(n3, s2, r3, h3, e2, t3, i4, o2);
          null !== _3 && (u2 = Math.min(u2, _3));
        }
        return Number.isFinite(_2) ? xs(_2, 2, "series-point") : Number.isFinite(u2) ? xs(u2, 1, "series-line") : null;
      })(this.kM, this.TM, t2, i2, n2.lineType, n2.lineVisible ? n2.lineWidth : 1, n2.pointMarkersVisible ? n2.pointMarkersRadius || n2.lineWidth / 2 + 2 : void 0, this.le.Bt().ml(), n2.hitTestTolerance);
    }
  };
  var ke = class extends Pe {
    constructor() {
      super(...arguments), this.VM = new be();
    }
    Rb(t2, i2, n2) {
      return { ...this.Tb(t2, i2), ...n2.Sh(t2) };
    }
    NM() {
      const t2 = this.ae.N(), i2 = { ot: this.kM, Gt: t2.lineStyle, yb: t2.lineVisible ? t2.lineType : void 0, ct: t2.lineWidth, Pb: t2.pointMarkersVisible ? t2.pointMarkersRadius || t2.lineWidth / 2 + 2 : void 0, lt: this.TM, Cb: this.le.Bt().ml() };
      this.VM.ht(i2);
    }
  };
  var Te = { type: "Line", isBuiltIn: true, defaultOptions: { color: "#2196f3", lineStyle: 0, lineWidth: 3, lineType: 0, lineVisible: true, crosshairMarkerVisible: true, crosshairMarkerRadius: 4, crosshairMarkerBorderColor: "", crosshairMarkerBorderWidth: 2, crosshairMarkerBackgroundColor: "", lastPriceAnimation: 0, pointMarkersVisible: false }, mb: (t2, i2) => new ke(t2, i2) };
  var Xe = class extends Es {
    constructor(t2, i2) {
      super(t2, i2, false);
    }
    EM(t2, i2) {
      return Rs(this.kM, this.TM, t2, i2, this.le.Bt().ml(), this.ae.N().hitTestTolerance, ((t3, i3) => {
        i3[0] = t3.n_, i3[1] = t3.s_;
      }));
    }
    OM(t2, i2, n2) {
      i2.Ic(this.kM, m(this.TM)), t2.t_(this.kM, n2, m(this.TM));
    }
    fS(t2, i2, n2) {
      return { wt: t2, jr: i2.Wt[0], qr: i2.Wt[1], Yr: i2.Wt[2], Kr: i2.Wt[3], _t: NaN, i_: NaN, n_: NaN, s_: NaN, e_: NaN };
    }
    zM() {
      const t2 = this.ae.Sa();
      this.kM = this.ae.Ua().Eh().map(((i2) => this.Rb(i2.$n, i2, t2)));
    }
  };
  var tr = class extends y {
    constructor() {
      super(...arguments), this.qt = null, this.oS = 0;
    }
    ht(t2) {
      this.qt = t2;
    }
    et(t2) {
      if (null === this.qt || 0 === this.qt.Un.length || null === this.qt.lt) return;
      const { horizontalPixelRatio: i2 } = t2;
      if (this.oS = (function(t3, i3) {
        if (t3 >= 2.5 && t3 <= 4) return Math.floor(3 * i3);
        const n3 = 1 - 0.2 * Math.atan(Math.max(4, t3) - 4) / (0.5 * Math.PI), s3 = Math.floor(t3 * n3 * i3), e2 = Math.floor(t3 * i3), r2 = Math.min(s3, e2);
        return Math.max(Math.floor(i3), r2);
      })(this.qt.ml, i2), this.oS >= 2) {
        Math.floor(i2) % 2 != this.oS % 2 && this.oS--;
      }
      const n2 = this.qt.Un;
      this.qt.pS && this.vS(t2, n2, this.qt.lt), this.qt.Mi && this.Vm(t2, n2, this.qt.lt);
      const s2 = this.mS(i2);
      (!this.qt.Mi || this.oS > 2 * s2) && this.wS(t2, n2, this.qt.lt);
    }
    vS(t2, i2, n2) {
      if (null === this.qt) return;
      const { context: s2, horizontalPixelRatio: e2, verticalPixelRatio: r2 } = t2;
      let h2 = "", a2 = Math.min(Math.floor(e2), Math.floor(this.qt.ml * e2));
      a2 = Math.max(Math.floor(e2), Math.min(a2, this.oS));
      const l2 = Math.floor(0.5 * a2);
      let o2 = null;
      for (let t3 = n2.from; t3 < n2.to; t3++) {
        const n3 = i2[t3];
        n3.rh !== h2 && (s2.fillStyle = n3.rh, h2 = n3.rh);
        const _2 = Math.round(Math.min(n3.i_, n3.e_) * r2), u2 = Math.round(Math.max(n3.i_, n3.e_) * r2), c2 = Math.round(n3.n_ * r2), d2 = Math.round(n3.s_ * r2);
        let f2 = Math.round(e2 * n3._t) - l2;
        const p2 = f2 + a2 - 1;
        null !== o2 && (f2 = Math.max(o2 + 1, f2), f2 = Math.min(f2, p2));
        const v2 = p2 - f2 + 1;
        s2.fillRect(f2, c2, v2, _2 - c2), s2.fillRect(f2, u2 + 1, v2, d2 - u2), o2 = p2;
      }
    }
    mS(t2) {
      let i2 = Math.floor(1 * t2);
      this.oS <= 2 * i2 && (i2 = Math.floor(0.5 * (this.oS - 1)));
      const n2 = Math.max(Math.floor(t2), i2);
      return this.oS <= 2 * n2 ? Math.max(Math.floor(t2), Math.floor(1 * t2)) : n2;
    }
    Vm(t2, i2, n2) {
      if (null === this.qt) return;
      const { context: s2, horizontalPixelRatio: e2, verticalPixelRatio: r2 } = t2;
      let h2 = "";
      const a2 = this.mS(e2);
      let l2 = null;
      for (let t3 = n2.from; t3 < n2.to; t3++) {
        const n3 = i2[t3];
        n3.eh !== h2 && (s2.fillStyle = n3.eh, h2 = n3.eh);
        let o2 = Math.round(n3._t * e2) - Math.floor(0.5 * this.oS);
        const _2 = o2 + this.oS - 1, u2 = Math.round(Math.min(n3.i_, n3.e_) * r2), c2 = Math.round(Math.max(n3.i_, n3.e_) * r2);
        if (null !== l2 && (o2 = Math.max(l2 + 1, o2), o2 = Math.min(o2, _2)), this.qt.ml * e2 > 2 * a2) V(s2, o2, u2, _2 - o2 + 1, c2 - u2 + 1, a2);
        else {
          const t4 = _2 - o2 + 1;
          s2.fillRect(o2, u2, t4, c2 - u2 + 1);
        }
        l2 = _2;
      }
    }
    wS(t2, i2, n2) {
      if (null === this.qt) return;
      const { context: s2, horizontalPixelRatio: e2, verticalPixelRatio: r2 } = t2;
      let h2 = "";
      const a2 = this.mS(e2);
      for (let t3 = n2.from; t3 < n2.to; t3++) {
        const n3 = i2[t3];
        let l2 = Math.round(Math.min(n3.i_, n3.e_) * r2), o2 = Math.round(Math.max(n3.i_, n3.e_) * r2), _2 = Math.round(n3._t * e2) - Math.floor(0.5 * this.oS), u2 = _2 + this.oS - 1;
        if (n3.sh !== h2) {
          const t4 = n3.sh;
          s2.fillStyle = t4, h2 = t4;
        }
        this.qt.Mi && (_2 += a2, l2 += a2, u2 -= a2, o2 -= a2), l2 > o2 || s2.fillRect(_2, l2, u2 - _2 + 1, o2 - l2 + 1);
      }
    }
  };
  var ir = class extends Xe {
    constructor() {
      super(...arguments), this.VM = new tr();
    }
    Rb(t2, i2, n2) {
      return { ...this.fS(t2, i2, n2), ...n2.Sh(t2) };
    }
    NM() {
      const t2 = this.ae.N();
      this.VM.ht({ Un: this.kM, ml: this.le.Bt().ml(), pS: t2.wickVisible, Mi: t2.borderVisible, lt: this.TM });
    }
  };
  var nr = { type: "Candlestick", isBuiltIn: true, defaultOptions: { upColor: "#26a69a", downColor: "#ef5350", wickVisible: true, borderVisible: true, borderColor: "#378658", borderUpColor: "#26a69a", borderDownColor: "#ef5350", wickColor: "#737375", wickUpColor: "#26a69a", wickDownColor: "#ef5350" }, mb: (t2, i2) => new ir(t2, i2) };
  var Cr = class {
    constructor(t2, i2) {
      this.ae = t2, this.Jh = i2, this.CS();
    }
    detach() {
      this.ae.detachPrimitive(this.Jh);
    }
    getSeries() {
      return this.ae;
    }
    applyOptions(t2) {
      this.Jh && this.Jh.vr && this.Jh.vr(t2);
    }
    CS() {
      this.ae.attachPrimitive(this.Jh);
    }
  };
  var yr = { autoScale: true, zOrder: "normal" };
  function Pr(t2, i2) {
    return ti(Math.min(Math.max(t2, 12), 30) * i2);
  }
  function kr(t2, i2) {
    const n2 = "circle" === t2 ? 0.8 : "square" === t2 ? 0.7 : 1;
    return ti(Math.max(i2, 12) * n2);
  }
  function Tr(t2) {
    return (function(t3) {
      const i2 = Math.ceil(t3);
      return i2 % 2 != 0 ? i2 - 1 : i2;
    })(Pr(t2, 1));
  }
  function Rr(t2) {
    return Math.max(Pr(t2, 0.1), 3);
  }
  function Dr(t2, i2, n2) {
    return i2 ? t2 : n2 ? Math.ceil(t2 / 2) : 0;
  }
  function Ir(t2, i2, n2, s2) {
    const e2 = (kr("arrowUp", s2) - 1) / 2 * n2.YS, r2 = (ti(s2 / 2) - 1) / 2 * n2.YS;
    i2.beginPath(), t2 ? (i2.moveTo(n2._t - e2, n2.ut), i2.lineTo(n2._t, n2.ut - e2), i2.lineTo(n2._t + e2, n2.ut), i2.lineTo(n2._t + r2, n2.ut), i2.lineTo(n2._t + r2, n2.ut + e2), i2.lineTo(n2._t - r2, n2.ut + e2), i2.lineTo(n2._t - r2, n2.ut)) : (i2.moveTo(n2._t - e2, n2.ut), i2.lineTo(n2._t, n2.ut + e2), i2.lineTo(n2._t + e2, n2.ut), i2.lineTo(n2._t + r2, n2.ut), i2.lineTo(n2._t + r2, n2.ut - e2), i2.lineTo(n2._t - r2, n2.ut - e2), i2.lineTo(n2._t - r2, n2.ut)), i2.fill();
  }
  function Vr(t2, i2, n2, s2, e2, r2) {
    const h2 = (kr("arrowUp", s2) - 1) / 2, a2 = (ti(s2 / 2) - 1) / 2;
    if (e2 >= i2 - a2 - 2 && e2 <= i2 + a2 + 2 && r2 >= (t2 ? n2 : n2 - h2) - 2 && r2 <= (t2 ? n2 + h2 : n2) + 2) return true;
    return (() => {
      if (e2 < i2 - h2 - 3 || e2 > i2 + h2 + 3 || r2 < (t2 ? n2 - h2 - 3 : n2) || r2 > (t2 ? n2 : n2 + h2 + 3)) return false;
      const s3 = Math.abs(e2 - i2);
      return Math.abs(r2 - n2) + 3 >= s3 / 2;
    })();
  }
  var Er = class {
    constructor() {
      this.qt = null, this.$s = new it(), this.F = -1, this.W = "", this.hm = "", this.KS = "normal";
    }
    ht(t2) {
      this.qt = t2;
    }
    js(t2, i2, n2) {
      this.F === t2 && this.W === i2 || (this.F = t2, this.W = i2, this.hm = g(t2, i2), this.$s.Os()), this.KS = n2;
    }
    Qs(t2, i2) {
      if (null === this.qt || null === this.qt.lt) return null;
      for (let n2 = this.qt.lt.from; n2 < this.qt.lt.to; n2++) {
        const s2 = this.qt.ot[n2];
        if (s2 && Ar(s2, t2, i2)) return { zOrder: "normal", externalId: s2.te ?? "", itemType: "marker" };
      }
      return null;
    }
    draw(t2) {
      "aboveSeries" !== this.KS && t2.useBitmapCoordinateSpace(((t3) => {
        this.et(t3);
      }));
    }
    drawBackground(t2) {
      "aboveSeries" === this.KS && t2.useBitmapCoordinateSpace(((t3) => {
        this.et(t3);
      }));
    }
    et({ context: t2, horizontalPixelRatio: i2, verticalPixelRatio: n2 }) {
      if (null !== this.qt && null !== this.qt.lt) {
        t2.textBaseline = "middle", t2.font = this.hm;
        for (let s2 = this.qt.lt.from; s2 < this.qt.lt.to; s2++) {
          const e2 = this.qt.ot[s2];
          void 0 !== e2.ri && (e2.ri.nn = this.$s.Ii(t2, e2.ri.GS), e2.ri.$t = this.F, e2.ri._t = e2._t - e2.ri.nn / 2), Br(e2, t2, i2, n2);
        }
      }
    }
  };
  function Br(t2, i2, n2, s2) {
    i2.fillStyle = t2.R, void 0 !== t2.ri && (function(t3, i3, n3, s3, e2, r2) {
      t3.save(), t3.scale(e2, r2), t3.fillText(i3, n3, s3), t3.restore();
    })(i2, t2.ri.GS, t2.ri._t, t2.ri.ut, n2, s2), (function(t3, i3, n3) {
      if (0 === t3.Th) return;
      switch (t3.ZS) {
        case "arrowDown":
          return void Ir(false, i3, n3, t3.Th);
        case "arrowUp":
          return void Ir(true, i3, n3, t3.Th);
        case "circle":
          return void (function(t4, i4, n4) {
            const s3 = (kr("circle", n4) - 1) / 2;
            t4.beginPath(), t4.arc(i4._t, i4.ut, s3 * i4.YS, 0, 2 * Math.PI, false), t4.fill();
          })(i3, n3, t3.Th);
        case "square":
          return void (function(t4, i4, n4) {
            const s3 = kr("square", n4), e2 = (s3 - 1) * i4.YS / 2, r2 = i4._t - e2, h2 = i4.ut - e2;
            t4.fillRect(r2, h2, s3 * i4.YS, s3 * i4.YS);
          })(i3, n3, t3.Th);
      }
      t3.ZS;
    })(t2, i2, (function(t3, i3, n3) {
      const s3 = Math.max(1, Math.floor(i3)) % 2 / 2;
      return { _t: Math.round(t3._t * i3) + s3, ut: t3.ut * n3, YS: i3 };
    })(t2, n2, s2));
  }
  function Ar(t2, i2, n2) {
    return !(void 0 === t2.ri || !(function(t3, i3, n3, s2, e2, r2) {
      const h2 = s2 / 2;
      return e2 >= t3 && e2 <= t3 + n3 && r2 >= i3 - h2 && r2 <= i3 + h2;
    })(t2.ri._t, t2.ri.ut, t2.ri.nn, t2.ri.$t, i2, n2)) || (function(t3, i3, n3) {
      if (0 === t3.Th) return false;
      switch (t3.ZS) {
        case "arrowDown":
          return Vr(true, t3._t, t3.ut, t3.Th, i3, n3);
        case "arrowUp":
          return Vr(false, t3._t, t3.ut, t3.Th, i3, n3);
        case "circle":
          return (function(t4, i4, n4, s2, e2) {
            const r2 = 2 + kr("circle", n4) / 2, h2 = t4 - s2, a2 = i4 - e2;
            return Math.sqrt(h2 * h2 + a2 * a2) <= r2;
          })(t3._t, t3.ut, t3.Th, i3, n3);
        case "square":
          return (function(t4, i4, n4, s2, e2) {
            const r2 = kr("square", n4), h2 = (r2 - 1) / 2, a2 = t4 - h2, l2 = i4 - h2;
            return s2 >= a2 && s2 <= a2 + r2 && e2 >= l2 && e2 <= l2 + r2;
          })(t3._t, t3.ut, t3.Th, i3, n3);
      }
    })(t2, i2, n2);
  }
  function zr(t2) {
    return "atPriceTop" === t2 || "atPriceBottom" === t2 || "atPriceMiddle" === t2;
  }
  function Lr(t2, i2, n2, s2, e2, r2, h2, l2) {
    const o2 = (function(t3, i3, n3) {
      if (zr(i3.position) && void 0 !== i3.price) return i3.price;
      if ("value" in (s3 = t3) && "number" == typeof s3.value) return t3.value;
      var s3;
      if ((function(t4) {
        return "open" in t4 && "high" in t4 && "low" in t4 && "close" in t4;
      })(t3)) {
        if ("inBar" === i3.position) return t3.close;
        if ("aboveBar" === i3.position) return n3 ? t3.low : t3.high;
        if ("belowBar" === i3.position) return n3 ? t3.high : t3.low;
      }
    })(n2, i2, h2.priceScale().options().invertScale);
    if (void 0 === o2) return;
    const _2 = zr(i2.position), c2 = l2.timeScale(), d2 = u(i2.size) ? Math.max(i2.size, 0) : 1, f2 = Tr(c2.options().barSpacing) * d2, p2 = f2 / 2;
    t2.Th = f2;
    switch (i2.position) {
      case "inBar":
      case "atPriceMiddle":
        return t2.ut = a(h2.priceToCoordinate(o2)), void (void 0 !== t2.ri && (t2.ri.ut = t2.ut + p2 + r2 + 0.6 * e2));
      case "aboveBar":
      case "atPriceTop": {
        const i3 = _2 ? 0 : s2.XS;
        return t2.ut = a(h2.priceToCoordinate(o2)) - p2 - i3, void 0 !== t2.ri && (t2.ri.ut = t2.ut - p2 - 0.6 * e2, s2.XS += 1.2 * e2), void (_2 || (s2.XS += f2 + r2));
      }
      case "belowBar":
      case "atPriceBottom": {
        const i3 = _2 ? 0 : s2.JS;
        return t2.ut = a(h2.priceToCoordinate(o2)) + p2 + i3, void 0 !== t2.ri && (t2.ri.ut = t2.ut + p2 + r2 + 0.6 * e2, s2.JS += 1.2 * e2), void (_2 || (s2.JS += f2 + r2));
      }
    }
  }
  var Or = class {
    constructor(t2, i2, n2) {
      this.QS = [], this.xt = true, this.tx = true, this.Xt = new Er(), this.Te = t2, this.Gv = i2, this.qt = { ot: [], lt: null }, this.yn = n2;
    }
    renderer() {
      if (!this.Te.options().visible) return null;
      this.xt && this.IM();
      const t2 = this.Gv.options().layout;
      return this.Xt.js(t2.fontSize, t2.fontFamily, this.yn.zOrder), this.Xt.ht(this.qt), this.Xt;
    }
    ix(t2) {
      this.QS = t2, this.Pt("data");
    }
    Pt(t2) {
      this.xt = true, "data" === t2 && (this.tx = true);
    }
    nx(t2) {
      this.xt = true, this.yn = t2;
    }
    zOrder() {
      return "aboveSeries" === this.yn.zOrder ? "top" : this.yn.zOrder;
    }
    IM() {
      const t2 = this.Gv.timeScale(), i2 = this.QS;
      this.tx && (this.qt.ot = i2.map(((t3) => ({ wt: t3.time, _t: 0, ut: 0, Th: 0, ZS: t3.shape, R: t3.color, te: t3.id, sx: t3.sx, ri: void 0 }))), this.tx = false);
      const n2 = this.Gv.options().layout;
      this.qt.lt = null;
      const s2 = t2.getVisibleLogicalRange();
      if (null === s2) return;
      const e2 = new Ii(Math.floor(s2.from), Math.ceil(s2.to));
      if (null === this.Te.dataByIndex(0, 1)) return;
      if (0 === this.qt.ot.length) return;
      let r2 = NaN;
      const h2 = Rr(t2.options().barSpacing), l2 = { XS: h2, JS: h2 };
      this.qt.lt = Vs(this.qt.ot, e2, true);
      for (let s3 = this.qt.lt.from; s3 < this.qt.lt.to; s3++) {
        const e3 = i2[s3];
        e3.time !== r2 && (l2.XS = h2, l2.JS = h2, r2 = e3.time);
        const o2 = this.qt.ot[s3];
        o2._t = a(t2.logicalToCoordinate(e3.time)), void 0 !== e3.text && e3.text.length > 0 && (o2.ri = { GS: e3.text, _t: 0, ut: 0, nn: 0, $t: 0 });
        const _2 = this.Te.dataByIndex(e3.time, 0);
        null !== _2 && Lr(o2, e3, _2, l2, n2.fontSize, h2, this.Te, this.Gv);
      }
      this.xt = false;
    }
  };
  function Nr(t2) {
    return { ...yr, ...t2 };
  }
  var Fr = class {
    constructor(t2) {
      this.Yh = null, this.QS = [], this.hx = [], this.lx = null, this.Te = null, this.Gv = null, this.ox = true, this._x = null, this.ux = null, this.vx = null, this.mx = true, this.yn = Nr(t2);
    }
    attached(t2) {
      this.wx(), this.Gv = t2.chart, this.Te = t2.series, this.Yh = new Or(this.Te, a(this.Gv), this.yn), this.jS = t2.requestUpdate, this.Te.subscribeDataChanged(((t3) => this.QM(t3))), this.mx = true, this.DS();
    }
    DS() {
      this.jS && this.jS();
    }
    detached() {
      this.Te && this.lx && this.Te.unsubscribeDataChanged(this.lx), this.Gv = null, this.Te = null, this.Yh = null, this.lx = null;
    }
    ix(t2) {
      this.mx = true, this.QS = t2, this.wx(), this.ox = true, this.ux = null, this.DS();
    }
    gx() {
      return this.QS;
    }
    paneViews() {
      return this.Yh ? [this.Yh] : [];
    }
    updateAllViews() {
      this.Mx();
    }
    hitTest(t2, i2) {
      return this.Yh ? this.Yh.renderer()?.Qs(t2, i2) ?? null : null;
    }
    autoscaleInfo(t2, i2) {
      if (this.yn.autoScale && this.Yh) {
        const t3 = this.bx();
        if (t3) return { priceRange: null, margins: t3 };
      }
      return null;
    }
    vr(t2) {
      this.yn = Nr({ ...this.yn, ...t2 }), this.DS && this.DS();
    }
    bx() {
      const t2 = a(this.Gv).timeScale().options().barSpacing;
      if (this.ox || t2 !== this.vx) {
        if (this.vx = t2, this.QS.length > 0) {
          const i2 = Rr(t2), n2 = 1.5 * Tr(t2) + 2 * i2, s2 = this.Sx();
          this._x = { above: Dr(n2, s2.aboveBar, s2.inBar), below: Dr(n2, s2.belowBar, s2.inBar) };
        } else this._x = null;
        this.ox = false;
      }
      return this._x;
    }
    Sx() {
      return null === this.ux && (this.ux = this.QS.reduce(((t2, i2) => (t2[i2.position] || (t2[i2.position] = true), t2)), { inBar: false, aboveBar: false, belowBar: false, atPriceTop: false, atPriceBottom: false, atPriceMiddle: false })), this.ux;
    }
    wx() {
      if (!this.mx || !this.Gv || !this.Te) return;
      const t2 = this.Gv.timeScale(), i2 = this.Te?.data();
      if (null == t2.getVisibleLogicalRange() || !this.Te || 0 === i2.length) return void (this.hx = []);
      const n2 = t2.timeToIndex(a(i2[0].time), true);
      this.hx = this.QS.map(((i3, s2) => {
        const e2 = t2.timeToIndex(i3.time, true), r2 = e2 < n2 ? 1 : -1, h2 = a(this.Te).dataByIndex(e2, r2), l2 = { time: t2.timeToIndex(a(h2).time, false), position: i3.position, shape: i3.shape, color: i3.color, id: i3.id, sx: s2, text: i3.text, size: i3.size, price: i3.price, Qr: i3.time };
        if ("atPriceTop" === i3.position || "atPriceBottom" === i3.position || "atPriceMiddle" === i3.position) {
          if (void 0 === i3.price) throw new Error(`Price is required for position ${i3.position}`);
          return { ...l2, position: i3.position, price: i3.price };
        }
        return { ...l2, position: i3.position, price: i3.price };
      })), this.mx = false;
    }
    Mx(t2) {
      this.Yh && (this.wx(), this.Yh.ix(this.hx), this.Yh.nx(this.yn), this.Yh.Pt(t2));
    }
    QM(t2) {
      this.mx = true, this.DS();
    }
  };
  var Wr = class extends Cr {
    constructor(t2, i2, n2) {
      super(t2, i2), n2 && this.setMarkers(n2);
    }
    setMarkers(t2) {
      this.Jh.ix(t2);
    }
    markers() {
      return this.Jh.gx();
    }
  };
  function Hr(t2, i2, n2) {
    const s2 = new Wr(t2, new Fr(n2 ?? {}));
    return i2 && s2.setMarkers(i2), s2;
  }
  var Jr = { ...t, color: "#2196f3" };
  function Qr() {
    return "5.2.1";
  }

  // frontend/src/lib/format.js
  function fmtPrice(p2) {
    const n2 = Number(p2);
    if (!isFinite(n2)) return String(p2);
    const abs = Math.abs(n2);
    let dp;
    if (abs >= 1e3) dp = 2;
    else if (abs >= 1) dp = 4;
    else if (abs >= 0.01) dp = 5;
    else if (abs >= 1e-4) dp = 6;
    else dp = 8;
    return n2.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: dp });
  }

  // frontend/src/lib/indicators.js
  var UP = "rgb(var(--chart-up))";
  var DOWN = "rgb(var(--chart-down))";
  var MID = "rgb(99 102 241)";
  var BAND = "rgb(148 163 184)";
  var FILL = "rgba(99,102,241,0.08)";
  var FAST = "rgb(234 88 12)";
  var SLOW = "rgb(99 102 241)";
  var NEUTRAL = "rgb(148 163 184)";
  var ENTRY = "rgb(99 102 241)";
  var num = (v2) => Number(v2);
  var ok = (v2) => Number.isFinite(v2);
  function sma(values, period) {
    const out = new Array(values.length).fill(null);
    if (!(period > 0)) return out;
    let sum = 0;
    const q2 = [];
    for (let i2 = 0; i2 < values.length; i2++) {
      q2.push(values[i2]);
      sum += values[i2];
      if (q2.length > period) sum -= q2.shift();
      if (q2.length === period) out[i2] = sum / period;
    }
    return out;
  }
  function ema(values, period) {
    const out = new Array(values.length).fill(null);
    if (!(period > 0)) return out;
    const k2 = 2 / (period + 1);
    let prev = null;
    for (let i2 = 0; i2 < values.length; i2++) {
      if (prev == null) {
        if (i2 >= period - 1) {
          let s2 = 0;
          for (let j2 = i2 - period + 1; j2 <= i2; j2++) s2 += values[j2];
          prev = s2 / period;
          out[i2] = prev;
        }
      } else {
        prev = values[i2] * k2 + prev * (1 - k2);
        out[i2] = prev;
      }
    }
    return out;
  }
  function rollingStd(values, period, means) {
    const out = new Array(values.length).fill(null);
    if (!(period > 0)) return out;
    for (let i2 = period - 1; i2 < values.length; i2++) {
      const m2 = means[i2];
      if (m2 == null) continue;
      let s2 = 0;
      for (let j2 = i2 - period + 1; j2 <= i2; j2++) {
        const d2 = values[j2] - m2;
        s2 += d2 * d2;
      }
      out[i2] = Math.sqrt(s2 / period);
    }
    return out;
  }
  function rsi(values, period) {
    const out = new Array(values.length).fill(null);
    if (!(period > 0) || values.length <= period) return out;
    let gain = 0;
    let loss = 0;
    for (let i2 = 1; i2 < values.length; i2++) {
      const ch = values[i2] - values[i2 - 1];
      const g2 = Math.max(0, ch);
      const l2 = Math.max(0, -ch);
      if (i2 <= period) {
        gain += g2;
        loss += l2;
        if (i2 === period) {
          gain /= period;
          loss /= period;
          out[i2] = loss === 0 ? 100 : 100 - 100 / (1 + gain / loss);
        }
      } else {
        gain = (gain * (period - 1) + g2) / period;
        loss = (loss * (period - 1) + l2) / period;
        out[i2] = loss === 0 ? 100 : 100 - 100 / (1 + gain / loss);
      }
    }
    return out;
  }
  var SIGNAL_KO = {
    long: { entry: "\uB9E4\uC218", exit: "\uB9E4\uB3C4" },
    short: { entry: "\uC20F \uC9C4\uC785", exit: "\uC20F \uCCAD\uC0B0" }
  };
  function signalSide(kind, isShort) {
    return kind === "entry" !== !!isShort ? "buy" : "sell";
  }
  function signalLabel(kind, isShort) {
    return SIGNAL_KO[isShort ? "short" : "long"][kind] || "";
  }
  function signal(index, kind, isShort, cond) {
    const ko = signalLabel(kind, isShort);
    return { index, kind, side: signalSide(kind, isShort), label: cond ? `${cond} \xB7 ${ko}` : ko };
  }
  function signalLegend(kind, isShort) {
    const side = signalSide(kind, isShort);
    return { label: signalLabel(kind, isShort), color: side === "buy" ? UP : DOWN, kind: side };
  }
  function crossSignals(a2, b2, isShort, upCond, downCond) {
    const out = [];
    for (let i2 = 1; i2 < a2.length; i2++) {
      if (a2[i2] == null || b2[i2] == null || a2[i2 - 1] == null || b2[i2 - 1] == null) continue;
      const upCross = a2[i2 - 1] <= b2[i2 - 1] && a2[i2] > b2[i2];
      const downCross = a2[i2 - 1] >= b2[i2 - 1] && a2[i2] < b2[i2];
      if (upCross) out.push(signal(i2, isShort ? "exit" : "entry", isShort, upCond));
      else if (downCross) out.push(signal(i2, isShort ? "entry" : "exit", isShort, downCond));
    }
    return out;
  }
  var priceLine = (price, color, label, dash) => ok(price) && price > 0 ? { price, color, label, dash } : null;
  function entryPriceOverlay(entryPrice, side) {
    const p2 = num(entryPrice);
    if (!ok(p2) || p2 <= 0) return null;
    return {
      legend: [{ label: "\uB0B4 \uD3C9\uB2E8(\uD3C9\uADE0 \uC9C4\uC785\uAC00)", color: ENTRY, dash: "5 3" }],
      priceLines: [{ price: p2, color: ENTRY, dash: "5 3", label: `\uB0B4 \uD3C9\uB2E8 ${fmtPrice(p2)}` }],
      note: side === "short" ? "\uC810\uC120\uC774 \uB0B4 \uD3C9\uB2E8\uC774\uC5D0\uC694. \uAC00\uACA9\uC774 \uD3C9\uB2E8\uBCF4\uB2E4 \uB0B4\uB824\uAC00\uBA74 \uC774\uC775, \uC62C\uB77C\uAC00\uBA74 \uC190\uC2E4\uC774\uC5D0\uC694." : "\uC810\uC120\uC774 \uB0B4 \uD3C9\uB2E8\uC774\uC5D0\uC694. \uAC00\uACA9\uC774 \uD3C9\uB2E8\uBCF4\uB2E4 \uC62C\uB77C\uAC00\uBA74 \uC774\uC775, \uB0B4\uB824\uAC00\uBA74 \uC190\uC2E4\uC774\uC5D0\uC694."
    };
  }
  function flattenMacro(macro, sideFallback) {
    if (!macro || !macro.rule_type) return null;
    const risk = macro.risk || {};
    return {
      rule_type: macro.rule_type,
      position_side: macro.position_side || sideFallback || "long",
      ...macro.params || {},
      use_stop_loss: risk.stop_loss_pct != null,
      stop_loss_pct: risk.stop_loss_pct ?? 0
    };
  }
  function computeSessionOverlay(macro, entryPrice, side, candles2) {
    const form = flattenMacro(macro, side);
    const base = form ? computeStrategyOverlay(form, candles2) : null;
    const entry = entryPriceOverlay(entryPrice, side || macro?.position_side);
    if (!base && !entry) return null;
    return {
      legend: [...base?.legend || [], ...entry?.legend || []],
      priceLines: [...base?.priceLines || [], ...entry?.priceLines || []],
      series: base?.series || [],
      bands: base?.bands || [],
      markers: base?.markers || [],
      rsi: base?.rsi || null,
      note: [base?.note, entry?.note].filter(Boolean).join(" ")
    };
  }
  function computeStrategyOverlay(form, candles2) {
    if (!form || !candles2 || candles2.length === 0) return null;
    const closes = candles2.map((k2) => k2.c);
    const last = closes[closes.length - 1];
    const rt2 = form.rule_type;
    const isShort = form.position_side === "short";
    const buy = { color: UP };
    const sell = { color: DOWN };
    const emptyLines = [];
    const push = (arr, line) => {
      if (line) arr.push(line);
    };
    switch (rt2) {
      case "A": {
        const tp = num(form.take_profit_pct);
        const sl = num(form.stop_loss_pct);
        const lines = [];
        push(lines, priceLine(last, NEUTRAL, `\uD604\uC7AC\uAC00 ${fmtPrice(last)}`, "2 3"));
        if (tp > 0) {
          const tpPrice = isShort ? last * (1 - tp / 100) : last * (1 + tp / 100);
          push(lines, priceLine(tpPrice, UP, `\uC775\uC808 ${isShort ? "-" : "+"}${tp}%`, "5 3"));
        }
        if (form.use_stop_loss && sl > 0) {
          const slPrice = isShort ? last * (1 + sl / 100) : last * (1 - sl / 100);
          push(lines, priceLine(slPrice, DOWN, `\uC190\uC808 ${isShort ? "+" : "-"}${sl}%`, "5 3"));
        }
        return {
          legend: [
            { label: "\uC775\uC808\uC120", color: UP },
            { label: "\uC190\uC808\uC120", color: DOWN }
          ],
          priceLines: lines,
          note: "\uC9C0\uAE08 \uAC00\uACA9\uC5D0 \uB4E4\uC5B4\uAC04\uB2E4\uACE0 \uAC00\uC815\uD55C \uC775\uC808\xB7\uC190\uC808\uC120\uC774\uC5D0\uC694. \uC2E4\uC81C \uC9C4\uC785\uAC00\uC5D0 \uB530\uB77C \uC704\uC544\uB798\uB85C \uD568\uAED8 \uC6C0\uC9C1\uC5EC\uC694."
        };
      }
      case "B": {
        const buyP = num(form.buy_price);
        const sellP = num(form.sell_price);
        const lines = [];
        push(lines, priceLine(buyP, UP, `${isShort ? "\uC20F \uC815\uB9AC" : "\uC5EC\uAE30\uC11C \uB9E4\uC218"} \xB7 ${fmtPrice(buyP)}`, "5 3"));
        push(lines, priceLine(sellP, DOWN, `${isShort ? "\uC20F \uC9C4\uC785" : "\uC5EC\uAE30\uC11C \uB9E4\uB3C4"} \xB7 ${fmtPrice(sellP)}`, "5 3"));
        return {
          legend: isShort ? [
            { label: "\uC20F \uC9C4\uC785\uAC00", color: DOWN },
            { label: "\uC20F \uCCAD\uC0B0\uAC00", color: UP }
          ] : [
            { label: "\uB9E4\uC218 \uC9C0\uC815\uAC00", color: UP },
            { label: "\uB9E4\uB3C4 \uC9C0\uC815\uAC00", color: DOWN }
          ],
          priceLines: lines,
          note: isShort ? "\uAC00\uACA9\uC774 \uC704\uCABD \uC120\uAE4C\uC9C0 \uC624\uB974\uBA74 \uD314\uC544\uC11C \uC20F\uC5D0 \uB4E4\uC5B4\uAC00\uACE0, \uC544\uB798\uCABD \uC120\uAE4C\uC9C0 \uB0B4\uB824\uC624\uBA74 \uB418\uC0AC\uC11C \uC815\uB9AC\uD574\uC694. \uB450 \uC120 \uC0AC\uC774\uB97C \uC624\uAC08 \uB54C \uC218\uC775\uC774 \uB098\uC694." : "\uAC00\uACA9\uC774 \uB9E4\uC218\uC120\uAE4C\uC9C0 \uB0B4\uB824\uC624\uBA74 \uC0AC\uACE0, \uB9E4\uB3C4\uC120\uAE4C\uC9C0 \uC624\uB974\uBA74 \uD314\uC544\uC694. \uB450 \uC120 \uC0AC\uC774\uB97C \uC624\uAC08 \uB54C \uC218\uC775\uC774 \uB098\uC694."
        };
      }
      case "C":
        return {
          legend: [],
          note: "\uC815\uAE30 \uBD84\uD560\uB9E4\uC218(DCA)\uB294 \uC815\uD574\uC9C4 \uAC04\uACA9\uB9C8\uB2E4 \uAC19\uC740 \uAE08\uC561\uC73C\uB85C \uACC4\uC18D \uC0AC\uC694. \uD2B9\uC815 \uAC00\uACA9\uC744 \uB178\uB9AC\uC9C0 \uC54A\uC544 \uCC28\uD2B8\uC5D0 \uB9E4\uC218\xB7\uB9E4\uB3C4\uC120\uC774 \uC5C6\uC5B4\uC694."
        };
      case "D": {
        const lo = num(form.lower_price);
        const up = num(form.upper_price);
        const n2 = Math.max(2, Math.round(num(form.grid_count) || 0));
        if (!(up > lo) || !(lo > 0)) return { note: "\uAC00\uACA9 \uBC94\uC704(\uD558\uB2E8\xB7\uC0C1\uB2E8)\uB97C \uC62C\uBC14\uB974\uAC8C \uB123\uC73C\uBA74 \uACA9\uC790\uC120\uC774 \uD45C\uC2DC\uB3FC\uC694." };
        const mid = (up + lo) / 2;
        const lines = [];
        for (let i2 = 0; i2 <= n2; i2++) {
          const price = form.grid_mode === "geometric" ? lo * Math.pow(up / lo, i2 / n2) : lo + (up - lo) * i2 / n2;
          const label = i2 === 0 ? `\uD558\uB2E8 ${fmtPrice(lo)}` : i2 === n2 ? `\uC0C1\uB2E8 ${fmtPrice(up)}` : "";
          push(lines, { price, color: price < mid ? UP : DOWN, label, dash: "2 4" });
        }
        return {
          legend: [
            { label: "\uC544\uB798\uCABD \uCE78(\uB9E4\uC218)", color: UP },
            { label: "\uC704\uCABD \uCE78(\uB9E4\uB3C4)", color: DOWN }
          ],
          priceLines: lines,
          note: "\uAC00\uACA9 \uBC94\uC704\uB97C \uC5EC\uB7EC \uCE78\uC73C\uB85C \uB098\uB220, \uD55C \uCE78 \uB0B4\uB9AC\uBA74 \uC0AC\uACE0 \uD55C \uCE78 \uC624\uB974\uBA74 \uD30C\uB294 \uAC78 \uBC18\uBCF5\uD574\uC694. \uC544\uB798 \uCE78\uC740 \uB9E4\uC218, \uC704 \uCE78\uC740 \uB9E4\uB3C4 \uC790\uB9AC\uC608\uC694."
        };
      }
      case "E": {
        const trail = num(form.trail_percent);
        const lines = [];
        let mx = -Infinity;
        const trailVals = closes.map((c2) => {
          mx = Math.max(mx, c2);
          return trail > 0 ? mx * (1 - trail / 100) : null;
        });
        if (form.entry_mode === "dip") {
          const dip = num(form.entry_dip);
          if (dip > 0) push(lines, priceLine(last * (1 - dip / 100), UP, `\uC9C4\uC785 \uBAA9\uD45C -${dip}%`, "5 3"));
        }
        return {
          legend: [
            { label: `\uD2B8\uB808\uC77C\uB9C1 \uC2A4\uD0D1(\uACE0\uC810 -${trail || "?"}%)`, color: DOWN },
            ...form.entry_mode === "dip" ? [{ label: "\uC9C4\uC785 \uBAA9\uD45C", color: UP }] : []
          ],
          priceLines: lines,
          series: [
            { id: "trail", color: DOWN, label: "\uD2B8\uB808\uC77C\uB9C1 \uC2A4\uD0D1", values: trailVals, width: 1.5, dash: "4 3" }
          ],
          note: "\uC774\uC775\uC774 \uB098\uAE30 \uC2DC\uC791\uD558\uBA74 \uACE0\uC810\uC744 \uB530\uB77C \uC2A4\uD0D1\uC120\uC774 \uC62C\uB77C\uAC00\uC694. \uAC00\uACA9\uC774 \uC774 \uC120\uAE4C\uC9C0 \uB0B4\uB824\uC624\uBA74 \uC774\uC775\uC744 \uC9C0\uD0A4\uBA70 \uC815\uB9AC\uD574\uC694."
        };
      }
      case "F": {
        const period = Math.round(num(form.rsi_period) || 14);
        const ent = num(form.entry_threshold);
        const ext = num(form.exit_threshold);
        const r2 = rsi(closes, period);
        const markers = [];
        for (let i2 = 1; i2 < r2.length; i2++) {
          if (r2[i2] == null || r2[i2 - 1] == null) continue;
          if (r2[i2 - 1] > ent && r2[i2] <= ent)
            markers.push(signal(i2, isShort ? "exit" : "entry", isShort, `RSI ${ent} \uC774\uD558`));
          else if (r2[i2 - 1] < ext && r2[i2] >= ext)
            markers.push(signal(i2, isShort ? "entry" : "exit", isShort, `RSI ${ext} \uC774\uC0C1`));
        }
        return {
          legend: [
            { label: `RSI(${period})`, color: MID },
            signalLegend("entry", isShort),
            signalLegend("exit", isShort)
          ],
          markers,
          // 보조창의 두 기준선에도 그 자리에서 무슨 주문이 나가는지 붙여 준다.
          rsi: {
            values: r2,
            entry: ent,
            exit: ext,
            lowLabel: signalLabel(isShort ? "exit" : "entry", isShort),
            highLabel: signalLabel(isShort ? "entry" : "exit", isShort)
          },
          note: isShort ? `\uC544\uB798 \uBCF4\uC870\uCC3D\uC774 RSI\uC608\uC694. ${ext} \uC774\uC0C1\uC73C\uB85C \uC624\uB974\uBA74(\uACFC\uB9E4\uC218) \uD314\uC544\uC11C \uC20F\uC5D0 \uB4E4\uC5B4\uAC00\uACE0, ${ent} \uC774\uD558\uB85C \uB0B4\uB824\uC624\uBA74(\uACFC\uB9E4\uB3C4) \uC815\uB9AC\uD574\uC694.` : `\uC544\uB798 \uBCF4\uC870\uCC3D\uC774 RSI\uC608\uC694. ${ent} \uC774\uD558\uB85C \uB0B4\uB824\uC624\uBA74(\uACFC\uB9E4\uB3C4) \uB9E4\uC218, ${ext} \uC774\uC0C1\uC774\uBA74(\uACFC\uB9E4\uC218) \uB9E4\uB3C4 \uC2E0\uD638\uB85C \uBD10\uC694.`
        };
      }
      case "G": {
        const period = Math.round(num(form.bb_period) || 20);
        const k2 = num(form.bb_std) || 2;
        const mid = sma(closes, period);
        const std = rollingStd(closes, period, mid);
        const upper = mid.map((m2, i2) => m2 == null || std[i2] == null ? null : m2 + k2 * std[i2]);
        const lower = mid.map((m2, i2) => m2 == null || std[i2] == null ? null : m2 - k2 * std[i2]);
        const reversion = form.strategy !== "breakout";
        const touchUp = (i2, level) => level[i2] != null && level[i2 - 1] != null && candles2[i2].h >= level[i2] && candles2[i2 - 1].h < level[i2 - 1];
        const touchDown = (i2, level) => level[i2] != null && level[i2 - 1] != null && candles2[i2].l <= level[i2] && candles2[i2 - 1].l > level[i2 - 1];
        const opposite = form.exit_target === "opposite";
        const markers = [];
        for (let i2 = 1; i2 < closes.length; i2++) {
          if (upper[i2] == null || lower[i2] == null) continue;
          if (reversion) {
            const entryBand = isShort ? upper : lower;
            const entryHit = isShort ? touchUp(i2, entryBand) : touchDown(i2, entryBand);
            if (entryHit)
              markers.push(signal(i2, "entry", isShort, isShort ? "\uC0C1\uB2E8 \uBC34\uB4DC \uD130\uCE58" : "\uD558\uB2E8 \uBC34\uB4DC \uD130\uCE58"));
            const exitBand = opposite ? isShort ? lower : upper : mid;
            const exitHit = isShort ? touchDown(i2, exitBand) : touchUp(i2, exitBand);
            if (exitHit)
              markers.push(
                signal(i2, "exit", isShort, opposite ? isShort ? "\uD558\uB2E8 \uBC34\uB4DC \uB3C4\uB2EC" : "\uC0C1\uB2E8 \uBC34\uB4DC \uB3C4\uB2EC" : "\uC911\uC559\uC120 \uB3C4\uB2EC")
              );
          } else {
            if (closes[i2 - 1] <= upper[i2 - 1] && closes[i2] > upper[i2])
              markers.push(signal(i2, isShort ? "exit" : "entry", isShort, "\uC0C1\uB2E8 \uB3CC\uD30C"));
            if (closes[i2 - 1] >= lower[i2 - 1] && closes[i2] < lower[i2])
              markers.push(signal(i2, isShort ? "entry" : "exit", isShort, "\uD558\uB2E8 \uC774\uD0C8"));
          }
        }
        return {
          legend: [
            { label: "\uC0C1\uB2E8 \uBC34\uB4DC", color: BAND },
            { label: "\uC911\uC559\uC120(\uC774\uB3D9\uD3C9\uADE0)", color: MID },
            { label: "\uD558\uB2E8 \uBC34\uB4DC", color: BAND },
            signalLegend("entry", isShort),
            signalLegend("exit", isShort)
          ],
          series: [
            { id: "bb_upper", color: BAND, label: "\uC0C1\uB2E8 \uBC34\uB4DC", values: upper, width: 1 },
            { id: "bb_mid", color: MID, label: "\uC911\uC559\uC120", values: mid, width: 1.5, dash: "4 3" },
            { id: "bb_lower", color: BAND, label: "\uD558\uB2E8 \uBC34\uB4DC", values: lower, width: 1 }
          ],
          bands: [{ upper, lower, fill: FILL }],
          markers,
          note: reversion ? isShort ? `\uAC00\uC6B4\uB370\uB294 \uC774\uB3D9\uD3C9\uADE0, \uC704\uC544\uB798\uB294 \uBCC0\uB3D9\uC131 \uBC34\uB4DC\uC608\uC694. \uAC00\uACA9\uC774 \uC0C1\uB2E8 \uBC34\uB4DC\uC5D0 \uB2FF\uC73C\uBA74 \uD314\uC544\uC11C \uC20F\uC5D0 \uB4E4\uC5B4\uAC00\uACE0, ${opposite ? "\uD558\uB2E8 \uBC34\uB4DC" : "\uC911\uC559\uC120"}\uAE4C\uC9C0 \uB0B4\uB824\uC624\uBA74 \uB418\uC0AC\uC11C \uC815\uB9AC\uD574\uC694.` : `\uAC00\uC6B4\uB370\uB294 \uC774\uB3D9\uD3C9\uADE0, \uC704\uC544\uB798\uB294 \uBCC0\uB3D9\uC131 \uBC34\uB4DC\uC608\uC694. \uAC00\uACA9\uC774 \uD558\uB2E8 \uBC34\uB4DC\uC5D0 \uB2FF\uC73C\uBA74 \uB9E4\uC218, ${opposite ? "\uC0C1\uB2E8 \uBC34\uB4DC" : "\uC911\uC559\uC120"}\uC5D0\uC11C \uB9E4\uB3C4\uD574\uC694.` : isShort ? "\uAC00\uC6B4\uB370\uB294 \uC774\uB3D9\uD3C9\uADE0, \uC704\uC544\uB798\uB294 \uBCC0\uB3D9\uC131 \uBC34\uB4DC\uC608\uC694. \uAC00\uACA9\uC774 \uD558\uB2E8 \uBC34\uB4DC\uB97C \uC544\uB798\uB85C \uB6AB\uC73C\uBA74 \uC20F\uC5D0 \uB4E4\uC5B4\uAC00\uACE0(\uD558\uB77D \uCD94\uC138), \uC0C1\uB2E8\uC744 \uC704\uB85C \uB6AB\uC73C\uBA74 \uC815\uB9AC\uD574\uC694." : "\uAC00\uC6B4\uB370\uB294 \uC774\uB3D9\uD3C9\uADE0, \uC704\uC544\uB798\uB294 \uBCC0\uB3D9\uC131 \uBC34\uB4DC\uC608\uC694. \uAC00\uACA9\uC774 \uC0C1\uB2E8 \uBC34\uB4DC\uB97C \uC704\uB85C \uB6AB\uC73C\uBA74 \uB9E4\uC218(\uCD94\uC138), \uD558\uB2E8\uC744 \uC544\uB798\uB85C \uB6AB\uC73C\uBA74 \uB9E4\uB3C4\uD574\uC694."
        };
      }
      case "H": {
        const dev = num(form.price_deviation) / 100;
        const stepScale = num(form.safety_order_step_scale) || 1;
        const maxSO = Math.max(0, Math.round(num(form.max_safety_orders) || 0));
        const tp = num(form.take_profit);
        const lines = [];
        push(lines, priceLine(last, UP, `\uAE30\uBCF8 \uB9E4\uC218 \xB7 ${fmtPrice(last)}`, void 0));
        let cumDev = 0;
        let step = dev;
        for (let i2 = 1; i2 <= maxSO; i2++) {
          cumDev += step;
          step *= stepScale;
          push(lines, priceLine(last * (1 - cumDev), UP, `${i2}\uCC28 \uCD94\uAC00\uB9E4\uC218`, "2 4"));
        }
        if (tp > 0) push(lines, priceLine(last * (1 + tp / 100), DOWN, `\uC775\uC808(\uD3C9\uB2E8 +${tp}%)`, "5 3"));
        return {
          legend: [
            { label: "\uB9E4\uC218/\uCD94\uAC00\uB9E4\uC218", color: UP },
            { label: "\uC775\uC808\uC120(\uD3C9\uB2E8 \uAE30\uC900)", color: DOWN }
          ],
          priceLines: lines,
          note: "\uAC00\uACA9\uC774 \uB0B4\uB824\uAC08 \uB54C\uB9C8\uB2E4 \uC815\uD574\uC9C4 \uAC04\uACA9\uC73C\uB85C \uB354 \uC0AC\uC11C \uD3C9\uB2E8\uC744 \uB0AE\uCDB0\uC694. \uC544\uB798 \uC120\uB4E4\uC774 \uCD94\uAC00\uB9E4\uC218 \uC790\uB9AC, \uC704 \uC120\uC774 \uD3C9\uB2E8 \uB300\uBE44 \uC775\uC808 \uBAA9\uD45C\uC608\uC694."
        };
      }
      case "I": {
        const k2 = num(form.k);
        const target = candles2.map(
          (c2, i2) => i2 === 0 || !(k2 >= 0) ? null : c2.o + k2 * (candles2[i2 - 1].h - candles2[i2 - 1].l)
        );
        const markers = [];
        for (let i2 = 1; i2 < candles2.length; i2++) {
          if (target[i2] == null) continue;
          if (candles2[i2].h >= target[i2] && candles2[i2 - 1].h < (target[i2 - 1] ?? Infinity))
            markers.push(signal(i2, "entry", isShort, "\uB3CC\uD30C"));
        }
        return {
          legend: [
            { label: "\uB3CC\uD30C \uB9E4\uC218\uC120", color: UP, kind: "buy" }
          ],
          series: [{ id: "breakout", color: UP, label: "\uB3CC\uD30C \uB9E4\uC218\uC120", values: target, width: 1.5, dash: "4 3" }],
          markers,
          note: "\uC804 \uBD09 \uBCC0\uB3D9\uD3ED\uC758 k\uBC30\uB9CC\uD07C \uC624\uB298 \uC2DC\uAC00 \uC704\uB85C \uAC00\uACA9\uC774 \uB6AB\uC73C\uBA74 \uB9E4\uC218\uD574\uC694. \uC8FC\uD669 \uACC4\uB2E8\uC120\uC774 \uADF8 \uB3CC\uD30C \uAE30\uC900\uC774\uC5D0\uC694."
        };
      }
      case "J": {
        const fp = Math.round(num(form.fast_period) || 20);
        const sp = Math.round(num(form.slow_period) || 60);
        const useEma = form.ma_type === "EMA";
        const fast = useEma ? ema(closes, fp) : sma(closes, fp);
        const slow = useEma ? ema(closes, sp) : sma(closes, sp);
        const markers = crossSignals(fast, slow, isShort, "\uACE8\uB4E0\uD06C\uB85C\uC2A4", "\uB370\uB4DC\uD06C\uB85C\uC2A4");
        return {
          legend: [
            { label: `\uB2E8\uAE30 ${useEma ? "EMA" : "SMA"}(${fp})`, color: FAST },
            { label: `\uC7A5\uAE30 ${useEma ? "EMA" : "SMA"}(${sp})`, color: SLOW },
            signalLegend("entry", isShort),
            signalLegend("exit", isShort)
          ],
          series: [
            { id: "ma_fast", color: FAST, label: "\uB2E8\uAE30 \uC774\uB3D9\uD3C9\uADE0", values: fast, width: 1.5 },
            { id: "ma_slow", color: SLOW, label: "\uC7A5\uAE30 \uC774\uB3D9\uD3C9\uADE0", values: slow, width: 1.5 }
          ],
          markers,
          note: isShort ? "\uB2E8\uAE30\uC120\uC774 \uC7A5\uAE30\uC120\uC744 \uC544\uB798\uB85C \uB6AB\uC73C\uBA74(\uB370\uB4DC\uD06C\uB85C\uC2A4) \uD314\uC544\uC11C \uC20F\uC5D0 \uB4E4\uC5B4\uAC00\uACE0, \uC704\uB85C \uB6AB\uC73C\uBA74(\uACE8\uB4E0\uD06C\uB85C\uC2A4) \uB418\uC0AC\uC11C \uC815\uB9AC\uD574\uC694. \uB450 \uC120\uC774 \uB9CC\uB098\uB294 \uACF3\uC774 \uC2E0\uD638\uC608\uC694." : "\uB2E8\uAE30\uC120\uC774 \uC7A5\uAE30\uC120\uC744 \uC704\uB85C \uB6AB\uC73C\uBA74(\uACE8\uB4E0\uD06C\uB85C\uC2A4) \uB9E4\uC218, \uC544\uB798\uB85C \uB6AB\uC73C\uBA74(\uB370\uB4DC\uD06C\uB85C\uC2A4) \uB9E4\uB3C4\uD574\uC694. \uB450 \uC120\uC774 \uB9CC\uB098\uB294 \uACF3\uC774 \uC2E0\uD638\uC608\uC694."
        };
      }
      case "K": {
        const drop = num(form.drop_trigger_pct);
        const ltp = num(form.long_take_profit_pct);
        const stp = num(form.short_take_profit_pct);
        const ssl = num(form.short_stop_loss_pct);
        const lines = [];
        push(lines, priceLine(last, NEUTRAL, `\uD604\uC7AC\uAC00 ${fmtPrice(last)}`, "2 3"));
        const trigger = last * (1 - drop / 100);
        push(lines, priceLine(trigger, DOWN, `\uBC29\uC5B4 \uC2DC\uC791 -${drop}%`, "5 3"));
        if (ltp > 0) push(lines, priceLine(last * (1 + ltp / 100), UP, `\uB871 \uC775\uC808 +${ltp}%`, "5 3"));
        if (stp > 0) push(lines, priceLine(trigger * (1 - stp / 100), UP, `\uC20F \uC775\uC808`, "2 4"));
        if (ssl > 0) push(lines, priceLine(trigger * (1 + ssl / 100), DOWN, `\uC20F \uC190\uC808`, "2 4"));
        return {
          legend: [
            { label: "\uBC29\uC5B4 \uC2DC\uC791\uC120", color: DOWN },
            { label: "\uC775\uC808 \uBC29\uD5A5", color: UP }
          ],
          priceLines: lines,
          note: "\uAC00\uACA9\uC774 \uC9C4\uC785\uAC00\uBCF4\uB2E4 \uBC29\uC5B4 \uC2DC\uC791\uC120\uAE4C\uC9C0 \uB0B4\uB824\uC624\uBA74 \uC77C\uBD80\uB97C \uD314\uACE0, \uC124\uC815\uC5D0 \uB530\uB77C \uC20F\uC73C\uB85C \uC804\uD658\uD574 \uCD94\uAC00 \uD558\uB77D\uC5D0 \uB300\uC751\uD574\uC694."
        };
      }
      default:
        return null;
    }
  }

  // Strategy overlay compatibility preview
  var fixtures = {
    A: { take_profit_pct: 5, use_stop_loss: true, stop_loss_pct: 3 },
    B: { buy_price: 96, sell_price: 106 },
    C: {},
    D: { lower_price: 90, upper_price: 110, grid_count: 8, grid_mode: "arithmetic" },
    E: { trail_percent: 3, entry_mode: "dip", entry_dip: 2 },
    F: { rsi_period: 7, entry_threshold: 35, exit_threshold: 65 },
    G: { bb_period: 12, bb_std: 1.3, strategy: "reversion", exit_target: "opposite" },
    H: { price_deviation: 1, safety_order_step_scale: 1.4, max_safety_orders: 4, take_profit: 3 },
    I: { k: 0.4 },
    J: { fast_period: 5, slow_period: 16, ma_type: "EMA" },
    K: { drop_trigger_pct: 3, long_take_profit_pct: 5, short_take_profit_pct: 4, short_stop_loss_pct: 2 }
  };
  var candles = Array.from({ length: 160 }, (_2, i2) => {
    const o2 = 100 + Math.sin((i2 - 1) / 4) * 6 + i2 * 0.01, c2 = 100 + Math.sin(i2 / 4) * 6 + i2 * 0.01;
    return { t: 17672256e5 + i2 * 6e4, o: o2, h: Math.max(o2, c2) + 1.5, l: Math.min(o2, c2) - 1.5, c: c2, closed: true };
  });
  var assert = (condition, message) => {
    if (!condition) throw new Error(message);
  };
  var resolveColor = (color) => {
    const probe = document.createElement("span");
    probe.style.color = color;
    document.body.append(probe);
    const result = getComputedStyle(probe).color;
    probe.remove();
    return result;
  };
  var time = (candle) => candle.t / 1e3;
  var active;
  var OverlayDrawing = class {
    constructor(rows, overlay, kind = "bands") {
      this.rows = rows;
      this.overlay = overlay;
      this.kind = kind;
      this.draws = 0;
      this.points = [];
    }
    attached({ chart, series, requestUpdate }) {
      Object.assign(this, { chart, series, requestUpdate });
    }
    paneViews() {
      return [{ zOrder: () => this.kind === "markers" ? "normal" : "bottom", renderer: () => ({ draw: (target) => target.useMediaCoordinateSpace((scope) => this.draw(scope)) }) }];
    }
    updateAllViews() {
    }
    draw({ context: ctx, mediaSize }) {
      this.draws++;
      this.points = [];
      const x2 = (i2) => this.chart.timeScale().timeToCoordinate(time(this.rows[i2]));
      const y2 = (value) => this.series.priceToCoordinate(value);
      const polygon = (points, color) => {
        if (points.length < 3) return;
        ctx.fillStyle = resolveColor(color);
        ctx.beginPath();
        points.forEach(([x3, y3], i2) => i2 ? ctx.lineTo(x3, y3) : ctx.moveTo(x3, y3));
        ctx.closePath();
        ctx.fill();
      };
      if (this.kind === "bands") for (const band of this.overlay.bands || []) {
        let upper = [], lower = [];
        const flush = () => {
          polygon([...upper, ...lower.reverse()], band.fill);
          upper = [];
          lower = [];
        };
        this.rows.forEach((_2, i2) => {
          if (band.upper[i2] == null || band.lower[i2] == null) {
            flush();
            return;
          }
          const xx = x2(i2), top = y2(band.upper[i2]), bottom = y2(band.lower[i2]);
          if (xx == null || top == null || bottom == null) {
            flush();
            return;
          }
          upper.push([xx, top]);
          lower.push([xx, bottom]);
          this.points.push([xx, top, bottom, i2]);
        });
        flush();
      }
      if (this.kind === "zones") {
        const upper = y2(this.overlay.exit), lower = y2(this.overlay.entry);
        ctx.fillStyle = "rgba(200,30,51,0.06)";
        ctx.fillRect(0, 0, mediaSize.width, upper);
        ctx.fillStyle = "rgba(0,119,56,0.06)";
        ctx.fillRect(0, lower, mediaSize.width, mediaSize.height - lower);
        this.points.push([0, upper, lower]);
      }
      if (this.kind === "markers") for (const m2 of this.overlay.markers || []) {
        const xx = x2(m2.index), bar = this.rows[m2.index], buy = m2.side === "buy";
        const yy = y2(buy ? bar.l : bar.h) + (buy ? 9 : -9);
        if (xx == null || yy == null) continue;
        polygon([[xx, yy + (buy ? -7 : 7)], [xx + 4, yy], [xx - 4, yy]], buy ? "rgb(var(--chart-up))" : "rgb(var(--chart-down))");
        this.points.push([xx, yy, m2.side, m2.index]);
      }
    }
  };
  function render(rule, side = "long", session = false, extra = {}) {
    active?.chart.remove();
    const rows = extra.rows || candles;
    const form = { rule_type: rule, position_side: side, ...fixtures[rule], ...extra.params };
    const overlay = session ? computeSessionOverlay({ rule_type: rule, position_side: side, params: form }, 100, side, rows) : computeStrategyOverlay(form, rows);
    const snapshots = JSON.stringify(overlay);
    const chart = ue(document.querySelector("#chart"), {
      autoSize: true,
      layout: { background: { type: "solid", color: "#0b0e11" }, textColor: "#bac0c8", fontFamily: "sans-serif", attributionLogo: true },
      grid: { vertLines: { color: "#20262e" }, horzLines: { color: "#20262e" } },
      timeScale: { timeVisible: true, secondsVisible: false },
      rightPriceScale: { scaleMargins: { top: 0.1, bottom: 0.1 } }
    });
    const base = chart.addSeries(nr, {
      upColor: "#00c087",
      downColor: "#f6465d",
      wickUpColor: "#00c087",
      wickDownColor: "#f6465d",
      borderVisible: false,
      autoscaleInfoProvider: (original) => {
        const info = original();
        if (!info) return info;
        const visible = chart.timeScale().getVisibleLogicalRange();
        const start = Math.max(0, Math.floor(visible?.from ?? 0)), end = Math.min(rows.length, Math.ceil(visible?.to ?? rows.length) + 1);
        const high = info.priceRange.maxValue, low = info.priceRange.minValue, span = high - low || high * 1e-3 || 1;
        const values = [...(overlay.priceLines || []).map((line) => line.price), ...(overlay.series || []).flatMap((line) => line.values.slice(start, end))].filter((value) => value != null && Number.isFinite(value));
        return { ...info, priceRange: { minValue: Math.min(low, Math.max(low - span * 1.5, ...[Math.min(...values)])), maxValue: Math.max(high, Math.min(high + span * 1.5, ...[Math.max(...values)])) } };
      }
    });
    base.setData(rows.map((c2) => ({ time: time(c2), open: c2.o, high: c2.h, low: c2.l, close: c2.c })));
    const priceLines = (overlay.priceLines || []).map((line) => {
      const target = base.createPriceLine({ price: line.price, color: resolveColor(line.color), title: line.label || "", lineStyle: line.dash ? n.Dashed : n.Solid, lineWidth: 1, axisLabelVisible: !!line.label });
      assert(target.options().price === line.price, "Price changed");
      return target;
    });
    const lineSeries = [];
    const addLine = (definition, pane = 0, options = {}) => {
      let run = [];
      const flush = () => {
        if (!run.length) return;
        const s2 = chart.addSeries(Te, { color: resolveColor(definition.color || "rgb(99 102 241)"), lineWidth: Math.round(definition.width || 1.5), lineStyle: definition.dash ? n.Dashed : n.Solid, priceLineVisible: false, lastValueVisible: false, autoscaleInfoProvider: () => null, ...options }, pane);
        s2.setData(run);
        assert(s2.data().every((point, i2) => point.value === run[i2].value), "Indicator value changed");
        lineSeries.push(s2);
        run = [];
      };
      definition.values.forEach((value, i2) => {
        if (value == null) {
          flush();
          return;
        }
        run.push({ time: time(rows[i2]), value });
      });
      flush();
    };
    (overlay.series || []).forEach((s2) => addLine(s2));
    const converted = (overlay.markers || []).map((m2, i2) => ({ time: time(rows[m2.index]), position: m2.side === "buy" ? "belowBar" : "aboveBar", shape: m2.side === "buy" ? "arrowUp" : "arrowDown", color: resolveColor(m2.side === "buy" ? "rgb(var(--chart-up))" : "rgb(var(--chart-down))"), id: String(i2), text: "" }));
    const nativeMarkers = Hr(base, converted, { autoScale: false });
    assert(nativeMarkers.markers().every((m2, i2) => m2.time === time(rows[overlay.markers[i2].index])), "Marker candle changed");
    nativeMarkers.detach();
    const drawing = new OverlayDrawing(rows, overlay);
    base.attachPrimitive(drawing);
    const markerDrawing = new OverlayDrawing(rows, overlay, "markers");
    base.attachPrimitive(markerDrawing);
    let rsiSeries, zones;
    if (overlay.rsi) {
      addLine({ values: overlay.rsi.values }, 1, { autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }) });
      rsiSeries = lineSeries.at(-1);
      for (const [price, color, title] of [[overlay.rsi.entry, "#00c087", overlay.rsi.lowLabel], [overlay.rsi.exit, "#f6465d", overlay.rsi.highLabel]]) rsiSeries.createPriceLine({ price, color, title, lineWidth: 1, lineStyle: n.Dashed });
      zones = new OverlayDrawing(rows, overlay.rsi, "zones");
      rsiSeries.attachPrimitive(zones);
      chart.panes()[1].setHeight(120);
    }
    chart.timeScale().setVisibleLogicalRange({ from: 70, to: 159 });
    assert(JSON.stringify(overlay) === snapshots, "Original computation mutated");
    document.querySelector("#heading").textContent = `${rule} \xB7 ${side} ${session ? "\xB7 \uD3C9\uB2E8 \uD3EC\uD568" : ""}`;
    document.querySelector("#legend").textContent = (overlay.legend || []).map((item) => item.label).join(" | ");
    active = { chart, base, overlay, rows, lineSeries, priceLines, drawing, markerDrawing, rsiSeries, zones, rule, side };
    return { rule, side, session, priceLines: priceLines.length, series: lineSeries.length, markers: converted.length, bands: (overlay.bands || []).length, rsi: !!rsiSeries, paneCount: chart.panes().length };
  }
  window.render = render;
  window.inspect = () => ({
    bands: active.drawing.points,
    bandDraws: active.drawing.draws,
    markers: active.markerDrawing.points,
    zones: active.zones?.points,
    timeX: active.chart.timeScale().timeToCoordinate(time(active.rows[120])),
    priceY: active.base.priceToCoordinate(active.rows[120].c),
    candleSpan: Math.abs(active.base.priceToCoordinate(108) - active.base.priceToCoordinate(94)),
    panes: active.chart.panes().length
  });
  window.zoom = () => active.chart.timeScale().setVisibleLogicalRange({ from: 100, to: 140 });
  window.tick = () => {
    const rows = candles.map((c2) => ({ ...c2 }));
    rows[159].c += 0.75;
    rows[159].h = Math.max(rows[159].h, rows[159].c);
    rows.push({ t: rows[159].t + 6e4, o: rows[159].c, h: rows[159].c + 2, l: rows[159].c - 1, c: rows[159].c + 1, closed: false });
    return render(active.rule, active.side, false, { rows });
  };
  window.checkGaps = () => {
    const overlay = computeStrategyOverlay({ rule_type: "J", ...fixtures.J }, candles);
    return { leadingNulls: overlay.series.map((s2) => s2.values.findIndex((value) => value != null)), version: Qr() };
  };
  window.ready = true;
})();
/*!
 * @license
 * TradingView Lightweight Charts™ v5.2.1
 * Copyright (c) 2026 TradingView, Inc.
 * Licensed under Apache License 2.0 https://www.apache.org/licenses/LICENSE-2.0
 */
