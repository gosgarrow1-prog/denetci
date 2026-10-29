"""Web arayüzü. Yalnızca 127.0.0.1 üzerinde çalışacak şekilde tasarlandı (tek kullanıcı, yerel kurulum)."""

import secrets
from datetime import date
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
from ..models import (AnalysisRun, AuditLog, Client, Finding, FindingCase, FindingEvidence, ImportBatch, Period, RawRow,
                      SourceFile, User)
from ..parsing import kurus_str
from ..finance import SINIFLAR, summarize
from ..checks.engine import build_context
from ..importer.clients import ClientRow, ListError, mark_existing, parse_client_list
from ..importer.tables import UnsupportedFile
from ..importer.ingest import ingest
from .charts import balance_bars, tl_short

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))
templates.env.filters["tl"] = kurus_str
templates.env.globals["RELIABILITY_LABELS"] = RELIABILITY_LABELS
templates.env.globals["DOC_LABELS"] = DOC_LABELS
templates.env.globals["SINIFLAR"] = SINIFLAR
templates.env.globals["tl_short"] = tl_short
templates.env.globals["PROFILE_LABELS"] = PROFILE_LABELS
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
    return templates.TemplateResponse(request, name, ctx)


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
        return render(request, "portfolio.html", rows=rows, has_demo=any(r["c"].is_demo for r in rows))

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

    @app.get("/mukellef/{cid}", response_class=HTMLResponse)
    def client_page(cid: int, request: Request, user=Depends(current_user), s=Depends(get_session), donem: int | None = None):
        c = s.get(Client, cid) or _404()
        periods = list(s.scalars(select(Period).where(Period.client_id == cid).order_by(Period.bitis.desc(), Period.baslangic)))
        p = owned(s, Period, donem, cid) if donem else (periods[0] if periods else None)
        flash = request.session.pop("flash", None)
        ctx = {"c": c, "periods": periods, "p": p, "flash": flash, "has_batches": False}
        if p:
            batches = list(s.scalars(select(ImportBatch).where(ImportBatch.client_id == cid, ImportBatch.period_id == p.id,
                                                               ImportBatch.status != "iptal").order_by(ImportBatch.id.desc())))
            ctx.update(batches=batches, files={b.source_file_id: s.get(SourceFile, b.source_file_id) for b in batches},
                       has_batches=bool(batches))
            run = s.scalar(select(AnalysisRun).where(AnalysisRun.client_id == cid, AnalysisRun.period_id == p.id)
                           .order_by(AnalysisRun.id.desc()))
            findings = list(s.scalars(select(Finding).where(Finding.run_id == run.id))) if run else []
            findings.sort(key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), -(abs(f.amount or 0))))
            cases = {cs.fingerprint: cs for cs in s.scalars(select(FindingCase).where(FindingCase.client_id == cid))}
            open_findings = [f for f in findings if f.fingerprint not in cases or cases[f.fingerprint].status != "aciklandi"]
            sev_counts = {k: sum(1 for f in open_findings if f.severity == k) for k in ("Kritik", "Yüksek", "Orta")}
            fctx, _ = build_context(s, c, p)
            fin = summarize(fctx) if fctx else summarize_empty()
            prev_fin = None
            if fctx:
                earlier = s.scalars(select(Period).where(Period.client_id == cid, Period.bitis < p.baslangic)
                                    .order_by(Period.bitis.desc())).all()
                for pp in earlier:
                    pctx, _ = build_context(s, c, pp)
                    if pctx:
                        prev_fin = summarize(pctx)
                        break
            chart = None
            if fin.has_balance:
                b = fin.bilanco
                chart = balance_bars([("Dönen varlıklar", b["1"], "--s1"), ("Duran varlıklar", b["2"], "--s2")],
                                     [("Kısa vadeli yabancı kaynaklar", b["3"], "--s3"), ("Uzun vadeli yabancı kaynaklar", b["4"], "--s4"),
                                      ("Özkaynaklar + dönem sonucu", b["5"] + (fin.donem_sonucu_kapanmamis or 0), "--s5")])
            ctx.update(run=run, findings=open_findings, cases=cases, sev_counts=sev_counts, fin=fin, prev_fin=prev_fin,
                       prev_ratios={r.key: r for r in prev_fin.oranlar} if prev_fin else {}, balance_chart=chart)
        return render(request, "client.html", **ctx)

    @app.post("/mukellef/{cid}/yukle")
    async def upload_many(cid: int, request: Request, user=Depends(current_user), s=Depends(get_session)):
        c = s.get(Client, cid) or _404()
        form = await form_checked(request)
        period = owned(s, Period, int(form["donem"]), cid) if str(form.get("donem", "")).isdigit() else None
        outcomes, touched = [], {}
        for up in form.getlist("dosya"):
            if not getattr(up, "filename", ""):
                continue
            content = await up.read()
            o = ingest(s, c, up.filename, content, period)
            outcomes.append(o)
            if o.batch:
                audit(s, user, "dosya_yukle", cid, f"batch={o.batch.id}")
            if o.status == "aktarildi":
                touched[o.period.id] = o.period
        for p in touched.values():
            run_analysis(s, c, p)
        request.session["flash"] = [{"filename": o.filename, "status": o.status, "message": o.message} for o in outcomes]
        pending = next((o for o in outcomes if o.status == "esleme"), None)
        if pending and not touched:
            return _redirect(s, f"/mukellef/{cid}/aktarim/{pending.batch.id}")
        target = next(iter(touched.values()), None) or (outcomes[0].period if outcomes and outcomes[0].period else period)
        return _redirect(s, f"/mukellef/{cid}" + (f"?donem={target.id}" if target else ""))

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
        return render(request, "run.html", c=c, p=p, run=run, findings=findings, cases=cases, gone=gone,
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
