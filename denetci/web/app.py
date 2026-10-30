"""Web arayüzü. Yalnızca 127.0.0.1 üzerinde çalışacak şekilde tasarlandı (tek kullanıcı, yerel kurulum)."""

import secrets
from datetime import date, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from itsdangerous import BadSignature, URLSafeSerializer
from starlette.middleware.sessions import SessionMiddleware

from .. import db
from ..auth import LoginThrottle, verify_password, verify_totp
from ..checks.engine import RELIABILITY_LABELS, NoData, run_analysis
from ..checks.registry import ALL_RULES
from ..config import MAX_YUKLEME_BAYT, data_dir, telefon_kodu
from ..importer.load import DuplicateFile, ImportError_, confirm, create_draft, preview
from ..importer.luca import PROFILE_LABELS
from ..importer.tables import DOC_FIELDS, DOC_LABELS, FIELD_DEFS, REQUIRED
from ..models import (AnalysisRun, AuditLog, Client, Declaration, Finding, FindingCase, FindingEvidence, ImportBatch, Period, RawRow,
                      SourceFile, User)
from ..parsing import ParseError, kurus_str, parse_amount
from ..finance import SINIFLAR, summarize
from ..checks.engine import build_context
from ..importer.clients import ClientRow, ListError, mark_existing, parse_client_list
from ..importer.tables import UnsupportedFile
from ..importer.ingest import find_or_create_period, ingest, peek
from ..importer.beyanname import donem_adi as beyan_donem_adi
from ..importer.firma import en_iyi as firma_en_iyi, eslesir as firma_eslesir
from .. import beyannameler
from ..analyses import build as build_analyses, kdv_amounts
from ..gecici import ALANLAR as GV_ALANLAR, AYLAR_DONEM as GV_DONEM_ADI, MALIYET_YONTEMLERI as GV_MALIYET, hesapla as gv_hesapla
from ..parametreler import GECICI_VERGI
from ..oneriler import ONCELIK_ADI, build as oneriler_build
from .charts import balance_bars, tl_short

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))
templates.env.filters["tl"] = kurus_str
templates.env.globals["RELIABILITY_LABELS"] = RELIABILITY_LABELS
templates.env.globals["DOC_LABELS"] = DOC_LABELS
templates.env.globals["SINIFLAR"] = SINIFLAR
templates.env.globals["tl_short"] = tl_short
# Stil dosyası değişince tarayıcı eskisini önbellekten kullanmasın.
templates.env.globals["static_v"] = str(int((HERE / "static" / "style.css").stat().st_mtime))
AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]


def donem_adi(p) -> str:
    if p is None:
        return ""
    if p.tur == "aylik":
        return f"{AYLAR[p.baslangic.month - 1]} {p.baslangic.year}"
    if p.tur == "yillik":
        return f"{p.baslangic.year} yılı"
    if p.baslangic.day == 1 and p.baslangic.year == p.bitis.year and (p.bitis + timedelta(days=1)).day == 1:
        return f"{AYLAR[p.baslangic.month - 1]}–{AYLAR[p.bitis.month - 1]} {p.bitis.year}"
    if p.baslangic.month == p.bitis.month and p.baslangic.year == p.bitis.year and p.baslangic.day == 1:
        return f"{AYLAR[p.baslangic.month - 1]} {p.baslangic.year}"
    return f"{p.baslangic:%d.%m.%Y} – {p.bitis:%d.%m.%Y}"


def tl0(kurus) -> str:
    """Kuruşsuz TL: 5.005.585"""
    if kurus is None:
        return "—"
    v = round(kurus / 100)
    return ("−" if v < 0 else "") + f"{abs(v):,}".replace(",", ".")


templates.env.filters["donem_adi"] = donem_adi
templates.env.filters["tl0"] = tl0
templates.env.filters["sayi"] = lambda n: f"{int(n or 0):,}".replace(",", ".")
templates.env.globals["PROFILE_LABELS"] = PROFILE_LABELS
templates.env.globals["beyan_donem"] = beyan_donem_adi
templates.env.globals["ONCELIK_ADI"] = ONCELIK_ADI
templates.env.globals["ROW_CLASS_LABELS"] = {
    "kayit": "✓ kayıt", "ara_toplam": "Σ ara toplam", "baslik": "≡ başlık", "bilgi": "i bilgi", "bos": "boş",
    "belirsiz": "? belirsiz", "hatali": "✗ hatalı", "ust_hesap": "↑ üst hesap", "grup": "≡ hesap grubu",
    "acilis": "↦ nakli yekün"}
templates.env.globals["SUMMARY_LABELS"] = {
    "kayit": "Aktarılan kayıt", "ara_toplam": "Ayıklanan ara toplam", "baslik": "Başlık", "bilgi": "Bilgi satırı",
    "bos": "Boş", "belirsiz": "Belirsiz (alınmadı)", "hatali": "Hatalı (alınmadı)",
    "ust_hesap": "Üst hesap satırı (toplanmadı)", "grup": "Hesap grubu başlığı", "acilis": "Nakli yekün (önceki dönem)"}

STATUS_LABELS = {"yeni": "Yeni", "inceleniyor": "İnceleniyor", "aciklandi": "Açıklandı"}
templates.env.globals["STATUS_LABELS"] = STATUS_LABELS
KIND_LABELS = {"A": "Aritmetik/veri tutarsızlığı", "K": "Kurala dayalı olası uygunsuzluk", "I": "İstatistiksel anomali",
               "D": "Ek belge / mesleki değerlendirme"}
templates.env.globals["KIND_LABELS"] = KIND_LABELS
SEVERITY_ORDER = {"Kritik": 0, "Yüksek": 1, "Orta": 2, "Düşük": 3}

throttle = LoginThrottle()


def _secret_key() -> str:
    path = data_dir() / ".oturum_anahtari"
    if not path.exists():
        path.write_text(secrets.token_hex(32))
        path.chmod(0o600)
    return path.read_text().strip()


class LoginRequired(Exception):
    pass


def get_session():
    s = db.SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def current_user(request: Request, s=Depends(get_session)) -> User:
    uid = request.session.get("uid")
    user = s.get(User, uid) if uid else None
    if not user:
        raise LoginRequired()
    return user


def csrf_token(request: Request) -> str:
    tok = request.session.get("csrf")
    if not tok:
        tok = secrets.token_urlsafe(24)
        request.session["csrf"] = tok
    return tok


async def form_checked(request: Request) -> dict:
    form = await request.form()
    expected = request.session.get("csrf", "")
    if not expected or not secrets.compare_digest(str(form.get("csrf", "")), expected):
        raise HTTPException(403, "Geçersiz form anahtarı. Sayfayı yenileyip tekrar deneyin.")
    return form


def render(request: Request, name: str, **ctx):
    ctx["request"] = request
    ctx["csrf"] = csrf_token(request)
    ctx.setdefault("telefon_kodu", telefon_kodu())
    if request.session.get("uid") and "shell" not in ctx:
        ctx["shell"] = _shell(request.session["uid"], ctx.get("c"), ctx.get("p"))
    return templates.TemplateResponse(request, name, ctx)


def _shell(uid: int, c, p) -> dict:
    """Sol menü ve üst çubuk için: mükellef listesi, dönemler, son analiz ve açık bulgu sayısı."""
    with db.SessionLocal() as s:
        user = s.get(User, uid)
        sh = {"username": user.username if user else "", "clients": list(s.execute(
            select(Client.id, Client.unvan).order_by(Client.is_demo, Client.unvan))), "c": c, "p": None, "periods": [],
            "run": None, "badge": 0}
        if c is None:
            return sh
        sh["periods"] = list(s.scalars(select(Period).where(Period.client_id == c.id)
                                       .order_by(Period.bitis.desc(), Period.baslangic)))
        pid = getattr(p, "id", None) or (sh["periods"][0].id if sh["periods"] else None)
        dolu = set(s.scalars(select(ImportBatch.period_id).where(ImportBatch.client_id == c.id,
                                                                 ImportBatch.status == "onaylandi")))
        sh["donem_gruplari"] = donem_gruplari(sh["periods"], dolu, pid, _today())
        sh["p"] = next((pp for pp in sh["periods"] if pp.id == pid), None)
        if sh["p"]:
            run = s.scalar(select(AnalysisRun).where(AnalysisRun.client_id == c.id, AnalysisRun.period_id == pid)
                           .order_by(AnalysisRun.id.desc()))
            if run:
                closed = {cs.fingerprint for cs in s.scalars(select(FindingCase).where(
                    FindingCase.client_id == c.id, FindingCase.status == "aciklandi"))}
                sh["run"] = run
                sh["badge"] = sum(1 for f in s.scalars(select(Finding).where(Finding.run_id == run.id))
                                  if f.fingerprint not in closed)
        s.expunge_all()
        return sh


def _today() -> date:
    return date.today()


def _ay_sonu(y: int, m: int) -> date:
    return (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))


def donem_gruplari(periods, dolu: set, pid, bugun: date) -> list:
    """Dönem seçicisi: her yıl için aylık ve 3 aylık dönemler (bugüne kadar), sonra diğer dönemler.
    Seçenek: {"bas", "bit", "ad", "id" (varsa), "dolu", "secili"}."""
    by_range = {(pp.baslangic, pp.bitis): pp for pp in periods}
    years = sorted({pp.bitis.year for pp in periods} | {bugun.year}, reverse=True)
    groups, used = [], set()

    def opt(bas, bit, ad):
        pp = by_range.get((bas, bit))
        if pp:
            used.add(pp.id)
        return {"bas": bas.isoformat(), "bit": bit.isoformat(), "ad": ad, "id": pp.id if pp else None,
                "dolu": bool(pp and pp.id in dolu), "secili": bool(pp and pp.id == pid)}

    for y in years:
        aylar = [opt(date(y, m, 1), _ay_sonu(y, m), f"{AYLAR[m - 1]} {y}") for m in range(12, 0, -1) if date(y, m, 1) <= bugun]
        ceyrek = [opt(date(y, 3 * q - 2, 1), _ay_sonu(y, 3 * q), f"{q}. dönem: {AYLAR[3 * q - 3]}–{AYLAR[3 * q - 1]} {y}")
                  for q in range(4, 0, -1) if date(y, 3 * q - 2, 1) <= bugun]
        groups.append((f"Aylık · {y}", aylar))
        groups.append((f"3 aylık · {y}", ceyrek))
    diger = [{"bas": pp.baslangic.isoformat(), "bit": pp.bitis.isoformat(), "ad": donem_adi(pp), "id": pp.id,
              "dolu": pp.id in dolu, "secili": pp.id == pid} for pp in periods if pp.id not in used]
    if diger:
        groups.append(("Diğer dönemler", diger))
    return [(g, o) for g, o in groups if o]


def owned(s, model, obj_id: int, client_id: int):
    """Mükellef yalıtımı: nesne bu mükellefe ait değilse 404."""
    obj = s.get(model, obj_id)
    if obj is None or getattr(obj, "client_id", None) != client_id:
        raise HTTPException(404, "Bulunamadı")
    return obj


def audit(s, user, action, client_id=None, detail=None):
    s.add(AuditLog(user_id=user.id if user else None, client_id=client_id, action=action, detail=detail))


def create_app() -> FastAPI:
    db.init_db()
    app = FastAPI(title="Denetçi", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(SessionMiddleware, secret_key=_secret_key(), same_site="strict", max_age=8 * 3600,
                       session_cookie="denetci_oturum")
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    @app.exception_handler(LoginRequired)
    async def _login_redirect(request, exc):
        return _redirect(None, "/giris")

    # ---- Giriş --------------------------------------------------------------

    @app.get("/giris", response_class=HTMLResponse)
    def login_form(request: Request):
        return render(request, "login.html", error=None)

    @app.post("/giris")
    async def login(request: Request, s=Depends(get_session)):
        form = await form_checked(request)
        username = str(form.get("kullanici", "")).strip()
        if throttle.locked(username):
            return render(request, "login.html", error="Çok fazla hatalı deneme. 5 dakika sonra tekrar deneyin.")
        user = s.scalar(select(User).where(User.username == username))
        ok = user and verify_password(str(form.get("parola", "")), user.password_hash) and \
            (not telefon_kodu() or verify_totp(user.totp_secret, str(form.get("kod", ""))))
        if not ok:
            throttle.fail(username)
            audit(s, None, "giris_basarisiz", detail=username[:64])
            return render(request, "login.html", error="Kullanıcı adı, parola veya doğrulama kodu hatalı."
                          if telefon_kodu() else "Kullanıcı adı veya parola hatalı.")
        throttle.reset(username)
        request.session.clear()
        request.session["uid"] = user.id
        request.session["csrf"] = secrets.token_urlsafe(24)
        audit(s, user, "giris")
        return _redirect(s, "/")

    @app.post("/cikis")
    async def logout(request: Request):
        await form_checked(request)
        request.session.clear()
        return _redirect(None, "/giris")

    # ---- Portföy ve mükellef -----------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def portfolio(request: Request, user=Depends(current_user), s=Depends(get_session)):
        rows = []
        for c in s.scalars(select(Client).order_by(Client.unvan)):
            period = s.scalar(select(Period).where(Period.client_id == c.id).order_by(Period.bitis.desc()))
            run = s.scalar(select(AnalysisRun).where(AnalysisRun.client_id == c.id, AnalysisRun.period_id == period.id)
                           .order_by(AnalysisRun.id.desc())) if period else None
            sev = {"Kritik": 0, "Yüksek": 0, "Orta": 0}
            if run:
                for sv, n in s.execute(select(Finding.severity, func.count()).where(Finding.run_id == run.id)
                                       .group_by(Finding.severity)):
                    sev[sv] = n
            rows.append({"c": c, "run": run, "period": period, "sev": sev, "open_high": sev["Kritik"] + sev["Yüksek"],
                         "rel": run.reliability if run else None})
        rows.sort(key=lambda r: (-r["open_high"], r["c"].is_demo, r["c"].unvan))
        return render(request, "portfolio.html", active="mukellefler", rows=rows, has_demo=any(r["c"].is_demo for r in rows))

    @app.post("/mukellef/yeni")
    async def client_create(request: Request, user=Depends(current_user), s=Depends(get_session)):
        form = await form_checked(request)
        unvan = str(form.get("unvan", "")).strip()
        if not unvan:
            raise HTTPException(400, "Unvan gerekli")
        c = Client(unvan=unvan[:256], sirket_turu=str(form.get("sirket_turu", ""))[:64] or None,
                   faaliyet=str(form.get("faaliyet", ""))[:256] or None, vkn=str(form.get("vkn", "")).strip()[:16] or None)
        s.add(c)
        s.flush()
        audit(s, user, "mukellef_olustur", c.id)
        return _redirect(s, f"/mukellef/{c.id}")

    # ---- Mükellef listesi (Luca) --------------------------------------------

    @app.post("/mukellef/liste", response_class=HTMLResponse)
    async def client_list_upload(request: Request, user=Depends(current_user), s=Depends(get_session)):
        form = await form_checked(request)
        up = form.get("dosya")
        if not up or not getattr(up, "filename", ""):
            return _redirect(s, "/")
        content = await up.read()
        if len(content) > MAX_YUKLEME_BAYT:
            return render(request, "error.html", title="Dosya çok büyük", message="Liste 20 MB'tan küçük olmalı.", back="/")
        try:
            rows = parse_client_list(up.filename, content)
        except (ListError, UnsupportedFile) as exc:
            return render(request, "error.html", title="Liste okunamadı", message=str(exc), back="/")
        except Exception:
            return render(request, "error.html", title="Liste okunamadı", message="Dosya açılamadı. Luca'dan .xlsx olarak yeniden alın.", back="/")
        mark_existing(rows, list(s.scalars(select(Client))))
        data = _signer().dumps([r.__dict__ for r in rows])
        return render(request, "client_list.html", rows=rows, filename=up.filename, data=data)

    @app.post("/mukellef/liste/onayla")
    async def client_list_confirm(request: Request, user=Depends(current_user), s=Depends(get_session)):
        form = await form_checked(request)
        try:
            rows = _signer().loads(str(form.get("veri", "")))
        except BadSignature:
            raise HTTPException(400, "Liste verisi bozulmuş; listeyi yeniden yükleyin.")
        chosen = {int(v) for v in form.getlist("sec") if str(v).isdigit()}
        existing = list(s.scalars(select(Client)))
        added = 0
        for i, r in enumerate(rows):
            if i not in chosen or r["durum"] != "yeni":
                continue
            # Onay ile yükleme arasında eklenmiş olabilir; tekrar kontrol et.
            if mark_existing([ClientRow(**r)], existing)[0].durum != "yeni":
                continue
            c = Client(unvan=r["unvan"], vkn=r["vkn"], vergi_dairesi=r["vergi_dairesi"], luca_kodu=r["luca_kodu"],
                       sirket_turu=(r["sirket_turu"] or None) and r["sirket_turu"][:64], faaliyet=r["faaliyet"])
            s.add(c)
            existing.append(c)
            added += 1
        s.flush()
        audit(s, user, "mukellef_listesi", None, f"{added} mükellef eklendi")
        return _redirect(s, "/")

    # ---- DEMO -------------------------------------------------------------------

    @app.post("/demo")
    async def demo(request: Request, user=Depends(current_user), s=Depends(get_session)):
        await form_checked(request)
        c = s.scalar(select(Client).where(Client.is_demo.is_(True)))
        if not c:
            from .. import synthetic_luca
            c = Client(unvan="DEMO — Örnek Ticaret A.Ş. (SENTETİK)", sirket_turu="A.Ş.", faaliyet="Toptan ticaret (sentetik)",
                       is_demo=True)
            s.add(c)
            s.flush()
            files = synthetic_luca.generate(data_dir() / "demo_dosyalari", hatali=True, mizan=True)
            periods = set()
            for path in files.values():
                o = ingest(s, c, path.name, path.read_bytes(), None)
                if o.period:
                    periods.add(o.period)
            for p in periods:
                run_analysis(s, c, p)
        return _redirect(s, f"/mukellef/{c.id}")

    # ---- Mükellef paneli ----------------------------------------------------------

    def _period_of(s, cid, donem):
        periods = list(s.scalars(select(Period).where(Period.client_id == cid).order_by(Period.bitis.desc(), Period.baslangic)))
        p = owned(s, Period, donem, cid) if donem else (periods[0] if periods else None)
        return periods, p

    def _view(s, c, p, request) -> dict:
        """Bir dönemin panelde gösterilen her şeyi: belgeler, analiz, bulgular, finansal özet, analiz gereksinimleri."""
        periods, _ = _period_of(s, c.id, None)
        v = {"c": c, "p": p, "periods": periods, "flash": request.session.pop("flash", None), "batches": [], "files": {},
             "latest": {}, "run": None, "findings": [], "cases": {}, "sev_counts": {}, "fin": None, "prev_fin": None,
             "prev_ratios": {}, "results": [], "kdv": {}}
        ctx = None
        prev_exists = False
        if p:
            batches = list(s.scalars(select(ImportBatch).where(ImportBatch.client_id == c.id, ImportBatch.period_id == p.id,
                                                               ImportBatch.status != "iptal").order_by(ImportBatch.id.desc())))
            v["batches"] = batches
            v["files"] = {b.source_file_id: s.get(SourceFile, b.source_file_id) for b in batches}
            run = s.scalar(select(AnalysisRun).where(AnalysisRun.client_id == c.id, AnalysisRun.period_id == p.id)
                           .order_by(AnalysisRun.id.desc()))
            findings = list(s.scalars(select(Finding).where(Finding.run_id == run.id))) if run else []
            findings.sort(key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), -(abs(f.amount or 0))))
            cases = {cs.fingerprint: cs for cs in s.scalars(select(FindingCase).where(FindingCase.client_id == c.id))}
            open_f = [f for f in findings if f.fingerprint not in cases or cases[f.fingerprint].status != "aciklandi"]
            ctx, latest = build_context(s, c, p)
            v.update(run=run, findings=open_f, cases=cases, latest={k: b for k, b in latest.items() if b},
                     sev_counts={k: sum(1 for f in open_f if f.severity == k) for k in ("Kritik", "Yüksek", "Orta", "Düşük")},
                     results=run.rule_results if run else [])
            if ctx:
                earlier = s.scalars(select(Period).where(Period.client_id == c.id, Period.bitis < p.baslangic)
                                    .order_by(Period.bitis.desc())).all()
                for pp in earlier:
                    pctx, _ = build_context(s, c, pp)
                    if pctx:
                        if not ctx.previous:
                            ctx.previous.append(pctx)
                        if v["prev_fin"] is None:
                            v["prev_fin"] = summarize(pctx)
                        prev_exists = True
                        break
                v["fin"] = summarize(ctx)
                v["kdv"] = kdv_amounts(ctx)
            if v["prev_fin"]:
                v["prev_ratios"] = {r.key: r for r in v["prev_fin"].oranlar}
        v["decls"] = beyannameler.aktif(s, c.id)
        v["decl_files"] = {d.source_file_id: s.get(SourceFile, d.source_file_id) for d in v["decls"]}
        v["analyses"] = build_analyses(p, v["latest"], ctx, v["fin"], prev_exists, v["decls"])
        v["ratios"] = {r.key: r for r in v["fin"].oranlar} if v["fin"] else {}
        return v

    def _alerts(v, c) -> list:
        """Dikkat gerektirenler: açık bulgular ve analizler için eksik belgeler."""
        out = []
        level = {"Kritik": "onemli", "Yüksek": "onemli", "Orta": "dikkat", "Düşük": "bilgi"}
        for f in v["findings"]:
            out.append({"level": level.get(f.severity, "bilgi"), "text": f.title, "detail": f.rule_id,
                        "amount": f.amount, "href": f"/mukellef/{c.id}/bulgu/{f.id}", "action": "İncele"})
        q = f"?donem={v['p'].id}" if v["p"] else ""
        for a in v["analyses"]:
            if a.yakinda:
                continue
            for n in a.missing:
                out.append({"level": "bilgi", "text": f"{a.title}: {n.label} eksik.", "detail": n.hint, "amount": None,
                            "href": f"/mukellef/{c.id}/{a.link}{q}", "action": "Tamamla"})
        return out

    @app.get("/mukellef/{cid}", response_class=HTMLResponse)
    def client_page(cid: int, request: Request, user=Depends(current_user), s=Depends(get_session), donem: int | None = None):
        c = s.get(Client, cid) or _404()
        _, p = _period_of(s, cid, donem)
        v = _view(s, c, p, request)
        v["alerts"] = _alerts(v, c)
        return render(request, "client.html", active="panel", **v)

    @app.get("/mukellef/{cid}/belgeler", response_class=HTMLResponse)
    def documents_page(cid: int, request: Request, user=Depends(current_user), s=Depends(get_session), donem: int | None = None):
        c = s.get(Client, cid) or _404()
        _, p = _period_of(s, cid, donem)
        return render(request, "documents.html", active="belgeler", **_view(s, c, p, request))

    @app.get("/mukellef/{cid}/finansal", response_class=HTMLResponse)
    def finance_page(cid: int, request: Request, user=Depends(current_user), s=Depends(get_session), donem: int | None = None):
        c = s.get(Client, cid) or _404()
        _, p = _period_of(s, cid, donem)
        v = _view(s, c, p, request)
        fin = v["fin"]
        v["balance_chart"] = None
        if fin and fin.has_balance:
            b = fin.bilanco
            v["balance_chart"] = balance_bars(
                [("Dönen varlıklar", b["1"], "--s1"), ("Duran varlıklar", b["2"], "--s2")],
                [("Kısa vadeli yabancı kaynaklar", b["3"], "--s3"), ("Uzun vadeli yabancı kaynaklar", b["4"], "--s4"),
                 ("Özkaynaklar + dönem sonucu", b["5"] + (fin.donem_sonucu_kapanmamis or 0), "--s5")])
        v["analysis"] = next(a for a in v["analyses"] if a.key == "finansal")
        gv = gv_hesapla(build_context(s, c, p)[0], p, c, v["decls"]) if p else None
        v["oneriler"] = oneriler_build(v["fin"], v["prev_fin"], v["findings"], gv, p)
        return render(request, "finance.html", active="finansal", **v)

    @app.get("/mukellef/{cid}/rapor", response_class=HTMLResponse)
    def report_page(cid: int, request: Request, user=Depends(current_user), s=Depends(get_session), donem: int | None = None):
        c = s.get(Client, cid) or _404()
        _, p = _period_of(s, cid, donem)
        if not p:
            return _redirect(s, f"/mukellef/{cid}")
        v = _view(s, c, p, request)
        v["alerts"] = _alerts(v, c)
        v["now"] = _now()
        v["gv"] = gv_hesapla(build_context(s, c, p)[0], p, c, v["decls"])
        v["GV_ALANLAR"] = GV_ALANLAR
        v["oneriler"] = oneriler_build(v["fin"], v["prev_fin"], v["findings"], v["gv"], p)
        return render(request, "report.html", active="rapor", **v)

    @app.get("/mukellef/{cid}/gecici-vergi", response_class=HTMLResponse)
    def gv_page(cid: int, request: Request, user=Depends(current_user), s=Depends(get_session), donem: int | None = None):
        c = s.get(Client, cid) or _404()
        _, p = _period_of(s, cid, donem)
        v = _view(s, c, p, request)
        a = next(x for x in v["analyses"] if x.key == "gecici")
        ctx = build_context(s, c, p)[0] if p else None
        v.update(analysis=a, gv=gv_hesapla(ctx, p, c, v["decls"]) if p else None, GV_ALANLAR=GV_ALANLAR, GV_PARAM=GECICI_VERGI,
                 GV_DONEM=GV_DONEM_ADI, GV_MALIYET=GV_MALIYET,
                 a_results=[r for r in v["results"] if r["id"] in a.rules],
                 a_findings=[f for f in v["findings"] if f.rule_id in a.rules])
        return render(request, "gecici.html", active="gecici", **v)

    @app.post("/mukellef/{cid}/donem/{pid}/gecici-vergi")
    async def gv_save(cid: int, pid: int, request: Request, user=Depends(current_user), s=Depends(get_session)):
        owned(s, Period, pid, cid)
        p = s.get(Period, pid)
        form = await form_checked(request)
        back = f"/mukellef/{cid}/gecici-vergi?donem={pid}"
        if (r := _guard(request, p, cid, back)):
            return r
        gv, bad = {"kaydedildi": True}, []
        for key, label, _ in GV_ALANLAR:
            raw = str(form.get(key, "")).replace("₺", "").replace("TL", "").strip()
            try:
                val = parse_amount(raw, "tr") if raw else 0
            except ParseError:
                val = None
            if val is None or val < 0:
                bad.append(label)
            else:
                gv[key] = val
        tur = str(form.get("tur", ""))
        if tur in ("kurum", "sahis"):
            gv["tur"] = tur
        yontem = str(form.get("maliyet", "otomatik"))
        gv["maliyet"] = yontem if yontem in GV_MALIYET else "otomatik"
        bko = str(form.get("brut_kar_orani", "")).replace("%", "").replace(",", ".").strip()
        if bko:
            try:
                gv["brut_kar_orani"] = float(bko) / 100
            except ValueError:
                bad.append("Brüt kâr oranı")
        stok = str(form.get("donem_sonu_stok", "")).replace("₺", "").replace("TL", "").strip()
        if stok:
            try:
                gv["donem_sonu_stok"] = parse_amount(stok, "tr")
            except ParseError:
                bad.append("Dönem sonu stok")
        oran_raw = str(form.get("oran", "")).replace("%", "").replace(",", ".").strip()
        if oran_raw:
            try:
                o = float(oran_raw)
                gv["oran"] = o / 100 if o > 1 else o
            except ValueError:
                bad.append("Oran")
        if bad:
            request.session["flash"] = [{"filename": "Geçici vergi", "status": "hata", "message": "Okunamayan tutar: " + ", ".join(bad)}]
            return _redirect(s, back)
        p.gv = gv
        audit(s, user, "gecici_vergi_girdi", cid, f"period={pid}")
        request.session["flash"] = [{"filename": "Geçici vergi", "status": "aktarildi", "message": "Bilgiler kaydedildi, hesaplama güncellendi."}]
        return _redirect(s, back)

    @app.get("/mukellef/{cid}/donem-sec")
    def period_select(cid: int, bas: str, bit: str, geri: str = "", user=Depends(current_user), s=Depends(get_session)):
        """Seçicideki henüz olmayan bir ay veya 3 aylık dönemi açar (boş dönem oluşturur) ve o sayfaya döner."""
        c = s.get(Client, cid) or _404()
        try:
            b, e = date.fromisoformat(bas), date.fromisoformat(bit)
        except ValueError:
            raise HTTPException(400, "Tarihler geçersiz")
        if e < b or (e - b).days > 400:
            raise HTTPException(400, "Dönem geçersiz")
        p = find_or_create_period(s, c, b, e)
        base = geri if geri.startswith(f"/mukellef/{cid}") and "?" not in geri and "//" not in geri else f"/mukellef/{cid}"
        return _redirect(s, f"{base}?donem={p.id}")

    @app.get("/mukellef/{cid}/{key}", response_class=HTMLResponse)
    def analysis_page(cid: int, key: str, request: Request, user=Depends(current_user), s=Depends(get_session),
                      donem: int | None = None):
        keys = {"kdv": "kdv", "mutabakat": "mutabakat"}
        if key not in keys:
            _404()
        c = s.get(Client, cid) or _404()
        _, p = _period_of(s, cid, donem)
        v = _view(s, c, p, request)
        a = next(x for x in v["analyses"] if x.key == keys[key])
        v["analysis"] = a
        v["a_results"] = [r for r in v["results"] if r["id"] in a.rules]
        v["a_findings"] = [f for f in v["findings"] if f.rule_id in a.rules]
        if p and key == "kdv" and beyannameler.tek_ay(p.baslangic, p.bitis):
            v["kdv_onceki"] = beyannameler.onceki_ay_kdv(v["decls"], p.baslangic)
            v["kdv_bu"] = beyannameler.bul(v["decls"], "kdv", p.bitis.year, p.bitis.month)
        return render(request, "analysis.html", active=keys[key], **v)

    @app.post("/mukellef/{cid}/donem/{pid}/devreden-kdv")
    async def set_devreden(cid: int, pid: int, request: Request, user=Depends(current_user), s=Depends(get_session)):
        c = s.get(Client, cid) or _404()
        p = owned(s, Period, pid, cid)
        form = await form_checked(request)
        if (r := _guard(request, p, cid, f"/mukellef/{cid}/kdv?donem={pid}")):
            return r
        raw = str(form.get("tutar", "")).strip()
        if raw == "":
            p.devreden_kdv = None
        else:
            try:
                val = parse_amount(raw.replace("₺", "").replace("TL", "").strip(), "tr")
            except ParseError:
                val = None
            if val is None or val < 0:
                request.session["flash"] = [{"filename": "Devreden KDV", "status": "hata",
                                             "message": "Tutar okunamadı. Örnek: 125.430,50"}]
                return _redirect(s, f"/mukellef/{cid}/kdv?donem={pid}")
            p.devreden_kdv = val
        audit(s, user, "devreden_kdv", cid, f"period={pid}")
        msg = "Devreden KDV kaydedildi."
        try:
            run_analysis(s, c, p)
            msg += " KDV kontrolleri yeniden çalıştı."
        except NoData:
            msg += " Belgeler yüklenince kontrollerde kullanılacak."
        request.session["flash"] = [{"filename": "Devreden KDV", "status": "aktarildi", "message": msg}]
        return _redirect(s, f"/mukellef/{cid}/kdv?donem={pid}")

    @app.post("/mukellef/{cid}/yukle")
    async def upload_many(cid: int, request: Request, user=Depends(current_user), s=Depends(get_session)):
        c = s.get(Client, cid) or _404()
        form = await form_checked(request)
        period = owned(s, Period, int(form["donem"]), cid) if str(form.get("donem", "")).isdigit() else None
        outcomes, touched, held = [], {}, []
        others = [x for x in s.scalars(select(Client)) if x.id != c.id]
        uploads = []
        for up in form.getlist("dosya"):
            if getattr(up, "filename", ""):
                content = await up.read()
                uploads.append((up.filename, content, peek(up.filename, content)))
        foreign = [i.get("firma") or i.get("vkn") for _, _, i in uploads if _yabanci(i, c)]
        decl_added = False
        for filename, content, info in uploads:
            firma = info.get("firma")
            # Dosya başka bir firmaya ait görünüyorsa aktarmadan önce sor. Unvan yazmayan dosya (ör. kebir),
            # aynı yüklemedeki diğer dosyalar başka firmaya aitse o da bekletilir.
            if foreign and len(content) <= MAX_YUKLEME_BAYT and (_yabanci(info, c) or not (firma or info.get("vkn"))):
                ref = firma or foreign[0]
                oneri = _oneri(info, ref, others)
                held.append({"id": _hold(content, filename), "filename": Path(filename).name[:200],
                             "firma": (firma[:200] + (f" (VKN {info['vkn']})" if info.get("vkn") else "")) if firma
                             else f"dosyada yazmıyor (aynı yüklemedeki dosyalar: {foreign[0][:120]})",
                             "oneri": oneri.id if oneri else None})
                continue
            o = ingest(s, c, filename, content, period)
            outcomes.append(o)
            if o.batch:
                audit(s, user, "dosya_yukle", cid, f"batch={o.batch.id}")
            if o.declaration:
                audit(s, user, "beyanname_yukle", cid, f"declaration={o.declaration.id}")
                decl_added = True
            if o.status == "aktarildi" and o.period:
                touched[o.period.id] = o.period
        for p in touched.values():
            run_analysis(s, c, p)
        if decl_added:
            _rerun_open(s, c, skip=set(touched))
        request.session["flash"] = [{"filename": o.filename, "status": o.status, "message": o.message} for o in outcomes]
        if held:
            request.session["bekleyen"] = {"cid": cid, "donem": period.id if period else None, "items": held}
            return _redirect(s, f"/mukellef/{cid}/yukle/kontrol")
        pending = next((o for o in outcomes if o.status == "esleme"), None)
        if pending and not touched:
            return _redirect(s, f"/mukellef/{cid}/aktarim/{pending.batch.id}")
        target = next(iter(touched.values()), None) or (outcomes[0].period if outcomes and outcomes[0].period else period)
        if decl_added and not touched:
            return _redirect(s, f"/mukellef/{cid}/belgeler" + (f"?donem={target.id}" if target else "") + "#beyannameler")
        return _redirect(s, f"/mukellef/{cid}" + (f"?donem={target.id}" if target else ""))

    @app.get("/mukellef/{cid}/yukle/kontrol", response_class=HTMLResponse)
    def upload_check(cid: int, request: Request, user=Depends(current_user), s=Depends(get_session)):
        c = s.get(Client, cid) or _404()
        b = request.session.get("bekleyen")
        if not b or b.get("cid") != cid:
            return _redirect(s, f"/mukellef/{cid}")
        clients = list(s.scalars(select(Client).order_by(Client.is_demo, Client.unvan)))
        names = {x.id: x.unvan for x in clients}
        flash = request.session.pop("flash", None)
        return render(request, "upload_check.html", c=c, items=b["items"], clients=clients, names=names, flash=flash)

    @app.post("/mukellef/{cid}/yukle/kontrol")
    async def upload_check_confirm(cid: int, request: Request, user=Depends(current_user), s=Depends(get_session)):
        c = s.get(Client, cid) or _404()
        form = await form_checked(request)
        b = request.session.pop("bekleyen", None)
        if not b or b.get("cid") != cid:
            return _redirect(s, f"/mukellef/{cid}")
        outcomes, touched, decl_clients = [], {}, {}
        for it in b["items"]:
            path = _held_path(it["id"])
            choice = str(form.get(f"hedef_{it['id']}", ""))
            if path is None:
                continue
            if choice.isdigit():
                target = s.get(Client, int(choice))
                if target:
                    period = s.get(Period, b["donem"]) if target.id == cid and b.get("donem") else None
                    o = ingest(s, target, it["filename"], path.read_bytes(), period)
                    o.filename = f"{it['filename']} → {target.unvan}"
                    outcomes.append(o)
                    if o.batch:
                        audit(s, user, "dosya_yukle", target.id, f"batch={o.batch.id} firma_uyarisi_onaylandi")
                    if o.declaration:
                        audit(s, user, "beyanname_yukle", target.id, f"declaration={o.declaration.id} firma_uyarisi_onaylandi")
                        decl_clients[target.id] = target
                    if o.status == "aktarildi" and o.period:
                        touched[(target.id, o.period.id)] = (target, o.period)
            else:
                outcomes.append(type("O", (), {"filename": it["filename"], "status": "tekrar", "message": "Yüklenmedi."})())
            path.unlink(missing_ok=True)
        for tc_, tp in touched.values():
            run_analysis(s, tc_, tp)
        for dc in decl_clients.values():
            _rerun_open(s, dc, skip={k[1] for k in touched if k[0] == dc.id})
        request.session["flash"] = [{"filename": o.filename, "status": o.status, "message": o.message} for o in outcomes]
        dest = next(iter(touched.values()), None)
        if dest:
            return _redirect(s, f"/mukellef/{dest[0].id}?donem={dest[1].id}")
        if decl_clients:
            return _redirect(s, f"/mukellef/{next(iter(decl_clients))}/belgeler#beyannameler")
        return _redirect(s, f"/mukellef/{cid}")

    # ---- Dönem işlemleri: geri alma, temizleme, kesinleştirme, not, rapor ---------

    def _rerun(s, c, p):
        try:
            run_analysis(s, c, p)
        except NoData:
            _clear_runs(s, c, p)

    def _guard(request, p, cid, back):
        """Kesinleşmiş dönemde değişiklik yapılmaz."""
        if p.kesin:
            request.session["flash"] = [{"filename": p_name(p), "status": "hata",
                                         "message": "Dönem kesinleştirilmiş. Değişiklik için önce kilidini açın."}]
            return _redirect(None, back)
        return None

    @app.post("/mukellef/{cid}/aktarim/{bid}/geri-al")
    async def batch_undo(cid: int, bid: int, request: Request, user=Depends(current_user), s=Depends(get_session)):
        c = s.get(Client, cid) or _404()
        b = owned(s, ImportBatch, bid, cid)
        await form_checked(request)
        p = s.get(Period, b.period_id)
        back = f"/mukellef/{cid}/belgeler?donem={p.id}"
        if (r := _guard(request, p, cid, back)):
            return r
        b.status = "iptal"
        s.flush()
        _rerun(s, c, p)
        sf = s.get(SourceFile, b.source_file_id)
        audit(s, user, "aktarim_geri_al", cid, f"batch={bid}")
        request.session["flash"] = [{"filename": sf.orig_name, "status": "aktarildi",
                                     "message": "Yükleme geri alındı; kontroller kalan belgelerle yeniden çalıştı."}]
        return _redirect(s, back)

    @app.post("/mukellef/{cid}/beyanname/{did}/geri-al")
    async def declaration_undo(cid: int, did: int, request: Request, user=Depends(current_user), s=Depends(get_session)):
        c = s.get(Client, cid) or _404()
        d = owned(s, Declaration, did, cid)
        form = await form_checked(request)
        d.durum = "iptal"
        s.flush()
        _rerun_open(s, c)
        audit(s, user, "beyanname_geri_al", cid, f"declaration={did}")
        sf = s.get(SourceFile, d.source_file_id)
        request.session["flash"] = [{"filename": sf.orig_name, "status": "aktarildi",
                                     "message": "Beyanname kaldırıldı; açık dönemlerin kontrolleri yeniden çalıştı."}]
        donem = str(form.get("donem", ""))
        return _redirect(s, f"/mukellef/{cid}/belgeler" + (f"?donem={donem}" if donem.isdigit() else "") + "#beyannameler")

    @app.post("/mukellef/{cid}/donem/{pid}/temizle")
    async def period_clear(cid: int, pid: int, request: Request, user=Depends(current_user), s=Depends(get_session)):
        c = s.get(Client, cid) or _404()
        p = owned(s, Period, pid, cid)
        await form_checked(request)
        if (r := _guard(request, p, cid, f"/mukellef/{cid}?donem={pid}")):
            return r
        n = 0
        for b in s.scalars(select(ImportBatch).where(ImportBatch.client_id == cid, ImportBatch.period_id == pid,
                                                     ImportBatch.status != "iptal")):
            b.status = "iptal"
            n += 1
        p.devreden_kdv = None
        s.flush()
        _clear_runs(s, c, p)
        audit(s, user, "donem_temizle", cid, f"period={pid} belge={n}")
        request.session["flash"] = [{"filename": donem_adi(p), "status": "aktarildi",
                                     "message": f"Dönem temizlendi: {n} belge ve analiz sonuçları kaldırıldı. Orijinal dosyalar arşivde duruyor."}]
        return _redirect(s, f"/mukellef/{cid}?donem={pid}")

    @app.post("/mukellef/{cid}/donem/{pid}/kesinlestir")
    async def period_finalize(cid: int, pid: int, request: Request, user=Depends(current_user), s=Depends(get_session)):
        owned(s, Period, pid, cid)
        p = s.get(Period, pid)
        await form_checked(request)
        run = s.scalar(select(AnalysisRun).where(AnalysisRun.client_id == cid, AnalysisRun.period_id == pid)
                       .order_by(AnalysisRun.id.desc()))
        if not run:
            request.session["flash"] = [{"filename": donem_adi(p), "status": "hata",
                                         "message": "Kesinleştirmek için önce belgeleri yükleyip analizi çalıştırın."}]
            return _redirect(s, f"/mukellef/{cid}?donem={pid}")
        p.durum, p.kesin_at, p.kesin_kullanici, p.kesin_run_id = "kesin", _now(), user.username, run.id
        audit(s, user, "donem_kesinlestir", cid, f"period={pid} run={run.id}")
        request.session["flash"] = [{"filename": donem_adi(p), "status": "aktarildi",
                                     "message": "Dönem kesinleştirildi. Belgeler ve analiz sonuçları kilitlendi."}]
        return _redirect(s, f"/mukellef/{cid}?donem={pid}")

    @app.post("/mukellef/{cid}/donem/{pid}/kilidi-ac")
    async def period_unlock(cid: int, pid: int, request: Request, user=Depends(current_user), s=Depends(get_session)):
        p = owned(s, Period, pid, cid)
        await form_checked(request)
        p.durum = "acik"
        audit(s, user, "donem_kilidi_ac", cid, f"period={pid}")
        request.session["flash"] = [{"filename": donem_adi(p), "status": "esleme",
                                     "message": "Dönemin kilidi açıldı; belgeler ve analiz yeniden değiştirilebilir."}]
        return _redirect(s, f"/mukellef/{cid}?donem={pid}")

    @app.post("/mukellef/{cid}/donem/{pid}/not")
    async def period_note(cid: int, pid: int, request: Request, user=Depends(current_user), s=Depends(get_session)):
        p = owned(s, Period, pid, cid)
        form = await form_checked(request)
        p.notlar = str(form.get("notlar", "")).strip()[:10000] or None
        audit(s, user, "donem_not", cid, f"period={pid}")
        request.session["flash"] = [{"filename": donem_adi(p), "status": "aktarildi", "message": "Dönem notu kaydedildi."}]
        return _redirect(s, f"/mukellef/{cid}?donem={pid}#not")

    @app.post("/mukellef/{cid}/donem")
    async def period_create(cid: int, request: Request, user=Depends(current_user), s=Depends(get_session)):
        c = s.get(Client, cid) or _404()
        form = await form_checked(request)
        try:
            bas = date.fromisoformat(str(form["baslangic"]))
            bit = date.fromisoformat(str(form["bitis"]))
        except (KeyError, ValueError):
            raise HTTPException(400, "Tarihler geçersiz")
        if bit < bas:
            raise HTTPException(400, "Bitiş başlangıçtan önce olamaz")
        p = Period(client_id=c.id, baslangic=bas, bitis=bit, tur=str(form.get("tur", "ara")),
                   kapanis_durumu=str(form.get("kapanis_durumu", "oncesi")),
                   enflasyon_duzeltmesi=str(form.get("enflasyon", "bilinmiyor")))
        s.add(p)
        s.flush()
        return _redirect(s, f"/mukellef/{cid}/donem/{p.id}")

    @app.get("/mukellef/{cid}/donem/{pid}", response_class=HTMLResponse)
    def period_page(cid: int, pid: int, request: Request, user=Depends(current_user), s=Depends(get_session), hata: str | None = None):
        c = s.get(Client, cid) or _404()
        p = owned(s, Period, pid, cid)
        batches = list(s.scalars(select(ImportBatch).where(ImportBatch.client_id == cid, ImportBatch.period_id == pid)
                                 .order_by(ImportBatch.id.desc())))
        files = {b.source_file_id: s.get(SourceFile, b.source_file_id) for b in batches}
        runs = list(s.scalars(select(AnalysisRun).where(AnalysisRun.client_id == cid, AnalysisRun.period_id == pid)
                              .order_by(AnalysisRun.id.desc())))
        return render(request, "period.html", c=c, p=p, batches=batches, files=files, runs=runs, hata=hata)

    # ---- İçe aktarma --------------------------------------------------------

    @app.post("/mukellef/{cid}/donem/{pid}/yukle")
    async def upload(cid: int, pid: int, request: Request, user=Depends(current_user), s=Depends(get_session)):
        c = s.get(Client, cid) or _404()
        p = owned(s, Period, pid, cid)
        form = await form_checked(request)
        if (r := _guard(request, p, cid, f"/mukellef/{cid}?donem={pid}")):
            return r
        up = form.get("dosya")
        if not up or not getattr(up, "filename", ""):
            return _redirect(s, f"/mukellef/{cid}/donem/{pid}?hata=Dosya+seçilmedi")
        content = await up.read()
        try:
            batch = create_draft(s, c, p, up.filename, content)
        except DuplicateFile as exc:
            return render(request, "error.html", c=c, title="Bu dosya zaten yüklenmiş",
                          message=f"Aynı içerikteki dosya ({exc.existing.orig_name}) bu mükellef için daha önce yüklendi. "
                                  "Kayıtların çoğalmaması için tekrar alınmadı.", back=f"/mukellef/{cid}/donem/{pid}")
        except ImportError_ as exc:
            s.rollback()
            return render(request, "error.html", c=c, title="Dosya alınamadı", message=str(exc), back=f"/mukellef/{cid}/donem/{pid}")
        audit(s, user, "dosya_yukle", cid, f"batch={batch.id}")
        return _redirect(s, f"/mukellef/{cid}/aktarim/{batch.id}")

    @app.get("/mukellef/{cid}/aktarim/{bid}", response_class=HTMLResponse)
    def batch_page(cid: int, bid: int, request: Request, user=Depends(current_user), s=Depends(get_session)):
        c = s.get(Client, cid) or _404()
        b = owned(s, ImportBatch, bid, cid)
        sf = s.get(SourceFile, b.source_file_id)
        rows = preview(s, b, limit=60) if b.status == "taslak" else []
        header = next((r["cells"] for r in rows if r["row_no"] == b.header_row), [])
        ncols = max((len(r["cells"]) for r in rows), default=0)
        return render(request, "batch.html", c=c, b=b, sf=sf, rows=rows, header=header, ncols=ncols,
                      FIELD_DEFS=FIELD_DEFS, DOC_FIELDS=DOC_FIELDS, REQUIRED=REQUIRED)

    @app.post("/mukellef/{cid}/aktarim/{bid}/onayla")
    async def batch_confirm(cid: int, bid: int, request: Request, user=Depends(current_user), s=Depends(get_session)):
        c = s.get(Client, cid) or _404()
        b = owned(s, ImportBatch, bid, cid)
        form = await form_checked(request)
        if (r := _guard(request, s.get(Period, b.period_id), cid, f"/mukellef/{cid}/aktarim/{bid}")):
            return r
        doc_type = str(form.get("doc_type", ""))
        mapping = {}
        for f in DOC_FIELDS.get(doc_type, []):
            v = str(form.get(f"map_{f}", ""))
            if v.isdigit():
                mapping[f] = int(v)
        try:
            confirm(s, b, doc_type, mapping, "en" if form.get("number_format") == "en" else "tr")
        except ImportError_ as exc:
            s.rollback()
            return render(request, "error.html", c=c, title="Aktarım onaylanamadı", message=str(exc),
                          back=f"/mukellef/{cid}/aktarim/{bid}")
        audit(s, user, "aktarim_onayla", cid, f"batch={bid}")
        return _redirect(s, f"/mukellef/{cid}/aktarim/{bid}")

    @app.post("/mukellef/{cid}/aktarim/{bid}/iptal")
    async def batch_cancel(cid: int, bid: int, request: Request, user=Depends(current_user), s=Depends(get_session)):
        b = owned(s, ImportBatch, bid, cid)
        await form_checked(request)
        if b.status == "taslak":
            b.status = "iptal"
        return _redirect(s, f"/mukellef/{cid}/donem/{b.period_id}")

    # ---- Analiz ve bulgular -------------------------------------------------

    @app.post("/mukellef/{cid}/donem/{pid}/analiz")
    async def analyse(cid: int, pid: int, request: Request, user=Depends(current_user), s=Depends(get_session)):
        c = s.get(Client, cid) or _404()
        p = owned(s, Period, pid, cid)
        await form_checked(request)
        form = await request.form()
        if (r := _guard(request, p, cid, f"/mukellef/{cid}?donem={pid}")):
            return r
        try:
            run = run_analysis(s, c, p)
        except NoData as exc:
            return _redirect(s, f"/mukellef/{cid}/donem/{pid}?hata={exc}")
        audit(s, user, "analiz", cid, f"run={run.id}")
        if form.get("geri") == "panel":
            return _redirect(s, f"/mukellef/{cid}?donem={pid}")
        return _redirect(s, f"/mukellef/{cid}/analiz/{run.id}")

    @app.get("/mukellef/{cid}/analiz/{rid}", response_class=HTMLResponse)
    def run_page(cid: int, rid: int, request: Request, user=Depends(current_user), s=Depends(get_session)):
        c = s.get(Client, cid) or _404()
        run = owned(s, AnalysisRun, rid, cid)
        p = s.get(Period, run.period_id)
        findings = list(s.scalars(select(Finding).where(Finding.run_id == rid, Finding.client_id == cid)))
        findings.sort(key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.rule_id, f.hesap_kodu or ""))
        cases = {cs.fingerprint: cs for cs in s.scalars(select(FindingCase).where(FindingCase.client_id == cid))}
        # Önceki analizde olup bu analizde görülmeyen bulgular
        prev = s.scalar(select(AnalysisRun).where(AnalysisRun.client_id == cid, AnalysisRun.period_id == run.period_id,
                                                  AnalysisRun.id < rid).order_by(AnalysisRun.id.desc()))
        gone = []
        if prev:
            now_fps = {f.fingerprint for f in findings}
            gone = [f for f in s.scalars(select(Finding).where(Finding.run_id == prev.id)) if f.fingerprint not in now_fps]
        src = run.source_batches or {"mizan": run.mizan_batch_id, "yevmiye": run.yevmiye_batch_id}
        batches = {k: owned(s, ImportBatch, v, cid) for k, v in src.items() if v}
        files = {k: s.get(SourceFile, b.source_file_id) for k, b in batches.items()}
        rules = {r.id: r for r, _ in ALL_RULES}
        return render(request, "run.html", active="bulgular", c=c, p=p, run=run, findings=findings, cases=cases, gone=gone,
                      batches=batches, files=files, rules=rules)

    @app.get("/mukellef/{cid}/bulgu/{fid}", response_class=HTMLResponse)
    def finding_page(cid: int, fid: int, request: Request, user=Depends(current_user), s=Depends(get_session)):
        c = s.get(Client, cid) or _404()
        f = owned(s, Finding, fid, cid)
        run = s.get(AnalysisRun, f.run_id)
        evidence = []
        for ev in s.scalars(select(FindingEvidence).where(FindingEvidence.finding_id == fid, FindingEvidence.client_id == cid)):
            raw = owned(s, RawRow, ev.raw_row_id, cid)
            batch = s.get(ImportBatch, raw.batch_id)
            sf = s.get(SourceFile, batch.source_file_id)
            header = s.scalar(select(RawRow).where(RawRow.batch_id == batch.id, RawRow.row_no == batch.header_row))
            evidence.append({"ev": ev, "raw": raw, "batch": batch, "sf": sf, "header": header.cells if header else []})
        case = s.scalar(select(FindingCase).where(FindingCase.client_id == cid, FindingCase.fingerprint == f.fingerprint))
        rule = next((r for r, _ in ALL_RULES if r.id == f.rule_id), None)
        return render(request, "finding.html", c=c, f=f, run=run, evidence=evidence, case=case, rule=rule)

    @app.post("/mukellef/{cid}/bulgu/{fid}/durum")
    async def finding_status(cid: int, fid: int, request: Request, user=Depends(current_user), s=Depends(get_session)):
        f = owned(s, Finding, fid, cid)
        form = await form_checked(request)
        status = str(form.get("durum", "yeni"))
        if status not in STATUS_LABELS:
            raise HTTPException(400, "Geçersiz durum")
        case = s.scalar(select(FindingCase).where(FindingCase.client_id == cid, FindingCase.fingerprint == f.fingerprint))
        case.status = status
        case.note = str(form.get("not", ""))[:5000] or None
        audit(s, user, "bulgu_durum", cid, f"{f.fingerprint} -> {status}")
        return _redirect(s, f"/mukellef/{cid}/bulgu/{fid}")

    return app


def _redirect(s, url):
    """Yönlendirmeden önce kaydet: tarayıcı yeni sayfayı istediğinde değişiklik veritabanında olmalı."""
    if s is not None:
        s.commit()
    return RedirectResponse(url, status_code=303)


def _signer():
    return URLSafeSerializer(_secret_key(), salt="mukellef-listesi")


def summarize_empty():
    from ..finance import Summary
    return Summary()


def _404():
    raise HTTPException(404, "Bulunamadı")


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


def _yabanci(info: dict, c) -> bool:
    """Dosya başka bir firmaya mı ait? Beyannamede VKN varsa ve mükellefin VKN'si kayıtlıysa VKN'ye bakılır."""
    if info.get("vkn") and c.vkn:
        return info["vkn"] != c.vkn
    return bool(info.get("firma")) and not firma_eslesir(info["firma"], c.unvan)


def _oneri(info: dict, ref: str, others):
    if info.get("vkn"):
        by_vkn = next((x for x in others if x.vkn and x.vkn == info["vkn"]), None)
        if by_vkn:
            return by_vkn
    return firma_en_iyi(ref, others)


def _rerun_open(s, c, skip=()):
    """Beyanname eklenince/kaldırılınca mükellefin açık dönemlerinin kontrollerini yeniler."""
    for p in s.scalars(select(Period).where(Period.client_id == c.id)):
        if p.id in skip or p.kesin:
            continue
        try:
            run_analysis(s, c, p)
        except NoData:
            pass


def p_name(p) -> str:
    return donem_adi(p)


def _hold_dir() -> Path:
    d = data_dir() / "bekleyen"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _hold(content: bytes, filename: str) -> str:
    """Onay bekleyen dosyayı geçici olarak saklar; rastgele kimlik döner."""
    hid = secrets.token_hex(12) + Path(filename).suffix.lower()[:6]
    (_hold_dir() / hid).write_bytes(content)
    return hid


def _held_path(hid: str) -> Path | None:
    import re
    if not re.fullmatch(r"[0-9a-f]{24}(\.[a-z]{1,5})?", hid or ""):
        return None
    path = _hold_dir() / hid
    return path if path.exists() else None


def _clear_runs(s, c, p):
    """Dönemin analiz sonuçlarını siler (belge kalmadığında veya dönem temizlendiğinde)."""
    from sqlalchemy import delete
    run_ids = [r.id for r in s.scalars(select(AnalysisRun).where(AnalysisRun.client_id == c.id, AnalysisRun.period_id == p.id))]
    if not run_ids:
        return
    f_ids = [f.id for f in s.scalars(select(Finding).where(Finding.run_id.in_(run_ids)))]
    if f_ids:
        s.execute(delete(FindingEvidence).where(FindingEvidence.finding_id.in_(f_ids)))
        s.execute(delete(Finding).where(Finding.id.in_(f_ids)))
    s.execute(delete(AnalysisRun).where(AnalysisRun.id.in_(run_ids)))
    s.flush()
