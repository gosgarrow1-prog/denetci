"""Bilinen sonuçlu SENTETİK muhasebe verisi üretir (test ve demo için).

Dosya düzeni Luca çıktısını TAKLİT ETMEYE ÇALIŞAN varsayımsal bir düzendir; gerçek Luca
dosyalarıyla doğrulanmadı. Tüm şirket adları, tutarlar ve fiş numaraları uydurmadır.

hatali=True olduğunda eklenen bilinen hatalar ve beklenen bulgular:
- K-09: 100.01 Merkez Kasa 48.250,00 TL alacak bakiyesi (14.09.2026, fiş Y-2026-0912 ile eksiye düşer)
- K-02: Y-2026-0920 fişi yevmiyede denk değil (borç 10.000, alacak 9.000)
- K-05: 102.01 (alacak farkı -1.000), 102.02 ve 770.02 (mükerrer fiş Y-2026-0921 yalnızca yevmiyede)
- K-04: 120 ana hesap satırında borç 1.000 TL fazla
- K-03: 153.01 ve 320.01 bakiyeleri 1.000 TL hatalı (mizan toplamı yine denk)
"""

from collections import defaultdict
from datetime import date
from pathlib import Path

from openpyxl import Workbook

ACCOUNTS = {
    "100": "KASA", "100.01": "Merkez Kasa",
    "102": "BANKALAR", "102.01": "Örnek Bankası", "102.02": "İkinci Banka",
    "120": "ALICILAR", "120.01": "Alıcı A Ltd. Şti.", "120.02": "Alıcı B A.Ş.",
    "153": "TİCARİ MALLAR", "153.01": "Ticari Mallar",
    "191": "İNDİRİLECEK KDV", "191.01": "İndirilecek KDV %20",
    "320": "SATICILAR", "320.01": "Satıcı X Ltd. Şti.",
    "360": "ÖDENECEK VERGİ VE FONLAR", "360.01": "Ödenecek KDV",
    "391": "HESAPLANAN KDV", "391.01": "Hesaplanan KDV %20",
    "500": "SERMAYE", "500.01": "Ödenmiş Sermaye",
    "570": "GEÇMİŞ YILLAR KARLARI", "570.01": "Geçmiş Yıllar Karları",
    "600": "YURTİÇİ SATIŞLAR", "600.01": "Yurtiçi Satışlar",
    "621": "SATILAN TİCARİ MALLAR MALİYETİ", "621.01": "Satılan Ticari Mallar Maliyeti",
    "770": "GENEL YÖNETİM GİDERLERİ", "770.01": "Genel Giderler", "770.02": "Kira Giderleri",
}

OPENING = {  # TL; pozitif = borç, negatif = alacak
    "100.01": 12_400, "102.01": 350_000, "102.02": 50_000, "120.01": 180_000, "120.02": 95_000,
    "153.01": 240_000, "320.01": -210_000, "500.01": -600_000, "570.01": -117_400,
}

BASE_ENTRIES = [
    ("Y-2026-0701", date(2026, 7, 5), "Peşin satış", [("100.01", 60_000, 0), ("600.01", 0, 50_000), ("391.01", 0, 10_000)]),
    ("Y-2026-0702", date(2026, 7, 12), "Mal alışı", [("153.01", 150_000, 0), ("191.01", 30_000, 0), ("320.01", 0, 180_000)]),
    ("Y-2026-0703", date(2026, 7, 20), "Peşin satış", [("100.01", 66_000, 0), ("600.01", 0, 55_000), ("391.01", 0, 11_000)]),
    ("Y-2026-0801", date(2026, 8, 2), "Alıcıdan nakit tahsilat", [("100.01", 60_350, 0), ("120.01", 0, 60_350)]),
    ("Y-2026-0802", date(2026, 8, 10), "Vadeli satış", [("120.02", 240_000, 0), ("600.01", 0, 200_000), ("391.01", 0, 40_000)]),
    ("Y-2026-0803", date(2026, 8, 15), "Kasadan bankaya", [("102.01", 157_000, 0), ("100.01", 0, 157_000)]),
    ("Y-2026-0804", date(2026, 8, 25), "Satıcıya banka ödemesi", [("320.01", 100_000, 0), ("102.01", 0, 100_000)]),
    ("Y-2026-0805", date(2026, 8, 28), "Alıcıdan banka tahsilatı", [("102.01", 95_000, 0), ("120.01", 0, 95_000)]),
    ("Y-2026-0901", date(2026, 9, 1), "Genel giderler nakit", [("770.01", 25_000, 0), ("191.01", 5_000, 0), ("100.01", 0, 30_000)]),
    ("Y-2026-0902", date(2026, 9, 1), "Eylül kirası", [("770.02", 18_000, 0), ("102.01", 0, 18_000)]),
]
CLOSING_ENTRIES = [
    ("Y-2026-0930", date(2026, 9, 30), "Satılan malın maliyeti", [("621.01", 180_000, 0), ("153.01", 0, 180_000)]),
    # Dönem sonu KDV mahsubu (basitlik için üç ay tek fişte): 391 = 61.000, 191 = 35.000, fark ödenecek.
    ("Y-2026-0931", date(2026, 9, 30), "KDV mahsubu", [("391.01", 61_000, 0), ("191.01", 0, 35_000), ("360.01", 0, 26_000)]),
]
ERROR_COMMON = [  # hem yevmiyede hem mizanda
    ("Y-2026-0912", date(2026, 9, 14), "Satıcıya nakit ödeme", [("320.01", 60_000, 0), ("100.01", 0, 60_000)]),
    ("Y-2026-0920", date(2026, 9, 20), "Genel gider banka ödemesi", [("770.01", 10_000, 0), ("102.01", 0, 10_000)]),
]
ERROR_YEVMIYE_ONLY = [
    ("Y-2026-0921", date(2026, 9, 21), "Ekim kirası avansı", [("770.02", 2_000, 0), ("102.02", 0, 2_000)]),
]

PERIOD = (date(2026, 7, 1), date(2026, 9, 30))


def tr_amount(tl: float) -> str:
    if not tl:
        return ""
    lira = int(round(tl * 100))
    s = f"{lira // 100:,}".replace(",", ".")
    return f"{s},{lira % 100:02d}"


def build(hatali: bool, mahsup: bool = True):
    closing = CLOSING_ENTRIES if mahsup else [e for e in CLOSING_ENTRIES if e[0] != "Y-2026-0931"]
    mizan_entries = BASE_ENTRIES + (ERROR_COMMON if hatali else []) + closing
    yev_entries = list(mizan_entries) + (ERROR_YEVMIYE_ONLY if hatali else [])
    if hatali:  # Y-2026-0920 yevmiyede yanlış: alacak 9.000
        yev_entries = [(f, d, a, [(c, b, 9_000) if (f == "Y-2026-0920" and al) else (c, b, al) for c, b, al in ls])
                       for f, d, a, ls in yev_entries]
    yev_entries.sort(key=lambda e: (e[1], e[0]))
    return mizan_entries, yev_entries


def write_mizan(path: Path, entries, hatali: bool):
    mov_b, mov_a = defaultdict(int), defaultdict(int)
    for _, _, _, lines in entries:
        for code, b, a in lines:
            mov_b[code] += b
            mov_a[code] += a
    leaves = sorted(c for c in ACCOUNTS if "." in c)
    rows = {}
    for c in leaves:
        op = OPENING.get(c, 0)
        ob, oa = max(op, 0), max(-op, 0)
        net = op + mov_b[c] - mov_a[c]
        rows[c] = [ob, oa, mov_b[c], mov_a[c], max(net, 0), max(-net, 0)]
    if hatali:
        rows["153.01"][4] += 1_000
        rows["320.01"][5] += 1_000

    wb = Workbook()
    ws = wb.active
    ws.title = "Mizan"
    ws.append(["DEMO — SENTETİK VERİ"])
    ws.append(["ÖRNEK TİCARET A.Ş. (SENTETİK)"])
    ws.append(["MİZAN  01.07.2026 - 30.09.2026"])
    ws.append([])
    ws.append(["Hesap Kodu", "Hesap Adı", "Açılış Borç", "Açılış Alacak", "Borç", "Alacak", "Borç Bakiye", "Alacak Bakiye"])
    grand = [0] * 6
    current_class = None
    class_tot = [0] * 6
    for ana in sorted(c for c in ACCOUNTS if "." not in c):
        if current_class and ana[0] != current_class:
            ws.append([current_class, f"{current_class} SINIFI TOPLAMI"] + [tr_amount(v) for v in class_tot])
            class_tot = [0] * 6
        current_class = ana[0]
        kids = [c for c in leaves if c.startswith(ana + ".")]
        sums = [sum(rows[k][i] for k in kids) for i in range(6)]
        if hatali and ana == "120":
            sums[2] += 1_000
        ws.append([ana, ACCOUNTS[ana]] + [tr_amount(v) for v in sums])
        for k in kids:
            ws.append([k, ACCOUNTS[k]] + [tr_amount(v) for v in rows[k]])
            for i in range(6):
                grand[i] += rows[k][i]
                class_tot[i] += rows[k][i]
    ws.append([current_class, f"{current_class} SINIFI TOPLAMI"] + [tr_amount(v) for v in class_tot])
    ws.append([None, "GENEL TOPLAM"] + [tr_amount(v) for v in grand])
    wb.save(path)


def write_yevmiye(path: Path, entries):
    wb = Workbook()
    ws = wb.active
    ws.title = "Yevmiye"
    header = ["Fiş No", "Tarih", "Hesap Kodu", "Hesap Adı", "Açıklama", "Borç", "Alacak"]
    ws.append(["DEMO — SENTETİK VERİ"])
    ws.append(["ÖRNEK TİCARET A.Ş. (SENTETİK) — YEVMİYE DEFTERİ 01.07.2026 - 30.09.2026"])
    ws.append([])
    ws.append(header)
    page_b = page_a = 0
    on_page = 0
    for fis, d, desc, lines in entries:
        for i, (code, b, a) in enumerate(lines):
            if on_page == 12:  # sayfa sonu: nakli yekün ve tekrar eden başlık
                ws.append([None, None, None, None, "Nakli Yekün", page_b, page_a])
                ws.append(header)
                on_page = 0
            # Fiş no ve tarih yalnızca fişin ilk satırında yazılır.
            ws.append([fis if i == 0 else None, d.strftime("%d.%m.%Y") if i == 0 else None, code, ACCOUNTS[code], desc,
                       float(b) if b else None, float(a) if a else None])
            page_b += b
            page_a += a
            on_page += 1
    ws.append([None, None, None, None, "Genel Toplam", page_b, page_a])
    wb.save(path)


def generate(folder: Path, hatali: bool = True) -> dict[str, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    mizan_entries, yev_entries = build(hatali)
    tag = "hatali" if hatali else "temiz"
    mp = folder / f"DEMO_mizan_2026Q3_{tag}.xlsx"
    yp = folder / f"DEMO_yevmiye_2026Q3_{tag}.xlsx"
    write_mizan(mp, mizan_entries, hatali)
    write_yevmiye(yp, yev_entries)
    return {"mizan": mp, "yevmiye": yp}
