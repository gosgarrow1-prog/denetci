"""Standart veri modeli.

Kurallar:
- Tutarlar tam sayı kuruş olarak saklanır (kayan nokta yok).
- Mükellefe ait her tabloda client_id bulunur; sorgular bu alanla süzülür.
- Orijinal dosya ve ham satırlar değiştirilmez.
"""

from datetime import date, datetime, timezone

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def now():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    totp_secret: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Client(Base):
    """Mükellef."""

    __tablename__ = "clients"
    id: Mapped[int] = mapped_column(primary_key=True)
    unvan: Mapped[str] = mapped_column(String(256))
    sirket_turu: Mapped[str | None] = mapped_column(String(64))
    faaliyet: Mapped[str | None] = mapped_column(String(256))
    vkn: Mapped[str | None] = mapped_column(String(16), index=True)  # VKN veya TCKN
    vergi_dairesi: Mapped[str | None] = mapped_column(String(128))
    luca_kodu: Mapped[str | None] = mapped_column(String(64))
    para_birimi: Mapped[str] = mapped_column(String(3), default="TRY")
    # Uygulanan çerçeve. Kurallar bu etikete göre seçilir; ileride BOBİ FRS vb. eklenebilir.
    cerceve: Mapped[str] = mapped_column(String(32), default="THP_VUK")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Period(Base):
    __tablename__ = "periods"
    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    baslangic: Mapped[date] = mapped_column(Date)
    bitis: Mapped[date] = mapped_column(Date)
    tur: Mapped[str] = mapped_column(String(16))  # aylik, uc_aylik, yillik, ara
    kapanis_durumu: Mapped[str] = mapped_column(String(16), default="oncesi")  # oncesi, sonrasi
    enflasyon_duzeltmesi: Mapped[str] = mapped_column(String(16), default="bilinmiyor")
    # Önceki ayın KDV beyannamesindeki "sonraki döneme devreden KDV" (kuruş); kullanıcı girer.
    devreden_kdv: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Kesinleşen dönem kilitlenir: yükleme, geri alma ve yeniden analiz yapılmaz (kilidi açılabilir).
    durum: Mapped[str | None] = mapped_column(String(16), default="acik")  # acik, kesin
    kesin_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    kesin_kullanici: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kesin_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notlar: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Geçici vergi girdileri (kuruş): kkeg, istisna, gecmis_zarar, indirim, onceki_odenen; oran ve tür (kurum/sahis).
    gv: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    @property
    def kesin(self) -> bool:
        return self.durum == "kesin"


class SourceFile(Base):
    __tablename__ = "source_files"
    __table_args__ = (UniqueConstraint("client_id", "sha256"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    sha256: Mapped[str] = mapped_column(String(64))
    orig_name: Mapped[str] = mapped_column(String(256))
    size: Mapped[int] = mapped_column(Integer)
    stored_path: Mapped[str] = mapped_column(String(512))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ImportBatch(Base):
    __tablename__ = "import_batches"
    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    period_id: Mapped[int] = mapped_column(ForeignKey("periods.id"), index=True)
    source_file_id: Mapped[int] = mapped_column(ForeignKey("source_files.id"))
    doc_type: Mapped[str | None] = mapped_column(String(16))  # mizan, yevmiye
    status: Mapped[str] = mapped_column(String(16), default="taslak")  # taslak, onaylandi, iptal
    # Tanınan program biçimi (ör. luca_yevmiye). Boşsa genel kolon eşleme kullanılır.
    profile: Mapped[str | None] = mapped_column(String(32))
    sheet: Mapped[str | None] = mapped_column(String(128))
    header_row: Mapped[int | None] = mapped_column(Integer)
    mapping: Mapped[dict] = mapped_column(JSON, default=dict)
    number_format: Mapped[str] = mapped_column(String(4), default="tr")
    version: Mapped[int] = mapped_column(Integer, default=0)
    supersedes_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id"))
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RawRow(Base):
    __tablename__ = "raw_rows"
    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"), index=True)
    sheet: Mapped[str] = mapped_column(String(128))
    row_no: Mapped[int] = mapped_column(Integer)  # dosyadaki satır numarası (1'den başlar)
    cells: Mapped[list] = mapped_column(JSON)
    row_class: Mapped[str] = mapped_column(String(16))  # kayit, ara_toplam, baslik, bilgi, bos, belirsiz, hatali
    note: Mapped[str | None] = mapped_column(Text)


class JournalLine(Base):
    """Kayıt düzeyi satır. Yevmiye, defteri kebir ve muavinden gelen satırlar burada tutulur;
    hangi belgeden geldiği batch'in doc_type alanından anlaşılır. Belgeler birbirine eklenmez."""

    __tablename__ = "journal_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"), index=True)
    raw_row_id: Mapped[int] = mapped_column(ForeignKey("raw_rows.id"))
    fis_no: Mapped[str] = mapped_column(String(64))
    madde_no: Mapped[str | None] = mapped_column(String(32))
    fis_tipi: Mapped[str | None] = mapped_column(String(32))
    evrak_no: Mapped[str | None] = mapped_column(String(64))
    tarih: Mapped[date] = mapped_column(Date)
    hesap_kodu: Mapped[str] = mapped_column(String(64), index=True)
    hesap_adi: Mapped[str | None] = mapped_column(String(256))
    aciklama: Mapped[str | None] = mapped_column(Text)
    borc: Mapped[int] = mapped_column(Integer, default=0)
    alacak: Mapped[int] = mapped_column(Integer, default=0)


class OpeningBalance(Base):
    """Kebir/muavindeki "Nakli Yekün": seçilen tarih aralığından önceki birikmiş borç/alacak."""

    __tablename__ = "opening_balances"
    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"), index=True)
    raw_row_id: Mapped[int] = mapped_column(ForeignKey("raw_rows.id"))
    hesap_kodu: Mapped[str] = mapped_column(String(64))
    borc: Mapped[int] = mapped_column(Integer, default=0)
    alacak: Mapped[int] = mapped_column(Integer, default=0)


class TrialBalanceLine(Base):
    """Mizan satırı."""

    __tablename__ = "trial_balance_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"), index=True)
    raw_row_id: Mapped[int] = mapped_column(ForeignKey("raw_rows.id"))
    hesap_kodu: Mapped[str] = mapped_column(String(64), index=True)
    hesap_adi: Mapped[str | None] = mapped_column(String(256))
    acilis_borc: Mapped[int | None] = mapped_column(Integer)
    acilis_alacak: Mapped[int | None] = mapped_column(Integer)
    borc: Mapped[int] = mapped_column(Integer, default=0)
    alacak: Mapped[int] = mapped_column(Integer, default=0)
    bakiye_borc: Mapped[int | None] = mapped_column(Integer)
    bakiye_alacak: Mapped[int | None] = mapped_column(Integer)
    parent_kodu: Mapped[str | None] = mapped_column(String(64))
    is_leaf: Mapped[bool] = mapped_column(Boolean, default=True)


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    period_id: Mapped[int] = mapped_column(ForeignKey("periods.id"))
    mizan_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id"))
    yevmiye_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id"))
    ruleset_version: Mapped[str] = mapped_column(String(16))
    engine_version: Mapped[str] = mapped_column(String(16))
    reliability: Mapped[str] = mapped_column(String(16))  # uygun, kismen, kritik
    rule_results: Mapped[list] = mapped_column(JSON, default=list)
    # Analizde kullanılan tüm belge sürümleri: {"yevmiye": batch_id, "kebir": batch_id, ...}
    source_batches: Mapped[dict | None] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Finding(Base):
    __tablename__ = "findings"
    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    rule_id: Mapped[str] = mapped_column(String(16))
    rule_version: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(2))  # A, K, I, D
    severity: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(256))
    hesap_kodu: Mapped[str | None] = mapped_column(String(64))
    amount: Mapped[int | None] = mapped_column(Integer)
    explanation: Mapped[str] = mapped_column(Text)
    certainty: Mapped[str] = mapped_column(Text)
    next_step: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(256), index=True)


class FindingEvidence(Base):
    __tablename__ = "finding_evidence"
    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    finding_id: Mapped[int] = mapped_column(ForeignKey("findings.id"), index=True)
    raw_row_id: Mapped[int] = mapped_column(ForeignKey("raw_rows.id"))
    label: Mapped[str] = mapped_column(String(256))


class FindingCase(Base):
    """Bulgu takibi. Parmak izi düzeyinde tutulur; yeni analizde notlar kaybolmaz.
    Durum değişikliği kaynak veriye dokunmaz."""

    __tablename__ = "finding_cases"
    __table_args__ = (UniqueConstraint("client_id", "fingerprint"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(16), default="yeni")  # yeni, inceleniyor, aciklandi
    note: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer)
    client_id: Mapped[int | None] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
