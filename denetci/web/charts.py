"""Sunucu tarafında çizilen basit SVG grafikler (dış kütüphane yok, çevrimdışı çalışır)."""

from markupsafe import Markup, escape

from ..parsing import kurus_str


def tl_short(kurus: int) -> str:
    """Kısa tutar gösterimi: 12,3 mn / 845 bin TL."""
    v = kurus / 100
    a = abs(v)
    if a >= 1_000_000_000:
        s = f"{v / 1_000_000_000:,.1f} mr"
    elif a >= 1_000_000:
        s = f"{v / 1_000_000:,.1f} mn"
    elif a >= 1_000:
        s = f"{v / 1_000:,.0f} bin"
    else:
        s = f"{v:,.0f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def balance_bars(segments_aktif, segments_pasif) -> Markup:
    """İki yatay yığılmış çubuk: Aktif ve Pasif. segments: [(ad, tutar_kurus, renk_degiskeni)]."""
    width, bar_h, gap_y, left = 640, 34, 26, 56
    total = max(sum(max(v, 0) for _, v, _ in segments_aktif), sum(max(v, 0) for _, v, _ in segments_pasif), 1)
    inner = width - left
    parts = [f'<svg viewBox="0 0 {width} {bar_h * 2 + gap_y + 8}" role="img" aria-label="Bilanço yapısı">']
    for row, (title, segs) in enumerate((("Aktif", segments_aktif), ("Pasif", segments_pasif))):
        y = 4 + row * (bar_h + gap_y)
        parts.append(f'<text x="0" y="{y + bar_h / 2 + 4}" class="lbl-strong">{title}</text>')
        x = left
        visible = [(n, v, c) for n, v, c in segs if v > 0]
        for i, (name, val, color) in enumerate(visible):
            w = inner * val / total
            seg_w = max(w - (2 if i < len(visible) - 1 else 0), 1)
            last = i == len(visible) - 1
            # Yalnızca uçtaki köşeler yuvarlatılır.
            rx = 4 if last else 0
            pct = val / sum(v for _, v, _ in visible) * 100
            tip = f"{name}: {kurus_str(val)} TL (%{pct:.0f})"
            if last and seg_w > 8:
                parts.append(f'<path d="M{x:.1f},{y} h{seg_w - 4:.1f} a4,4 0 0 1 4,4 v{bar_h - 8} a4,4 0 0 1 -4,4 h-{seg_w - 4:.1f} z" '
                             f'fill="var({color})"><title>{escape(tip)}</title></path>')
            else:
                parts.append(f'<rect x="{x:.1f}" y="{y}" width="{seg_w:.1f}" height="{bar_h}" rx="{rx if seg_w > 8 else 0}" '
                             f'fill="var({color})"><title>{escape(tip)}</title></rect>')
            x += w
    parts.append("</svg>")
    return Markup("".join(parts))


# ---- Rapor grafikleri ---------------------------------------------------------------------------------------------
# Yazdırmada da çalışsın diye renkler CSS değişkeni + yedek renkle verilir; her grafikte değerler yazıyla da yer alır.

C_ANA = "var(--s1, #8F6D22)"  # altın koyu: ana seri / toplam
C_IKI = "var(--s2, #29241D)"  # mürekkep: ikinci seri
C_ART = "var(--s3, #4E7A4A)"  # yeşil: artış
C_AZ = "var(--s5, #9A3B2E)"  # kırmızı: azalış
C_BANT = "var(--accent-soft, #F3E8CF)"
C_IZGARA = "var(--line, #E4D7BC)"


def _esc(s) -> str:
    return str(escape(s))


def _nice_max(v: float) -> float:
    """Eksen için yuvarlak üst sınır (1, 2, 2.5, 5 × 10^n)."""
    if v <= 0:
        return 1
    import math
    e = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * e:
            return m * e
    return 10 * e


def aylik_grafik(aylar, seriler=(("Net satışlar", "net_satis", C_ANA), ("Dönem kârı", "donem_kari", C_IKI))) -> Markup:
    """Ay ay gruplanmış dikey çubuklar; eksi değerler sıfır çizgisinin altına iner. aylar: ceyrek.Kolon listesi."""
    if not aylar:
        return Markup("")
    W, H, L, R, T, B = 680, 270, 64, 8, 24, 46
    vals = [[(a.kal.get(key) or 0) if a.var else None for a in aylar] for _, key, _ in seriler]
    flat = [v for s in vals for v in s if v is not None] or [0]
    hi, lo = max(max(flat), 0), min(min(flat), 0)
    adim = _nice_max(((hi - lo) / 4 or 100) / 100) * 100
    import math
    top, bot = math.ceil(hi / adim) * adim or adim, math.floor(lo / adim) * adim
    span = (top - bot) or 1
    ih = H - T - B
    y = lambda v: T + (top - v) / span * ih  # noqa: E731
    p = [f'<svg class="rc" viewBox="0 0 {W} {H}" role="img" aria-label="Aylara göre {_esc(", ".join(s[0] for s in seriler))}">']
    for i in range(int(round(span / adim)) + 1):
        v = bot + adim * i
        yy = y(v)
        p.append(f'<line x1="{L}" x2="{W - R}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="{C_IZGARA}" stroke-width="1"/>'
                 f'<text x="{L - 8}" y="{yy + 4:.1f}" text-anchor="end" class="rc-ax">{tl_short(int(v))}</text>')
    y0 = y(0)
    p.append(f'<line x1="{L}" x2="{W - R}" y1="{y0:.1f}" y2="{y0:.1f}" stroke="var(--ink-2, #4A4237)" stroke-width="1"/>')
    gw = (W - L - R) / len(aylar)
    n = len(seriler)
    bw = min(40, (gw - 18) / n)
    for i, a in enumerate(aylar):
        gx = L + gw * i + (gw - bw * n - 2 * (n - 1)) / 2
        p.append(f'<text x="{L + gw * i + gw / 2:.1f}" y="{H - B + 18}" text-anchor="middle" class="rc-lbl">{_esc(a.ad)}</text>')
        if not a.var:
            p.append(f'<text x="{L + gw * i + gw / 2:.1f}" y="{y0 - 8:.1f}" text-anchor="middle" class="rc-ax">veri yok</text>')
            continue
        for j, (ad, key, renk) in enumerate(seriler):
            v = vals[j][i]
            x = gx + j * (bw + 2)
            y1, y2 = sorted((y(v), y0))
            h = max(y2 - y1, 1)
            # Yalnızca veri ucu yuvarlatılır (taban düz)
            if v >= 0:
                d = f"M{x:.1f},{y0:.1f} V{y1 + min(4, h):.1f} q0,-{min(4, h):.1f} {min(4, h):.1f},-{min(4, h):.1f} H{x + bw - min(4, h):.1f} q{min(4, h):.1f},0 {min(4, h):.1f},{min(4, h):.1f} V{y0:.1f} Z"
                ty = y1 - 5
            else:
                d = f"M{x:.1f},{y0:.1f} V{y2 - min(4, h):.1f} q0,{min(4, h):.1f} {min(4, h):.1f},{min(4, h):.1f} H{x + bw - min(4, h):.1f} q{min(4, h):.1f},0 {min(4, h):.1f},-{min(4, h):.1f} V{y0:.1f} Z"
                ty = y2 + 12
            p.append(f'<path d="{d}" fill="{renk}"><title>{_esc(a.ad)} · {_esc(ad)}: {kurus_str(v)} TL</title></path>')
            if v:
                # Yan yana çubukların etiketleri üst üste binmesin: aynı yükseklikteyse ikinci etiket çubuğun içine yazılır
                if j and v > 0 and vals[0][i] is not None and abs(y(vals[0][i]) - y(v)) < 14:
                    if y0 - y1 < 18:
                        continue
                    p.append(f'<text x="{x + bw / 2:.1f}" y="{y1 + 14:.1f}" text-anchor="middle" class="rc-val rc-in">{tl_short(v)}</text>')
                    continue
                p.append(f'<text x="{x + bw / 2:.1f}" y="{ty:.1f}" text-anchor="middle" class="rc-val">{tl_short(v)}</text>')
    p.append("</svg>")
    leg = "".join(f'<span><i style="background:{r}"></i>{_esc(ad)}</span>' for ad, _, r in seriler)
    return Markup(f'<div class="rc-wrap">{"".join(p)}<div class="rc-leg">{leg}</div></div>')


def selale(gelir_tablosu) -> Markup:
    """Net satıştan dönem kârına yatay şelale: her kalem kârı ne kadar artırıp azalttığını gösterir."""
    gt = {ln.label: ln.value for ln in gelir_tablosu}
    adimlar = [("Net satışlar", gt.get("Net satışlar", 0), "toplam")]
    for ad in ("Satışların maliyeti", "Faaliyet giderleri", "Diğer faaliyet gelirleri", "Diğer faaliyet giderleri",
               "Finansman giderleri", "Olağandışı gelir ve giderler (net)"):
        if gt.get(ad):
            adimlar.append((ad, gt[ad], "adim"))
    adimlar.append(("Dönem kârı (vergi öncesi)", gt.get("Dönem kârı (vergi öncesi)", 0), "toplam"))
    return kopru(adimlar, "Net satıştan dönem kârına")


def kopru(adimlar, etiket="Köprü grafiği") -> Markup:
    """Yatay şelale: [(ad, tutar, 'toplam'|'adim')]; toplamlar sıfırdan, adımlar bir öncekinin ucundan çizilir."""
    W, rh, L, R = 680, 30, 200, 92
    H = rh * len(adimlar) + 24
    cum, noktalar = 0, []
    for ad, v, tur in adimlar:
        if tur == "toplam":
            noktalar.append((0, v))
            cum = v
        else:
            noktalar.append((cum, cum + v))
            cum += v
    lo = min(0, *(min(a, b) for a, b in noktalar))
    hi = max(0, *(max(a, b) for a, b in noktalar)) or 1
    span = (hi - lo) or 1
    x = lambda v: L + (v - lo) / span * (W - L - R)  # noqa: E731
    p = [f'<svg class="rc" viewBox="0 0 {W} {H}" role="img" aria-label="{_esc(etiket)}">']
    x0 = x(0)
    p.append(f'<line x1="{x0:.1f}" x2="{x0:.1f}" y1="0" y2="{H - 18}" stroke="var(--ink-2, #4A4237)" stroke-width="1"/>')
    for i, ((ad, v, tur), (a, b)) in enumerate(zip(adimlar, noktalar)):
        yy = 4 + i * rh
        renk = C_ANA if tur == "toplam" else (C_ART if v > 0 else C_AZ)
        x1, x2 = sorted((x(a), x(b)))
        p.append(f'<text x="{L - 10}" y="{yy + 15}" text-anchor="end" class="rc-lbl{" rc-strong" if tur == "toplam" else ""}">{_esc(ad)}</text>')
        p.append(f'<rect x="{x1:.1f}" y="{yy + 3}" width="{max(x2 - x1, 1):.1f}" height="{rh - 10}" rx="3" fill="{renk}">'
                 f'<title>{_esc(ad)}: {kurus_str(v)} TL</title></rect>')
        p.append(f'<text x="{max(x1, x2) + 6:.1f}" y="{yy + 15}" class="rc-val">{"+" if tur == "adim" and v > 0 else ""}{tl_short(v)}</text>')
        if i < len(adimlar) - 1:
            nx = x(b)
            p.append(f'<line x1="{nx:.1f}" x2="{nx:.1f}" y1="{yy + rh - 7}" y2="{yy + rh + 3}" stroke="var(--muted, #6B6253)" stroke-dasharray="2 2"/>')
    p.append("</svg>")
    az, art = ("Kârı azaltan", "Kârı artıran") if "kâr" in etiket else ("Azaltan", "Artıran")
    leg = (f'<span><i style="background:{C_ANA}"></i>Toplam</span><span><i style="background:{C_AZ}"></i>{az}</span>'
           f'<span><i style="background:{C_ART}"></i>{art}</span>')
    return Markup(f'<div class="rc-wrap">{"".join(p)}<div class="rc-leg">{leg}</div></div>')


def yatay_cubuk(kalemler, renk=C_ANA) -> Markup:
    """[(etiket, tutar_kurus)] için büyükten küçüğe yatay çubuklar; değer sağda yazılır."""
    if not kalemler:
        return Markup("")
    W, rh, L, R = 680, 26, 250, 86
    H = rh * len(kalemler) + 4
    mx = max(v for _, v in kalemler) or 1
    p = [f'<svg class="rc" viewBox="0 0 {W} {H}" role="img" aria-label="Dağılım">']
    for i, (ad, v) in enumerate(kalemler):
        yy = 2 + i * rh
        w = max((W - L - R) * v / mx, 1)
        kisa = ad if len(ad) <= 38 else ad[:36] + "…"
        p.append(f'<text x="{L - 10}" y="{yy + 16}" text-anchor="end" class="rc-lbl">{_esc(kisa)}</text>'
                 f'<path d="M{L},{yy + 4} h{max(w - 4, 0):.1f} q4,0 4,4 v{rh - 16} q0,4 -4,4 h-{max(w - 4, 0):.1f} z" fill="{renk}">'
                 f'<title>{_esc(ad)}: {kurus_str(v)} TL</title></path>'
                 f'<text x="{L + w + 6:.1f}" y="{yy + 16}" class="rc-val">{tl_short(v)}</text>')
    p.append("</svg>")
    return Markup(f'<div class="rc-wrap">{"".join(p)}</div>')


def gosterge(deger, aralik, olcek) -> Markup:
    """Oran göstergesi: genel kabul gören aralık taranır, değer koyu çubukla gösterilir."""
    if deger is None or not olcek:
        return Markup("")
    W, H = 200, 16
    x = lambda v: max(0, min(v, olcek)) / olcek * W  # noqa: E731
    p = [f'<svg class="rc-g" viewBox="0 0 {W} {H}" aria-hidden="true">',
         f'<rect x="0" y="5" width="{W}" height="6" rx="3" fill="var(--gray-soft, #EFE7D6)"/>']
    if aralik:
        a, b = x(aralik[0]), x(aralik[1])
        p.append(f'<rect x="{a:.1f}" y="2" width="{max(b - a, 2):.1f}" height="12" rx="2" fill="{C_BANT}" stroke="var(--gold, #C9A24A)" stroke-width="1"/>')
    p.append(f'<rect x="0" y="6" width="{max(x(deger), 2):.1f}" height="4" rx="2" fill="{C_IKI}"/>'
             f'<circle cx="{min(max(x(deger), 5), W - 5):.1f}" cy="8" r="5" fill="{C_IKI}" stroke="#fff" stroke-width="2"/>')
    p.append("</svg>")
    return Markup("".join(p))


def benford_grafik(sonuc) -> Markup:
    """İlk rakam 1–9 için gerçekleşen oran (çubuk) ve Benford'a göre beklenen oran (nokta ve çizgi)."""
    from ..benford import BEKLENEN
    if sonuc is None:
        return Markup("")
    W, H, L, R, T, B = 680, 250, 44, 8, 16, 40
    import math
    top = math.ceil(max(max(sonuc.gozlenen.values()), max(BEKLENEN.values())) * 10 + 1e-9) / 10
    ih = H - T - B
    y = lambda v: T + (top - v) / top * ih  # noqa: E731
    gw = (W - L - R) / 9
    bw = min(36, gw - 14)
    p = [f'<svg class="rc" viewBox="0 0 {W} {H}" role="img" aria-label="İlk rakam dağılımı ve Benford beklentisi">']
    for i in range(int(round(top * 10)) + 1):
        v = i / 10
        p.append(f'<line x1="{L}" x2="{W - R}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="{C_IZGARA}"/>'
                 f'<text x="{L - 6}" y="{y(v) + 4:.1f}" text-anchor="end" class="rc-ax">%{v * 100:.0f}</text>')
    pts = []
    for d in range(1, 10):
        cx = L + gw * (d - 1) + gw / 2
        g, e = sonuc.gozlenen[d], BEKLENEN[d]
        renk = C_AZ if d in sonuc.fazla else C_ANA
        h = max(y(0) - y(g), 1)
        r4 = min(4, h)
        p.append(f'<path d="M{cx - bw / 2:.1f},{y(0):.1f} V{y(g) + r4:.1f} q0,-{r4:.1f} {r4:.1f},-{r4:.1f} H{cx + bw / 2 - r4:.1f} '
                 f'q{r4:.1f},0 {r4:.1f},{r4:.1f} V{y(0):.1f} Z" fill="{renk}"><title>İlk rakam {d}: gerçekleşen %{g * 100:.1f}, '
                 f'beklenen %{e * 100:.1f} ({sonuc.adet.get(d, 0)} kayıt)</title></path>')
        p.append(f'<text x="{cx:.1f}" y="{H - B + 18}" text-anchor="middle" class="rc-lbl rc-strong">{d}</text>')
        pts.append((cx, y(e)))
    p.append('<polyline fill="none" stroke="var(--ink, #29241D)" stroke-width="2" points="'
             + " ".join(f"{x:.1f},{yy:.1f}" for x, yy in pts) + '"/>')
    for x, yy in pts:
        p.append(f'<circle cx="{x:.1f}" cy="{yy:.1f}" r="4" fill="var(--ink, #29241D)" stroke="#fff" stroke-width="2"/>')
    p.append("</svg>")
    leg = (f'<span><i style="background:{C_ANA}"></i>Gerçekleşen</span><span><i style="background:{C_AZ}"></i>Beklenenden belirgin fazla</span>'
           f'<span><i style="background:var(--ink, #29241D);border-radius:50%"></i>Benford beklentisi</span>')
    return Markup(f'<div class="rc-wrap">{"".join(p)}<div class="rc-leg">{leg}</div></div>')
