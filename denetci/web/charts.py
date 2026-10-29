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
