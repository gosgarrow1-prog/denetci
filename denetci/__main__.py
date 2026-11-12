"""Komut satırı.

    python -m denetci kullanici-ekle <kullanici_adi>
    python -m denetci demo
    python -m denetci calistir
    python -m denetci telefon-kodu <kullanici_adi>
    python -m denetci yedek
"""

import argparse
import getpass
import sys
from datetime import date

from sqlalchemy import select

from . import db
from .auth import hash_password, new_totp_secret, otpauth_uri
from .config import data_dir, telefon_kodu


def cmd_add_user(args):
    db.init_db()
    from .models import User

    with db.SessionLocal() as s:
        if s.scalar(select(User).where(User.username == args.username)):
            sys.exit("Bu kullanıcı zaten var.")
        pw = getpass.getpass("Parola (en az 12 karakter): ")
        if len(pw) < 12:
            sys.exit("Parola en az 12 karakter olmalı.")
        if pw != getpass.getpass("Parola (tekrar): "):
            sys.exit("Parolalar eşleşmedi.")
        secret = new_totp_secret()
        s.add(User(username=args.username, password_hash=hash_password(pw), totp_secret=secret))
        s.commit()
    print("\nKullanıcı eklendi.")
    if not telefon_kodu():
        return
    print("Telefonunuzdaki kimlik doğrulayıcı uygulamaya (Google Authenticator, Microsoft Authenticator vb.)")
    print("'anahtarı elle gir' seçeneğiyle şu anahtarı ekleyin:\n")
    print(f"    {secret}\n")
    print(f"(Bazı uygulamalar bu bağlantıyı da kabul eder: {otpauth_uri(secret, args.username)})")
    print("Bu anahtarı kimseyle paylaşmayın.")


def cmd_telefon_kodu(args):
    """Kullanıcının telefon kodu anahtarını gösterir (kimlik doğrulayıcı uygulamaya eklemek için)."""
    db.init_db()
    from .models import User

    with db.SessionLocal() as s:
        u = s.scalar(select(User).where(User.username == args.username))
        if not u:
            sys.exit("Böyle bir kullanıcı yok.")
        if args.yenile:
            u.totp_secret = new_totp_secret()
            s.commit()
        print(f"Kullanıcı: {u.username}")
        print("Telefonunuzdaki kimlik doğrulayıcı uygulamaya 'anahtarı elle gir' seçeneğiyle şu anahtarı ekleyin:\n")
        print(f"    {u.totp_secret}\n")
        print(f"(Bağlantı: {otpauth_uri(u.totp_secret, u.username)})")
        print("Bu anahtarı kimseyle paylaşmayın.")


def cmd_yedek(args):
    from . import buro, yedek

    yol = yedek.al(ek_klasorler=[buro.klasor()])
    print(f"Yedek alındı: {yol}")


def cmd_demo(args):
    from .importer.load import confirm, create_draft
    from .models import Client, Period
    from .synthetic import PERIOD, generate

    db.init_db()
    folder = data_dir() / "demo_dosyalari"
    files = generate(folder, hatali=True)
    with db.SessionLocal() as s:
        c = Client(unvan="DEMO — Örnek Ticaret A.Ş. (SENTETİK)", sirket_turu="A.Ş.", faaliyet="Toptan ticaret (sentetik)", is_demo=True)
        s.add(c)
        s.flush()
        p = Period(client_id=c.id, baslangic=PERIOD[0], bitis=PERIOD[1], tur="uc_aylik", kapanis_durumu="oncesi", enflasyon_duzeltmesi="yok")
        s.add(p)
        s.flush()
        if args.yukle:
            for path in files.values():
                b = create_draft(s, c, p, path.name, path.read_bytes())
                confirm(s, b, b.doc_type, b.mapping, b.number_format)
        s.commit()
    print(f"DEMO mükellef oluşturuldu. Sentetik dosyalar: {folder}")
    if not args.yukle:
        print("Bu dosyaları arayüzden yükleyerek akışı deneyebilirsiniz.")


def cmd_run(args):
    import uvicorn

    from .web.app import create_app

    print(f"Veri klasörü: {data_dir()}")
    print(f"Tarayıcıda açın: http://127.0.0.1:{args.port}")
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="warning")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="denetci")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("kullanici-ekle", help="Yeni kullanıcı ekler (kayıt ekranı yoktur)")
    p.add_argument("username")
    p.set_defaults(func=cmd_add_user)
    p = sub.add_parser("demo", help="Sentetik DEMO mükellef ve dosyalar oluşturur")
    p.add_argument("--yukle", action="store_true", help="Dosyaları otomatik olarak içe aktar")
    p.set_defaults(func=cmd_demo)
    p = sub.add_parser("calistir", help="Uygulamayı yalnızca bu bilgisayarda (127.0.0.1) başlatır")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_run)
    p = sub.add_parser("telefon-kodu", help="Kullanıcının telefon kodu anahtarını gösterir")
    p.add_argument("username")
    p.add_argument("--yenile", action="store_true", help="Yeni anahtar üretir (eski telefon geçersiz olur)")
    p.set_defaults(func=cmd_telefon_kodu)
    p = sub.add_parser("yedek", help="Veritabanı ve dosyaların yedeğini alır, 14 günden eskileri siler")
    p.set_defaults(func=cmd_yedek)
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
