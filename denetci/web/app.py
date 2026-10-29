"""Web arayüzü. Yalnızca 127.0.0.1 üzerinde çalışacak şekilde tasarlandı (tek kullanıcı, yerel kurulum)."""

import secrets
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from starlette.middleware.sessions import SessionMiddleware

from .. import db
from ..auth import LoginThrottle, verify_password, verify_totp
from ..checks.engine import RELIABILITY_LABELS, NoData, run_analysis
from ..checks.rules import ALL_RULES
from ..config import data_dir
from ..importer.load import DuplicateFile, ImportError_, confirm, create_draft, preview
from ..importer.luca import PROFILE_LABELS
from ..importer.tables import DOC_FIELDS, DOC_LABELS, FIELD_DEFS, REQUIRED
from ..models import (AnalysisRun, AuditLog, Client, Finding, FindingCase, FindingEvidence, ImportBatch, Period, RawRow,
                      SourceFile, User)
from ..parsing import kurus_str

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))
templates.env.filters["tl"] = kurus_str
templates.env.globals["RELIABILITY_LABELS"] = RELIABILITY_LABELS
templates.env.globals["DOC_LABELS"] = DOC_LABELS
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
        return RedirectResponse("/giris", status_code=303)

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
            verify_totp(user.totp_secret, str(form.get("kod", "")))
        if not ok:
            throttle.fail(username)
            audit(s, None, "giris_basarisiz", detail=username[:64])
            return render(request, "login.html", error="Kullanıcı adı, parola veya doğrulama kodu hatalı.")
        throttle.reset(username)
        request.session.clear()
        request.session["uid"] = user.id
        request.session["csrf"] = secrets.token_urlsafe(24)
        audit(s, user, "giris")
        return RedirectResponse("/", status_code=303)

    @app.post("/cikis")
    async def logout(request: Request):
        await form_checked(request)
        request.session.clear()
        return RedirectResponse("/giris", status_code=303)

    # ---- Portföy ve mükellef -----------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def portfolio(request: Request, user=Depends(current_user), s=Depends(get_session)):
        rows = []
        for c in s.scalars(select(Client).order_by(Client.unvan)):
            run = s.scalar(select(AnalysisRun).where(AnalysisRun.client_id == c.id).order_by(AnalysisRun.id.desc()))
            open_high = 0
            if run:
                open_high = s.scalar(select(func.count()).select_from(Finding).where(
                    Finding.run_id == run.id, Finding.severity.in_(["Kritik", "Yüksek"]))) or 0
            last_upload = s.scalar(select(func.max(ImportBatch.created_at)).where(ImportBatch.client_id == c.id))
            rows.append({"c": c, "run": run, "open_high": open_high, "last_upload": last_upload})
        rows.sort(key=lambda r: (-(r["open_high"]), r["c"].unvan))
        return render(request, "portfolio.html", rows=rows)

    @app.post("/mukellef/yeni")
    async def client_create(request: Request, user=Depends(current_user), s=Depends(get_session)):
        form = await form_checked(request)
        unvan = str(form.get("unvan", "")).strip()
        if not unvan:
            raise HTTPException(400, "Unvan gerekli")
        c = Client(unvan=unvan[:256], sirket_turu=str(form.get("sirket_turu", ""))[:64] or None,
                   faaliyet=str(form.get("faaliyet", ""))[:256] or None)
        s.add(c)
        s.flush()
        audit(s, user, "mukellef_olustur", c.id)
        return RedirectResponse(f"/mukellef/{c.id}", status_code=303)

    @app.get("/mukellef/{cid}", response_class=HTMLResponse)
    def client_page(cid: int, request: Request, user=Depends(current_user), s=Depends(get_session)):
        c = s.get(Client, cid) or _404()
        periods = list(s.scalars(select(Period).where(Period.client_id == cid).order_by(Period.baslangic.desc())))
        return render(request, "client.html", c=c, periods=periods)

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
        return RedirectResponse(f"/mukellef/{cid}/donem/{p.id}", status_code=303)

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
            return RedirectResponse(f"/mukellef/{cid}/donem/{pid}?hata=Dosya+seçilmedi", status_code=303)
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
        return RedirectResponse(f"/mukellef/{cid}/aktarim/{batch.id}", status_code=303)

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
        return RedirectResponse(f"/mukellef/{cid}/aktarim/{bid}", status_code=303)

    @app.post("/mukellef/{cid}/aktarim/{bid}/iptal")
    async def batch_cancel(cid: int, bid: int, request: Request, user=Depends(current_user), s=Depends(get_session)):
        b = owned(s, ImportBatch, bid, cid)
        await form_checked(request)
        if b.status == "taslak":
            b.status = "iptal"
        return RedirectResponse(f"/mukellef/{cid}/donem/{b.period_id}", status_code=303)

    # ---- Analiz ve bulgular -------------------------------------------------

    @app.post("/mukellef/{cid}/donem/{pid}/analiz")
    async def analyse(cid: int, pid: int, request: Request, user=Depends(current_user), s=Depends(get_session)):
        c = s.get(Client, cid) or _404()
        p = owned(s, Period, pid, cid)
        await form_checked(request)
        try:
            run = run_analysis(s, c, p)
        except NoData as exc:
            return RedirectResponse(f"/mukellef/{cid}/donem/{pid}?hata={exc}", status_code=303)
        audit(s, user, "analiz", cid, f"run={run.id}")
        return RedirectResponse(f"/mukellef/{cid}/analiz/{run.id}", status_code=303)

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
        return RedirectResponse(f"/mukellef/{cid}/bulgu/{fid}", status_code=303)

    return app


def _404():
    raise HTTPException(404, "Bulunamadı")
