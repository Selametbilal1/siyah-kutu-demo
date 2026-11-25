import datetime
import hashlib
import io
import json
from pathlib import Path
import pandas as pd
import streamlit as st

try:
    from PIL import Image, ImageOps
    import zxingcpp
except ImportError:
    Image = None
    ImageOps = None
    zxingcpp = None

st.set_page_config(
    page_title="Siyah Kutu v0.3.3 | Saha Pilot Sürümü",
    page_icon="🟩",
    layout="wide",
)

BASE_DIR = Path(__file__).resolve().parent
DURUM_DOSYASI = BASE_DIR / "siyah_kutu_durum.json"
AKTIF_SURUM = "0.3.3"
VARSAYILAN_TARIH = datetime.date(2026, 11, 24)

KATEGORI_MAX_OMUR = {
    "Günlük Süt": 7,
    "Süt Ürünleri": 21,
    "Manav (Taze)": 5,
    "Şarküteri": 30,
    "Paketli Ekmek": 10,
    "Paketli Atıştırmalık": 90,
    "Kuru Gıda": 365,
}

REYON_VARSAYILAN_KATEGORI = {
    "Sütlük": "Süt Ürünleri",
    "Manav": "Manav (Taze)",
    "Şarküteri": "Şarküteri",
    "Ekmek": "Paketli Ekmek",
    "Paketli Gıda": "Paketli Atıştırmalık",
    "Kuru Gıda": "Kuru Gıda",
}


# --- 0. YARDIMCI FONKSİYONLAR: BARKOD MOD-10 DOĞRULAMA, HASH & KAMERA ---
def ean_kontrol_basamagi(govde: str) -> int:
    if len(govde) == 12:  # EAN-13
        toplam = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(govde))
    elif len(govde) == 7:  # EAN-8
        toplam = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(govde))
    else:
        return -1
    return (10 - (toplam % 10)) % 10


def barkod_dogrula(kod: str, bilinen_barkodlar: set):
    kod = (kod or "").strip()
    if not kod:
        return False, "bos", ""
    if kod in bilinen_barkodlar:
        return True, "katalog", ""
    if not kod.isdigit():
        return False, "format", "🚫 Barkod yalnızca rakamlardan oluşmalıdır."
    if len(kod) == 13:
        beklenen = ean_kontrol_basamagi(kod[:12])
        if int(kod[-1]) != beklenen:
            return (
                False,
                "checksum",
                f"🚫 **Geçersiz EAN-13 Barkod (`{kod}`):** Yazım hatası var! "
                f"Kontrol basamağı **`{beklenen}`** olmalıydı (`{kod[-1]}` girildi). "
                "Yanlış ürün açılmaması için form kilitlendi.",
            )
        return True, "ean13", ""
    if len(kod) == 8:
        beklenen = ean_kontrol_basamagi(kod[:7])
        if int(kod[-1]) != beklenen:
            return (
                False,
                "checksum",
                f"🚫 **Geçersiz EAN-8 Barkod (`{kod}`):** Kontrol basamağı **`{beklenen}`** olmalıydı (`{kod[-1]}` girildi).",
            )
        return True, "ean8", ""
    return (
        False,
        "uzunluk",
        f"🚫 **Geçersiz Barkod Uzunluğu ({len(kod)} hane):** Standart 13 haneli EAN-13 veya 8 haneli EAN-8 girin.",
    )


def dosya_bayt_ve_imza(dosya_kaynak, yedek_yol: Path):
    if dosya_kaynak is not None:
        bayt = dosya_kaynak.getvalue()
        ad = getattr(dosya_kaynak, "name", "yuklenen.csv")
    else:
        bayt = yedek_yol.read_bytes()
        ad = yedek_yol.name
    imza = hashlib.sha256(bayt).hexdigest()[:12]
    return bayt, ad, imza


def guvenli_int_opsiyonel(deger):
    if pd.isna(deger) or str(deger).strip() == "":
        return None
    v = int(float(deger))
    return v if v > 0 else None


def guvenli_float_opsiyonel(deger):
    if pd.isna(deger) or str(deger).strip() == "":
        return None
    v = float(deger)
    return v if v > 0 else None


def barkod_coz(resim_baytlari: bytes):
    if zxingcpp is None or Image is None:
        return None, "eksik_paket"

    img = Image.open(io.BytesIO(resim_baytlari))
    img = ImageOps.exif_transpose(img)

    gri = img.convert("L")
    denemeler = [img, gri, ImageOps.autocontrast(gri)]

    if max(img.size) > 1400:
        kucuk = img.copy()
        kucuk.thumbnail((1400, 1400))
        kucuk_gri = kucuk.convert("L")
        denemeler.extend([kucuk, kucuk_gri, ImageOps.autocontrast(kucuk_gri)])

    for aday in denemeler:
        sonuclar = zxingcpp.read_barcodes(aday)
        for s in sonuclar:
            metin = (s.text or "").strip()
            if metin:
                return metin, str(s.format)

    return None, "okunamadi"


def barkod_tarayici_alani(prefix: str, baslik: str):
    st.session_state.setdefault(f"{prefix}_cam_sayac", 0)
    sayac = st.session_state[f"{prefix}_cam_sayac"]

    with st.expander(baslik, expanded=False):
        if zxingcpp is None:
            st.error(
                "Barkod çözücü kurulu değil. `requirements.txt` içinde `pillow` ve `zxing-cpp` bulunmalıdır."
            )
            return

        mod = st.radio(
            "Tarama Yöntemi",
            ["📷 Kamera ile Çek", "🖼️ Fotoğraf Yükle"],
            horizontal=True,
            key=f"{prefix}_tarama_mod",
        )

        if mod == "📷 Kamera ile Çek":
            gorsel = st.camera_input(
                "Barkodu net şekilde çekin (otomatik aktarılır)",
                key=f"{prefix}_cam_{sayac}",
            )
        else:
            gorsel = st.file_uploader(
                "Barkod fotoğrafı seçin (otomatik aktarılır)",
                type=["png", "jpg", "jpeg", "webp"],
                key=f"{prefix}_upload_{sayac}",
            )

        if gorsel is not None:
            kod, bilgi = barkod_coz(gorsel.getvalue())
            st.session_state[f"{prefix}_cam_sayac"] = sayac + 1

            if kod:
                st.session_state[f"{prefix}_bekleyen_barkod"] = kod
                st.session_state[f"{prefix}_tarama_mesaj"] = (
                    "ok",
                    f"📷 Barkod doğrudan okundu ({bilgi}): **`{kod}`**",
                )
            else:
                st.session_state[f"{prefix}_bekleyen_barkod"] = ""
                st.session_state[f"{prefix}_tarama_mesaj"] = (
                    "hata",
                    "❌ Barkod okunamadı. Yanlış ürüne kayıt önlemek için önceki seçim temizlendi — tekrar çekin.",
                )
            st.rerun()


# --- 1. KALICI DİSK KAYDI & FEFO SATIŞ DÜŞÜM MOTORU ---
def durumu_diske_yaz(durum: dict):
    with open(DURUM_DOSYASI, "w", encoding="utf-8") as f:
        json.dump(durum, f, ensure_ascii=False, indent=2)


def durumdan_oku():
    if DURUM_DOSYASI.exists():
        try:
            with open(DURUM_DOSYASI, "r", encoding="utf-8") as f:
                veri = json.load(f)
            if veri.get("surum") == AKTIF_SURUM:
                return veri
        except Exception:
            return None
    return None


def fefo_satis_uygula(stok_kayitlari: list, satis_haritasi: dict):
    # Yeni gün başlangıcı: mevcut kalan stok -> gün başı (Dun_Stok) olur
    for s in stok_kayitlari:
        s["Dun_Stok"] = int(s["Kalan_Stok"])
        s["Satilan_Adet"] = 0
        s["Coklu_Parti_Uyari"] = False

    gruplar = {}
    for s in stok_kayitlari:
        gruplar.setdefault(s["Barkod"], []).append(s)

    toplam_dusulen = 0
    eslesmeyen_barkodlar = []

    for barkod, satis_miktari in satis_haritasi.items():
        satis_miktari = int(satis_miktari)
        if satis_miktari <= 0:
            continue
        if barkod not in gruplar:
            eslesmeyen_barkodlar.append(barkod)
            continue

        aktif_partiler = [
            p for p in gruplar[barkod] if int(p["Kalan_Stok"]) > 0
        ]
        aktif_partiler.sort(key=lambda x: (x["SKT"], x["Parti_ID"]))

        havuz = satis_miktari
        for p in aktif_partiler:
            mevcut = int(p["Kalan_Stok"])
            dusulen = min(mevcut, havuz)
            p["Satilan_Adet"] = dusulen
            p["Kalan_Stok"] = mevcut - dusulen
            havuz -= dusulen
            toplam_dusulen += dusulen

        if len(aktif_partiler) >= 2:
            for idx, p in enumerate(aktif_partiler):
                yeni_aktif_var_mi = any(
                    diger["Kalan_Stok"] > 0 for diger in aktif_partiler[idx + 1:]
                )
                if p["Satilan_Adet"] > 0 and p["Kalan_Stok"] == 0 and yeni_aktif_var_mi:
                    p["Sifir_Dogrulama_Gerekli"] = True
                elif p["Satilan_Adet"] > 0 and p["Kalan_Stok"] > 0 and yeni_aktif_var_mi:
                    p["Coklu_Parti_Uyari"] = True

    return toplam_dusulen, eslesmeyen_barkodlar


def csvden_baslat(
    stok_dosya,
    satis_dosya,
    irsaliye_dosya,
    satis_dusulsun_mu: bool,
    baslangic_tarihi: datetime.date,
):
    stok_bayt, _, _ = dosya_bayt_ve_imza(stok_dosya, BASE_DIR / "dun_stok.csv")
    satis_bayt, satis_ad, satis_imza = dosya_bayt_ve_imza(satis_dosya, BASE_DIR / "t1_satis.csv")
    irs_bayt, _, _ = dosya_bayt_ve_imza(irsaliye_dosya, BASE_DIR / "irsaliye.csv")

    df_stok = pd.read_csv(io.BytesIO(stok_bayt), dtype={"Barkod": str})
    df_satis = pd.read_csv(io.BytesIO(satis_bayt), dtype={"Barkod": str})
    df_irsaliye = pd.read_csv(io.BytesIO(irs_bayt), dtype={"Barkod": str})

    df_stok["_skt_sira"] = pd.to_datetime(df_stok["SKT"])
    df_stok = df_stok.sort_values(by=["Barkod", "_skt_sira"]).reset_index(drop=True)

    stok_kayitlari = []
    for idx, r in df_stok.iterrows():
        barkod = str(r["Barkod"]).strip()
        dun_stok = int(r["Dun_Stok"])
        stok_kayitlari.append({
            "Parti_ID": f"P-{idx + 1:03d}",
            "Barkod": barkod,
            "Urun_Adi": str(r["Urun_Adi"]),
            "Reyon": str(r["Reyon"]),
            "Dun_Stok": dun_stok,
            "Satilan_Adet": 0,
            "Kalan_Stok": dun_stok,
            "Gunluk_Hiz": guvenli_int_opsiyonel(r.get("Gunluk_Hiz")),
            "SKT": str(r["SKT"]),
            "Birim_Fiyat": guvenli_float_opsiyonel(r.get("Birim_Fiyat")),
            "Kaynak": "T+1 FEFO Motoru" if satis_dusulsun_mu else "Gün Sonu Kapanış Stoğu",
            "Coklu_Parti_Uyari": False,
            "Sifir_Dogrulama_Gerekli": False,
            "Son_Aksiyon_Tarihi": None,
            "Son_Aksiyon_Tipi": "",
            "Son_Fiziksel_Kontrol": None,
            "Mudahale_Notu": "",
        })

    islenen_satis_imzalari = []
    if satis_dusulsun_mu and not df_satis.empty:
        satis_haritasi = {
            str(k).strip(): int(v)
            for k, v in df_satis.groupby("Barkod")["Satilan_Adet"].sum().to_dict().items()
        }
        toplam_dusulen, _ = fefo_satis_uygula(stok_kayitlari, satis_haritasi)
        islenen_satis_imzalari.append({
            "imza": satis_imza,
            "dosya": satis_ad,
            "tarih": baslangic_tarihi.isoformat(),
            "toplam_adet": toplam_dusulen,
        })
        uygulanan_mod = "Satış Öncesi Stok (T+1 FEFO Düşüldü)"
        son_imza = satis_imza
    else:
        uygulanan_mod = "Satış Sonrası Kapanış Stoğu (Satış Düşülmedi)"
        son_imza = "Satış Düşülmedi"

    katalog_map = {}
    for _, r in df_stok.iterrows():
        kat = REYON_VARSAYILAN_KATEGORI.get(r["Reyon"], "Paketli Atıştırmalık")
        b = str(r["Barkod"]).strip()
        katalog_map[b] = {
            "Barkod": b,
            "Urun_Adi": str(r["Urun_Adi"]),
            "Reyon": str(r["Reyon"]),
            "Kategori": kat,
            "Max_Raf_Omru_Gun": KATEGORI_MAX_OMUR.get(kat, 90),
            "Gunluk_Hiz": guvenli_int_opsiyonel(r.get("Gunluk_Hiz")),
            "Birim_Fiyat": guvenli_float_opsiyonel(r.get("Birim_Fiyat")),
        }

    irsaliye_listesi = []
    for idx, r in df_irsaliye.iterrows():
        b = str(r["Barkod"]).strip()
        katalog_map[b] = {
            "Barkod": b,
            "Urun_Adi": str(r["Urun_Adi"]),
            "Reyon": str(r["Reyon"]),
            "Kategori": str(r["Kategori"]),
            "Max_Raf_Omru_Gun": int(r["Max_Raf_Omru_Gun"]),
            "Gunluk_Hiz": guvenli_int_opsiyonel(r.get("Gunluk_Hiz")),
            "Birim_Fiyat": guvenli_float_opsiyonel(r.get("Birim_Fiyat")),
        }
        irsaliye_listesi.append({
            "Satir_ID": f"IRS-{idx + 1:03d}",
            "Barkod": b,
            "Urun_Adi": str(r["Urun_Adi"]),
            "Reyon": str(r["Reyon"]),
            "Kategori": str(r["Kategori"]),
            "Beklenen_Adet": int(r["Beklenen_Adet"]),
            "Max_Raf_Omru_Gun": int(r["Max_Raf_Omru_Gun"]),
            "Gunluk_Hiz": guvenli_int_opsiyonel(r.get("Gunluk_Hiz")),
            "Birim_Fiyat": guvenli_float_opsiyonel(r.get("Birim_Fiyat")),
        })

    durum = {
        "surum": AKTIF_SURUM,
        "stok_kayitlari": stok_kayitlari,
        "katalog": list(katalog_map.values()),
        "irsaliye": irsaliye_listesi,
        "islenen_irsaliye_idler": [],
        "kabul_kayitlari": [],
        "aksiyon_loglari": [],
        "satis_dusuldu_mu": satis_dusulsun_mu,
        "son_pos_gunu": baslangic_tarihi.isoformat(),
        "uygulanan_pos_modu": uygulanan_mod,
        "son_satis_imza": son_imza,
        "islenen_satis_imzalari": islenen_satis_imzalari,
        "parti_sayaci": len(stok_kayitlari) + 1,
    }
    durumu_diske_yaz(durum)
    return durum


# --- 2. DENETİM ÖNCELİKLİ + OPSİYONEL HIZLI FEFO ANALİZ MOTORU ---
def tum_partileri_analiz_et(stok_kayitlari: list, tarih: datetime.date) -> list:
    tarih_str = tarih.isoformat()

    kapasite_sonuclari = {}
    barkod_gruplari = {}
    for p in stok_kayitlari:
        barkod_gruplari.setdefault(p["Barkod"], []).append(p)

    for barkod, partiler in barkod_gruplari.items():
        aktifler = []
        for p in partiler:
            gun = (datetime.date.fromisoformat(p["SKT"]) - tarih).days
            stok = int(p["Kalan_Stok"])
            hiz = p.get("Gunluk_Hiz")
            if stok > 0 and gun >= 1 and hiz is not None and int(hiz) > 0:
                aktifler.append((gun, p["Parti_ID"], p))

        aktifler.sort(key=lambda x: (x[0], x[1]))
        kullanilan_kapasite = 0

        for gun, pid, p in aktifler:
            hiz = int(p["Gunluk_Hiz"])
            stok = int(p["Kalan_Stok"])
            toplam_kapasite = hiz * gun
            uygun_kapasite = max(0, toplam_kapasite - kullanilan_kapasite)
            satilabilir = min(stok, uygun_kapasite)
            elde_kalacak = stok - satilabilir
            kullanilan_kapasite += satilabilir

            kapasite_sonuclari[pid] = {
                "Paylasimli_Kapasite": uygun_kapasite,
                "Elde_Kalacak_Adet": elde_kalacak,
                "Onceki_Parti_Yuk": toplam_kapasite - uygun_kapasite,
            }

    sonuclar = []
    for p in stok_kayitlari:
        pid = p["Parti_ID"]
        stok = int(p["Kalan_Stok"])
        gun = (datetime.date.fromisoformat(p["SKT"]) - tarih).days
        hiz = p.get("Gunluk_Hiz")
        hiz_biliniyor = hiz is not None and int(hiz) > 0
        fiyat = p.get("Birim_Fiyat")
        fiyat_sayisal = float(fiyat) if fiyat is not None else 0.0

        kap = kapasite_sonuclari.get(
            pid,
            {"Paylasimli_Kapasite": None, "Elde_Kalacak_Adet": 0, "Onceki_Parti_Yuk": 0},
        )

        bugun_one_cekildi = (
            p.get("Son_Aksiyon_Tarihi") == tarih_str
            and p.get("Son_Aksiyon_Tipi") == "AKSIYON_ALINDI"
        )

        if p.get("Sifir_Dogrulama_Gerekli"):
            if gun <= 0:
                durum_kod = "KIRMIZI"
                katman = 5
                etiket = "⚖️ KRİTİK DENETİM (ARKADA KALMIŞ OLABİLİR)"
            elif gun == 1:
                durum_kod = "KIRMIZI"
                katman = 4
                etiket = "⚖️ DENETİM RİSKİ (ESKİ PARTİ DOĞRULA)"
            else:
                durum_kod = "TURUNCU"
                katman = 3
                etiket = "🔍 ESKİ PARTİ DOĞRULAMA"

            sonuc = {
                "Kalan_Gun": gun,
                "Durum": durum_kod,
                "Oncelik_Katmani": katman,
                "Oncelik_Etiketi": etiket,
                "Risk_Adet": 0,
                "Risk_TL": 0.0,
                "Satilabilir_Mi": gun > 0,
                "Gorev_Tipi": "SIFIR_DOGRULAMA",
                "Paylasimli_Kapasite": kap["Paylasimli_Kapasite"],
                "Elde_Kalacak_Adet": 0,
                "Aksiyon_Mesaji": (
                    f"Hesapta 0 görünüyor (dün {p['Satilan_Adet']} düşüldü) ama öndeki yeni koliden satılmış olabilir. "
                    f"Denetim riski: {p['SKT']} tarihli paketten rafta kaldı mı?"
                ),
            }
        elif stok <= 0:
            sonuc = {
                "Kalan_Gun": gun,
                "Durum": "YESIL",
                "Oncelik_Katmani": 1,
                "Oncelik_Etiketi": "🟢 TEMİZ",
                "Risk_Adet": 0,
                "Risk_TL": 0.0,
                "Satilabilir_Mi": False,
                "Gorev_Tipi": "TEMIZ",
                "Paylasimli_Kapasite": 0 if hiz_biliniyor else None,
                "Elde_Kalacak_Adet": 0,
                "Aksiyon_Mesaji": p.get("Mudahale_Notu") or "Stok sıfır — kontrole gerek yok",
            }
        elif gun <= 0:
            sonuc = {
                "Kalan_Gun": gun,
                "Durum": "KIRMIZI",
                "Oncelik_Katmani": 5,
                "Oncelik_Etiketi": "⚖️ KRİTİK DENETİM (SKT DOLDU)",
                "Risk_Adet": stok,
                "Risk_TL": round(stok * fiyat_sayisal, 2),
                "Satilabilir_Mi": False,
                "Gorev_Tipi": "SKT_DOLDU",
                "Paylasimli_Kapasite": 0,
                "Elde_Kalacak_Adet": stok,
                "Aksiyon_Mesaji": "SKT doldu — denetim riskini sıfırlamak için hemen raftan topla",
            }
        elif bugun_one_cekildi:
            sonuc = {
                "Kalan_Gun": gun,
                "Durum": "YESIL",
                "Oncelik_Katmani": 1,
                "Oncelik_Etiketi": "🟢 BUGÜN KONTROL EDİLDİ",
                "Risk_Adet": 0,
                "Risk_TL": 0.0,
                "Satilabilir_Mi": True,
                "Gorev_Tipi": "BUGUN_TAMAM",
                "Paylasimli_Kapasite": kap["Paylasimli_Kapasite"],
                "Elde_Kalacak_Adet": kap["Elde_Kalacak_Adet"],
                "Aksiyon_Mesaji": f"Bugün ({tarih.strftime('%d.%m')}) öne çekildi — yarın stok kalırsa tekrar uyaracak",
            }
        elif gun == 1:
            sonuc = {
                "Kalan_Gun": gun,
                "Durum": "KIRMIZI",
                "Oncelik_Katmani": 4,
                "Oncelik_Etiketi": "⚖️ DENETİM RİSKİ (YARIN SON GÜN)",
                "Risk_Adet": stok,
                "Risk_TL": round(stok * fiyat_sayisal, 2),
                "Satilabilir_Mi": True,
                "Gorev_Tipi": "SON_GUN",
                "Paylasimli_Kapasite": kap["Paylasimli_Kapasite"],
                "Elde_Kalacak_Adet": kap["Elde_Kalacak_Adet"],
                "Aksiyon_Mesaji": "Yarın son gün — raftaki adedi doğrula, acil indirim/öne çek",
            }
        elif not hiz_biliniyor:
            if gun <= 3:
                sonuc = {
                    "Kalan_Gun": gun,
                    "Durum": "TURUNCU",
                    "Oncelik_Katmani": 2,
                    "Oncelik_Etiketi": "🟠 HIZ BİLİNMİYOR (TARİH UYARISI)",
                    "Risk_Adet": stok,
                    "Risk_TL": round(stok * fiyat_sayisal, 2),
                    "Satilabilir_Mi": True,
                    "Gorev_Tipi": "HIZ_BILINMIYOR_TARIH",
                    "Paylasimli_Kapasite": None,
                    "Elde_Kalacak_Adet": 0,
                    "Aksiyon_Mesaji": (
                        f"Satış hızı henüz bilinmiyor (POS geçmişi bekleniyor). "
                        f"SKT'ye {gun} gün kaldı — raftan kontrol et / öne çek"
                    ),
                }
            else:
                sonuc = {
                    "Kalan_Gun": gun,
                    "Durum": "YESIL",
                    "Oncelik_Katmani": 1,
                    "Oncelik_Etiketi": "🟢 TEMİZ (HIZ BEKLENİYOR)",
                    "Risk_Adet": 0,
                    "Risk_TL": 0.0,
                    "Satilabilir_Mi": True,
                    "Gorev_Tipi": "TEMIZ",
                    "Paylasimli_Kapasite": None,
                    "Elde_Kalacak_Adet": 0,
                    "Aksiyon_Mesaji": "Satış hızı henüz bilinmiyor — güvenli raf ömrü (>3 gün)",
                }
        elif kap["Elde_Kalacak_Adet"] > 0:
            elde_kalacak = kap["Elde_Kalacak_Adet"]
            paylasim_notu = (
                f" (önceki partiler {kap['Onceki_Parti_Yuk']} adet kapasiteyi dolduruyor)"
                if kap["Onceki_Parti_Yuk"] > 0
                else ""
            )
            sonuc = {
                "Kalan_Gun": gun,
                "Durum": "TURUNCU",
                "Oncelik_Katmani": 2,
                "Oncelik_Etiketi": "🟠 HIZ / FİRE RİSKİ",
                "Risk_Adet": elde_kalacak,
                "Risk_TL": round(elde_kalacak * fiyat_sayisal, 2),
                "Satilabilir_Mi": True,
                "Gorev_Tipi": "HIZ_RISKI",
                "Paylasimli_Kapasite": kap["Paylasimli_Kapasite"],
                "Elde_Kalacak_Adet": elde_kalacak,
                "Aksiyon_Mesaji": (
                    f"{gun} gün kaldı; {int(hiz)}/gün hızla bu partiye kalan kapasite {kap['Paylasimli_Kapasite']} adet"
                    f"{paylasim_notu}. {elde_kalacak} adet elde kalacak — indirim & öne çek"
                ),
            }
        elif p.get("Coklu_Parti_Uyari"):
            sonuc = {
                "Kalan_Gun": gun,
                "Durum": "TURUNCU",
                "Oncelik_Katmani": 3,
                "Oncelik_Etiketi": "🔍 ÇOKLU SKT DOĞRULAMA",
                "Risk_Adet": stok,
                "Risk_TL": round(stok * fiyat_sayisal, 2),
                "Satilabilir_Mi": True,
                "Gorev_Tipi": "COKLU_DOGRULAMA",
                "Paylasimli_Kapasite": kap["Paylasimli_Kapasite"],
                "Elde_Kalacak_Adet": 0,
                "Aksiyon_Mesaji": f"Rafta 2 farklı SKT var — {p['SKT']} tarihli paketten gerçekten kaç adet kaldığını doğrula",
            }
        else:
            sonuc = {
                "Kalan_Gun": gun,
                "Durum": "YESIL",
                "Oncelik_Katmani": 1,
                "Oncelik_Etiketi": "🟢 TEMİZ",
                "Risk_Adet": 0,
                "Risk_TL": 0.0,
                "Satilabilir_Mi": True,
                "Gorev_Tipi": "TEMIZ",
                "Paylasimli_Kapasite": kap["Paylasimli_Kapasite"],
                "Elde_Kalacak_Adet": 0,
                "Aksiyon_Mesaji": "Güvenli raf ömrü — kontrole gerek yok",
            }

        sonuclar.append({**p, **sonuc})

    return sonuclar


def oncelik_sirala_anahtari(x: dict):
    return (
        x["Oncelik_Katmani"],
        -x["Kalan_Gun"],
        1 if not x.get("Son_Fiziksel_Kontrol") else 0,
        x["Risk_TL"],
    )


# --- 3. BAŞLANGIÇ DURUMU & YAN MENÜ ---
if "bekleyen_tarih" in st.session_state:
    st.session_state["aktif_tarih"] = st.session_state.pop("bekleyen_tarih")

if "durum" not in st.session_state:
    disk_durum = durumdan_oku()
    if disk_durum is not None:
        st.session_state.durum = disk_durum
    else:
        try:
            st.session_state.durum = csvden_baslat(
                None, None, None, True, VARSAYILAN_TARIH
            )
        except FileNotFoundError as e:
            st.error(f"Eksik CSV dosyası: {e.filename}. Tüm CSV dosyaları `app.py` ile aynı klasörde olmalı.")
            st.stop()

durum = st.session_state.durum
son_pos_tarih_obj = datetime.date.fromisoformat(durum["son_pos_gunu"])
st.session_state.setdefault("aktif_tarih", son_pos_tarih_obj)

st.sidebar.header("⚙️ Siyah Kutu v0.3.3")
kontrol_tarihi = st.sidebar.date_input(
    "Aktif Radar Tarihi",
    key="aktif_tarih",
    format="DD.MM.YYYY",
)

# --- YENİ GÜN T+1 AKIŞI (STOK KORUNUR + ÇİFT DOSYA KİLİDİ) ---
st.sidebar.divider()
st.sidebar.subheader("☀️ Yeni Gün T+1 Satışı İşle")
st.sidebar.caption("Mevcut stoğu ve yeni gelen partileri silmeden sadece ertesi günün satışını FEFO düşer.")

yeni_gun_satis_dosya = st.sidebar.file_uploader(
    "Yeni Gün Satış CSV (Barkod, Satilan_Adet)",
    type=["csv"],
    key="yeni_gun_csv_uploader",
)
yeni_gun_tarih = st.sidebar.date_input(
    "İşlenecek Yeni POS Günü",
    value=son_pos_tarih_obj + datetime.timedelta(days=1),
    format="DD.MM.YYYY",
    key="yeni_gun_tarih_input",
)

if st.sidebar.button(
    "☀️ Yeni Gün Satışını FEFO Düş",
    type="primary",
    disabled=(yeni_gun_satis_dosya is None),
    use_container_width=True,
):
    bayt = yeni_gun_satis_dosya.getvalue()
    imza = hashlib.sha256(bayt).hexdigest()[:12]
    mevcut_kayit = next(
        (k for k in durum.get("islenen_satis_imzalari", []) if k["imza"] == imza),
        None,
    )
    if mevcut_kayit:
        st.session_state["pos_islem_mesaj"] = (
            "hata",
            f"🚫 **ÇİFT İŞLEME KİLİDİ:** Bu satış dosyası (`{imza}`) daha önce "
            f"**{mevcut_kayit['tarih']}** günü için işlendi ({mevcut_kayit['toplam_adet']} adet düşüldü). "
            "Aynı POS dosyasının ikinci kez düşülmesi engellendi.",
        )
    else:
        try:
            df_yeni_satis = pd.read_csv(io.BytesIO(bayt), dtype={"Barkod": str})
            if not {"Barkod", "Satilan_Adet"}.issubset(df_yeni_satis.columns):
                st.session_state["pos_islem_mesaj"] = (
                    "hata",
                    "❌ CSV dosyasında `Barkod` ve `Satilan_Adet` sütunları bulunmalıdır.",
                )
            else:
                satis_haritasi = {
                    str(k).strip(): int(v)
                    for k, v in df_yeni_satis.groupby("Barkod")["Satilan_Adet"].sum().to_dict().items()
                }
                toplam_dusulen, eslesmeyen = fefo_satis_uygula(durum["stok_kayitlari"], satis_haritasi)
                durum["son_pos_gunu"] = yeni_gun_tarih.isoformat()
                durum["uygulanan_pos_modu"] = "Artımlı Yeni Gün T+1 (FEFO Düşüldü)"
                durum["son_satis_imza"] = imza
                durum.setdefault("islenen_satis_imzalari", []).append({
                    "imza": imza,
                    "dosya": yeni_gun_satis_dosya.name,
                    "tarih": yeni_gun_tarih.isoformat(),
                    "toplam_adet": toplam_dusulen,
                })
                durumu_diske_yaz(durum)
                st.session_state["bekleyen_tarih"] = yeni_gun_tarih
                ek_uyari = f" (Stokta bulunmayan {len(eslesmeyen)} barkod atlandı)" if eslesmeyen else ""
                st.session_state["pos_islem_mesaj"] = (
                    "ok",
                    f"✅ **{yeni_gun_tarih.strftime('%d.%m.%Y')}** T+1 satışı işlendi: "
                    f"toplam **{toplam_dusulen} adet** FEFO sırasıyla düşüldü (İmza: `{imza}`).{ek_uyari}",
                )
        except Exception as exc:
            st.session_state["pos_islem_mesaj"] = ("hata", f"❌ Satış dosyası okunamadı: {exc}")
    st.rerun()

# --- DEMO SIFIRLAMA (AYRI BÖLÜM) ---
st.sidebar.divider()
with st.sidebar.expander("🔄 Demo Başlangıcı / Sıfırlama", expanded=False):
    st.caption("Dikkat: Sıfırlama tüm manuel girişleri silip başlangıç CSV'lerinden yeniden kurar.")
    pos_stok_tipi = st.radio(
        "Başlangıç POS Stok Saati",
        [
            "Satış Öncesi Stok (T+1 Satışı FEFO Düş)",
            "Satış Sonrası Kapanış Stoğu (Tekrar Düşme)",
        ],
        index=0 if durum.get("satis_dusuldu_mu", True) else 1,
    )
    satis_dusulsun_mu = pos_stok_tipi.startswith("Satış Öncesi")

    if durum.get("satis_dusuldu_mu") != satis_dusulsun_mu:
        st.warning("Seçili başlangıç modu henüz uygulanmadı. Uygulamak için aşağıdaki 'Demoyu Sıfırla' butonuna basın.")

    stok_dosya = st.file_uploader("Dün Stok CSV (Opsiyonel)", type=["csv"], key="reset_stok")
    satis_dosya = st.file_uploader("Gece T+1 Satış CSV (Opsiyonel)", type=["csv"], key="reset_satis")
    irsaliye_dosya = st.file_uploader("Gizli İrsaliye CSV (Opsiyonel)", type=["csv"], key="reset_irs")

    if st.button("🔄 Demoyu Sıfırla (Baştan Kur)", use_container_width=True):
        if DURUM_DOSYASI.exists():
            DURUM_DOSYASI.unlink()
        st.session_state.durum = csvden_baslat(
            stok_dosya, satis_dosya, irsaliye_dosya, satis_dusulsun_mu, VARSAYILAN_TARIH
        )
        st.session_state["bekleyen_tarih"] = VARSAYILAN_TARIH
        st.session_state["bekleyen_kabul_uyari"] = None
        st.session_state["son_ilk_giris_mesaj"] = None
        st.session_state["s3_son_islem_mesaj"] = None
        st.session_state["pos_islem_mesaj"] = None
        st.session_state["s2_bekleyen_barkod"] = ""
        st.session_state["s3_bekleyen_barkod"] = ""
        st.rerun()

yarin_satirlari = [
    {
        "Barkod": p["Barkod"],
        "Urun_Adi": p["Urun_Adi"],
        "Reyon": p["Reyon"],
        "Dun_Stok": p["Kalan_Stok"],
        "Gunluk_Hiz": "" if p.get("Gunluk_Hiz") is None else p["Gunluk_Hiz"],
        "SKT": p["SKT"],
        "Birim_Fiyat": "" if p.get("Birim_Fiyat") is None else p["Birim_Fiyat"],
    }
    for p in durum["stok_kayitlari"]
    if int(p["Kalan_Stok"]) > 0
]
yarin_csv = pd.DataFrame(yarin_satirlari).to_csv(index=False).encode("utf-8-sig")
st.sidebar.download_button(
    "📥 Yarının Başlangıç Stoğunu İndir (CSV)",
    data=yarin_csv,
    file_name="yarin_baslangic_stok.csv",
    mime="text/csv",
    use_container_width=True,
)


# --- 4. ANA BAŞLIK VE 3 SEKME ---
st.title("🟩 Siyah Kutu v0.3.3 — Saha Pilot Sürümü")

pos_mesaj = st.session_state.get("pos_islem_mesaj")
if pos_mesaj:
    if pos_mesaj[0] == "ok":
        st.success(pos_mesaj[1])
    else:
        st.error(pos_mesaj[1])

tab1, tab2, tab3 = st.tabs([
    "🗺️ 1. Sabah T+1 Renk Radarı",
    "➕ 2. İlk Ürün / Parti Girişi",
    "📦 3. Kör Sayım (Karma SKT)",
])

# ==============================================================================
# SEKME 1: SABAH T+1 RENK RADARI (DENETİM ÖNCELİKLİ + ŞEFFAF POS BANDI)
# ==============================================================================
with tab1:
    son_pos_gosterim = datetime.date.fromisoformat(durum["son_pos_gunu"]).strftime("%d.%m.%Y")
    st.info(
        f"📡 **Aktif Radar Verisi** · Son İşlenen POS Günü: **{son_pos_gosterim}** · "
        f"Fiilen Uygulanan Stok Modu: **{durum['uygulanan_pos_modu']}** · "
        f"Satış Dosyası İmzası: **`{durum['son_satis_imza']}`**"
    )
    if kontrol_tarihi.isoformat() != durum["son_pos_gunu"]:
        st.warning(
            f"📅 Takvimde **{kontrol_tarihi.strftime('%d.%m.%Y')}** görünümündesiniz; "
            f"son işlenen POS verisi **{son_pos_gosterim}** tarihine aittir. "
            "Yeni günün satışını düşmek için sol menüdeki **'☀️ Yeni Gün T+1 Satışı İşle'** bölümünü kullanın."
        )

    analizli_partiler = tum_partileri_analiz_et(durum["stok_kayitlari"], kontrol_tarihi)

    reyon_sirasi = list(dict.fromkeys(p["Reyon"] for p in analizli_partiler))
    reyon_ozetleri = []
    for reyon in reyon_sirasi:
        alt = [p for p in analizli_partiler if p["Reyon"] == reyon]
        alt.sort(key=oncelik_sirala_anahtari, reverse=True)
        en_kotu = alt[0]["Durum"]
        acik_gorev = sum(1 for x in alt if x["Durum"] != "YESIL")

        reyon_ozetleri.append({
            "Reyon": reyon,
            "Durum": en_kotu,
            "Acik_Gorev": acik_gorev,
            "Toplam_Risk_TL": sum(x["Risk_TL"] for x in alt),
            "Dunku_Satis": sum(int(x["Satilan_Adet"]) for x in alt),
            "Kalan_Stok": sum(int(x["Kalan_Stok"]) for x in alt),
            "Odak_Urun": alt[0]["Urun_Adi"],
            "Oncelik_Etiketi": alt[0]["Oncelik_Etiketi"],
            "Aksiyon": alt[0]["Aksiyon_Mesaji"],
        })

    toplam_reyon = len(reyon_ozetleri)
    yesil_reyon = sum(1 for r in reyon_ozetleri if r["Durum"] == "YESIL")
    saglik_skoru = int((yesil_reyon / toplam_reyon) * 100) if toplam_reyon else 100
    aktif_risk_tl = sum(r["Toplam_Risk_TL"] for r in reyon_ozetleri)
    denetim_sayisi = sum(1 for p in analizli_partiler if p["Oncelik_Katmani"] >= 4)
    gerceklesen_fire_tl = sum(log.get("Gerceklesen_Fire_TL", 0.0) for log in durum["aksiyon_loglari"])

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Mağaza Sağlık Skoru", f"%{saglik_skoru}", f"{yesil_reyon}/{toplam_reyon} Reyon Yeşil")
    k2.metric("⚖️ Kritik Denetim Görevi", f"{denetim_sayisi} Parti")
    k3.metric("Aktif Fire Riski (TL)", f"{aktif_risk_tl:,.0f} TL")
    k4.metric("Gerçekleşen Fire (TL)", f"{gerceklesen_fire_tl:,.0f} TL")

    st.divider()

    st.subheader("🗺️ 2D Mağaza Reyon Haritası")
    renk_stili = {
        "KIRMIZI": ("#fef2f2", "#dc2626", "#991b1b", "🔴 KRİTİK / DENETİM"),
        "TURUNCU": ("#fff7ed", "#ea580c", "#9a3412", "🟠 İNDİRİM / DOĞRULAMA"),
        "YESIL":   ("#f0fdf4", "#16a34a", "#166534", "🟢 TEMİZ"),
    }

    for i in range(0, len(reyon_ozetleri), 3):
        cols = st.columns(3)
        for j, col in enumerate(cols):
            if i + j >= len(reyon_ozetleri):
                continue
            r = reyon_ozetleri[i + j]
            bg, border, title_color, etiket = renk_stili[r["Durum"]]
            gorev_yazisi = f"{r['Acik_Gorev']} parti bekliyor" if r["Acik_Gorev"] > 0 else "Tüm partiler temiz"

            with col:
                st.markdown(
                    f"""
                    <div style="
                        background-color: {bg};
                        border: 2px solid {border};
                        border-radius: 12px;
                        padding: 15px;
                        margin-bottom: 10px;
                        color: #1f2937;
                        min-height: 190px;
                    ">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <strong style="font-size:18px; color:{title_color};">{r['Reyon']}</strong>
                            <span style="font-size:11px; font-weight:700; color:{title_color};">{etiket}</span>
                        </div>
                        <hr style="border:none; border-top:1px solid {border}40; margin:8px 0;">
                        <div style="font-size:13px; margin-bottom:4px;"><strong>Durum:</strong> {gorev_yazisi}</div>
                        <div style="font-size:13px; margin-bottom:4px;"><strong>En Acil Ürün:</strong> {r['Odak_Urun']}</div>
                        <div style="font-size:13px; margin-bottom:6px;">
                            <strong>Dün Satılan:</strong> {r['Dunku_Satis']} | <strong>Kalan:</strong> {r['Kalan_Stok']} | <strong>Risk:</strong> {r['Toplam_Risk_TL']:,.0f} TL
                        </div>
                        <div style="font-size:12px; background:#ffffffaa; padding:6px 8px; border-radius:6px;">
                            👉 {r['Aksiyon']}
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

    st.divider()

    st.subheader("🎯 Ürün Bazlı Sabah Görevleri (Denetim & Kalan Gün Öncelikli)")
    acik_partiler = [p for p in analizli_partiler if p["Durum"] != "YESIL"]
    acik_partiler.sort(key=oncelik_sirala_anahtari, reverse=True)

    def kardes_stok_dengele(hedef_parti: dict, fark: int):
        if fark == 0:
            return
        kardesler = [
            k for k in durum["stok_kayitlari"]
            if k["Barkod"] == hedef_parti["Barkod"]
            and k["Parti_ID"] != hedef_parti["Parti_ID"]
            and k["SKT"] >= hedef_parti["SKT"]
        ]
        kardesler.sort(key=lambda x: x["SKT"])

        if fark > 0:
            kalan_fark = fark
            for k in kardesler:
                dus = min(int(k["Kalan_Stok"]), kalan_fark)
                k["Kalan_Stok"] = int(k["Kalan_Stok"]) - dus
                kalan_fark -= dus
                if kalan_fark <= 0:
                    break
        else:
            eklenecek = abs(fark)
            for k in kardesler:
                max_geri = max(0, int(k["Dun_Stok"]) - int(k["Kalan_Stok"]))
                ekle = min(max_geri, eklenecek)
                k["Kalan_Stok"] = int(k["Kalan_Stok"]) + ekle
                eklenecek -= ekle
                if eklenecek <= 0:
                    break

    def partiyi_guncelle(parti_analiz: dict, yeni_tip: str, sayilan: int, islem_adi: str):
        hedef_pid = parti_analiz["Parti_ID"]
        hedef_parti = next((k for k in durum["stok_kayitlari"] if k["Parti_ID"] == hedef_pid), None)
        if hedef_parti is None:
            return

        tarih_str = kontrol_tarihi.isoformat()
        eski_tahmin = int(hedef_parti["Kalan_Stok"])
        coklu_kontrol = hedef_parti.get("Coklu_Parti_Uyari") or hedef_parti.get("Sifir_Dogrulama_Gerekli")

        if coklu_kontrol and sayilan != eski_tahmin:
            kardes_stok_dengele(hedef_parti, sayilan - eski_tahmin)

        hedef_parti["Coklu_Parti_Uyari"] = False
        hedef_parti["Sifir_Dogrulama_Gerekli"] = False
        hedef_parti["Son_Aksiyon_Tarihi"] = tarih_str
        hedef_parti["Son_Aksiyon_Tipi"] = yeni_tip
        hedef_parti["Son_Fiziksel_Kontrol"] = tarih_str
        hedef_parti["Mudahale_Notu"] = f"{islem_adi} ({kontrol_tarihi.strftime('%d.%m.%Y')})"

        fiyat_sayisal = float(hedef_parti["Birim_Fiyat"]) if hedef_parti.get("Birim_Fiyat") is not None else 0.0
        kurtarilan = 0.0
        fire = 0.0

        if yeni_tip == "AKSIYON_ALINDI":
            hedef_parti["Kalan_Stok"] = sayilan
            kalan_gun = parti_analiz["Kalan_Gun"]
            kapasite = parti_analiz["Paylasimli_Kapasite"]
            if kalan_gun <= 1 or kapasite is None:
                riskli_adet = sayilan
            else:
                riskli_adet = max(0, sayilan - kapasite)
            kurtarilan = round(riskli_adet * fiyat_sayisal, 2)
        elif yeni_tip == "TUKENDI":
            hedef_parti["Kalan_Stok"] = 0
        elif yeni_tip == "FIRE_AYRILDI":
            fire = round(sayilan * fiyat_sayisal, 2)
            hedef_parti["Kalan_Stok"] = 0

        durum["aksiyon_loglari"].insert(0, {
            "Tarih": kontrol_tarihi.strftime("%d.%m.%Y"),
            "Saat": datetime.datetime.now().strftime("%H:%M"),
            "Reyon": hedef_parti["Reyon"],
            "Urun_Adi": hedef_parti["Urun_Adi"],
            "SKT": hedef_parti["SKT"],
            "Islem": islem_adi,
            "Fiziksel_Adet": sayilan,
            "Potansiyel_Kurtarilan_TL": kurtarilan,
            "Gerceklesen_Fire_TL": fire,
        })
        durumu_diske_yaz(durum)

    if not acik_partiler:
        st.success(f"🎉 {kontrol_tarihi.strftime('%d.%m.%Y')} için tüm riskli ve denetim gerektiren partiler kapatıldı. Mağaza %100 Yeşil.")
    else:
        for sira_no, p in enumerate(acik_partiler, start=1):
            pid = p["Parti_ID"]
            ikon = "🔴" if p["Durum"] == "KIRMIZI" else "🟠"
            sifir_mi = p["Gorev_Tipi"] == "SIFIR_DOGRULAMA"
            hiz_metin = f"{int(p['Gunluk_Hiz'])}/gün" if p.get("Gunluk_Hiz") is not None else "Bilinmiyor"
            kontrol_notu = p.get("Son_Fiziksel_Kontrol") or "Henüz sayılmadı"

            with st.container(border=True):
                st.markdown(
                    f"**#{sira_no} · {ikon} {p['Reyon']} → {p['Urun_Adi']}** "
                    f"(Parti: `{pid}` · SKT: **{p['SKT']}** · Kalan Gün: **{p['Kalan_Gun']}** · {p['Oncelik_Etiketi']})"
                )
                st.caption(
                    f"Tahmini Kalan: **{p['Kalan_Stok']} adet** (Dün düşülen: {p['Satilan_Adet']}) | "
                    f"Satış Hızı: **{hiz_metin}** | Son Fiziksel Kontrol: **{kontrol_notu}** | "
                    f"Fire Riski: **{p['Risk_TL']:,.0f} TL** — {p['Aksiyon_Mesaji']}"
                )

                c_sayim, c_b1, c_b2, c_b3 = st.columns([1.2, 1.3, 1.15, 1.2])
                with c_sayim:
                    soru = (
                        f"{p['SKT']} tarihli paketten rafta kaldı mı?"
                        if sifir_mi
                        else f"Rafta ({p['SKT']}) kaç adet var?"
                    )
                    fiziksel_adet = st.number_input(
                        soru,
                        min_value=0,
                        max_value=500,
                        value=int(p["Kalan_Stok"]),
                        step=1,
                        key=f"sayim_{pid}_{kontrol_tarihi.isoformat()}",
                    )

                with c_b1:
                    st.write("")
                    if p["Satilabilir_Mi"]:
                        buton_ad = "🟠 Arkada Buldum / Öne Çek" if sifir_mi else "🟠 Öne Çek / İndirim"
                        if st.button(buton_ad, key=f"btn_one_{pid}", use_container_width=True):
                            if fiziksel_adet <= 0:
                                st.warning("Öne çekmek için adedi 0'dan büyük girin veya 'Rafta Kalmadı'ya basın.")
                            else:
                                partiyi_guncelle(p, "AKSIYON_ALINDI", fiziksel_adet, "Öne Çekildi / İndirim (Bugün)")
                                st.rerun()
                    else:
                        st.button("🚫 Satılamaz (SKT Doldu)", key=f"btn_dis_{pid}", disabled=True, use_container_width=True)

                with c_b2:
                    st.write("")
                    bitti_ad = "✅ 0 / Rafta Kalmadı" if sifir_mi else "✅ Rafta Yok / Tükendi"
                    if st.button(bitti_ad, key=f"btn_tuk_{pid}", use_container_width=True):
                        partiyi_guncelle(p, "TUKENDI", 0, "Sıfır Doğrulandı / Tükendi")
                        st.rerun()

                with c_b3:
                    st.write("")
                    fire_ad = "🗑️ Buldum (Fire / İade)" if sifir_mi else "🗑️ Fire / İadeye Ayır"
                    if st.button(fire_ad, key=f"btn_fir_{pid}", use_container_width=True):
                        if fiziksel_adet <= 0:
                            st.warning("Fire kaydı için ayrılan adedi 0'dan büyük girin.")
                        else:
                            partiyi_guncelle(p, "FIRE_AYRILDI", fiziksel_adet, "Fire / İadeye Ayrıldı")
                            st.rerun()

    if durum["aksiyon_loglari"]:
        st.divider()
        st.subheader("🧾 Sabah Müdahale & Fire Defteri")
        st.dataframe(pd.DataFrame(durum["aksiyon_loglari"]), use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("📊 Tüm Partiler (Paylaşımlı FEFO Canlı Tablo)")
    tablo_df = pd.DataFrame(analizli_partiler)[[
        "Parti_ID", "Barkod", "Reyon", "Urun_Adi", "Dun_Stok", "Satilan_Adet",
        "Kalan_Stok", "Gunluk_Hiz", "SKT", "Kalan_Gun",
        "Paylasimli_Kapasite", "Elde_Kalacak_Adet", "Oncelik_Etiketi", "Risk_TL"
    ]].copy()
    tablo_df["Gunluk_Hiz"] = tablo_df["Gunluk_Hiz"].apply(lambda v: "Bilinmiyor" if pd.isna(v) else f"{int(v)}/gün")
    tablo_df["Paylasimli_Kapasite"] = tablo_df["Paylasimli_Kapasite"].apply(
        lambda v: "Hız Bekleniyor" if pd.isna(v) else int(v)
    )
    st.dataframe(tablo_df, use_container_width=True, hide_index=True)

# ==============================================================================
# SEKME 2: İLK ÜRÜN / PARTİ GİRİŞİ (EAN-13 KONTROLÜ + OPSİYONEL HIZ/FİYAT)
# ==============================================================================
with tab2:
    st.subheader("➕ Yeni Ürün / Parti Girişi")
    st.caption(
        "Barkod Mod-10 kontrol basamağı anında doğrulanır. Yeni üründe fiyat ve günlük hız opsiyoneldir; "
        "POS geçmişi oluşana kadar salt tarih kuralıyla izlenir."
    )

    st.session_state.setdefault("s2_barkod", "")
    st.session_state.setdefault("s2_son_barkod", "")
    st.session_state.setdefault("s2_form_versiyon", 0)

    if "s2_bekleyen_barkod" in st.session_state:
        yeni_b = st.session_state.pop("s2_bekleyen_barkod")
        st.session_state["s2_barkod"] = yeni_b
        st.session_state["s2_son_barkod"] = yeni_b
        st.session_state["s2_form_versiyon"] += 1

    if st.session_state.get("son_ilk_giris_mesaj"):
        st.success(st.session_state.son_ilk_giris_mesaj)

    tarama_s2 = st.session_state.get("s2_tarama_mesaj")
    if tarama_s2:
        if tarama_s2[0] == "ok":
            st.success(tarama_s2[1])
        else:
            st.error(tarama_s2[1])

    barkod_tarayici_alani("s2", "📷 Barkod Tara (Kamera veya Fotoğraf — Otomatik Aktarım)")

    c_b1, c_b2 = st.columns([3, 1])
    with c_b1:
        barkod_giris = st.text_input(
            "Barkod Numarası (EAN-13 / EAN-8)",
            key="s2_barkod",
            placeholder="Yukarıdan okutun veya 13 haneli EAN-13 yazıp Enter'a basın...",
        ).strip()
    with c_b2:
        st.write("")
        st.write("")
        if st.button("🧹 Formu Temizle", key="s2_temizle", use_container_width=True):
            st.session_state["s2_bekleyen_barkod"] = ""
            st.session_state["s2_tarama_mesaj"] = None
            st.session_state["son_ilk_giris_mesaj"] = None
            st.rerun()

    if barkod_giris != st.session_state["s2_son_barkod"]:
        st.session_state["s2_son_barkod"] = barkod_giris
        st.session_state["s2_form_versiyon"] += 1

    form_v = st.session_state["s2_form_versiyon"]
    katalog_map = {k["Barkod"]: k for k in durum["katalog"]}
    gecerli_mi, _, hata_metni = barkod_dogrula(barkod_giris, set(katalog_map.keys()))

    if not barkod_giris:
        st.info("👆 Kayıt açmak için yukarıdan bir ürün barkodu okutun veya geçerli bir EAN-13 barkod yazın.")
    elif not gecerli_mi:
        st.error(hata_metni)
    else:
        kayitli = katalog_map.get(barkod_giris)

        if kayitli:
            urun_adi = kayitli["Urun_Adi"]
            reyon = kayitli["Reyon"]
            kategori = kayitli["Kategori"]
            max_omur = int(kayitli["Max_Raf_Omru_Gun"])
            birim_fiyat = kayitli.get("Birim_Fiyat")
            gunluk_hiz = kayitli.get("Gunluk_Hiz")
            fiyat_yazi = f"{float(birim_fiyat):.0f} TL" if birim_fiyat is not None else "Bilinmiyor"
            hiz_yazi = f"{int(gunluk_hiz)}/gün" if gunluk_hiz is not None else "Henüz bilinmiyor"
            st.info(
                f"✅ **Katalogdan Otomatik Tanındı:** **{urun_adi}** | Reyon: **{reyon}** | "
                f"Kategori: **{kategori}** (Max {max_omur} gün) | Fiyat: **{fiyat_yazi}** | Hız: **{hiz_yazi}**"
            )
        else:
            st.warning(
                f"🆕 **Geçerli Yeni Barkod (`{barkod_giris}`):** Önceki ürün temizlendi. "
                "Yalnızca yıldızlı (*) temel alanları doldurmanız yeterlidir."
            )
            n1, n2 = st.columns(2)
            with n1:
                urun_adi = st.text_input(
                    "Ürün Adı *",
                    value="",
                    placeholder="Örn: İçim Günlük Süt 1L",
                    key=f"s2_ad_{form_v}",
                ).strip()
                reyon = st.selectbox(
                    "Reyon *",
                    list(REYON_VARSAYILAN_KATEGORI.keys()),
                    index=None,
                    placeholder="Reyon seçin...",
                    key=f"s2_reyon_{form_v}",
                )
            with n2:
                kategori = st.selectbox(
                    "Kategori (Raf Ömrü Kilidi) *",
                    list(KATEGORI_MAX_OMUR.keys()),
                    index=None,
                    placeholder="Kategori seçin...",
                    key=f"s2_kat_{form_v}",
                )
                max_omur = KATEGORI_MAX_OMUR.get(kategori, 90) if kategori else None

            n3, n4 = st.columns(2)
            with n3:
                birim_fiyat = st.number_input(
                    "Birim Fiyat (TL — Opsiyonel)",
                    min_value=1.0,
                    max_value=5000.0,
                    value=None,
                    step=1.0,
                    placeholder="Boş bırakılabilir (POS'tan gelir)",
                    key=f"s2_fiyat_{form_v}",
                )
            with n4:
                gunluk_hiz = st.number_input(
                    "Günlük Satış Hızı (Opsiyonel)",
                    min_value=1,
                    max_value=500,
                    value=None,
                    step=1,
                    placeholder="Boş = Hız henüz bilinmiyor",
                    key=f"s2_hiz_{form_v}",
                )

        g1, g2 = st.columns(2)
        with g1:
            yeni_adet = st.number_input(
                "Raftaki / Gelen Adet *",
                min_value=1,
                max_value=1000,
                value=None,
                step=1,
                placeholder="Fiziksel adedi yazın...",
                key=f"s2_adet_{form_v}",
            )
        with g2:
            secilen_skt = st.date_input(
                "Paket Üzerindeki SKT *",
                value=None,
                format="DD.MM.YYYY",
                key=f"s2_skt_{form_v}",
            )

        eksikler = []
        if not urun_adi:
            eksikler.append("Ürün Adı")
        if reyon is None:
            eksikler.append("Reyon")
        if kategori is None:
            eksikler.append("Kategori")
        if yeni_adet is None:
            eksikler.append("Adet")
        if secilen_skt is None:
            eksikler.append("SKT")

        if eksikler:
            st.caption(f"🔒 Kayıt için zorunlu alanlar: **{', '.join(eksikler)}** (Fiyat ve Hız opsiyoneldir)")

        if st.button(
            "➕ Diske Kaydet ve 2D Haritayı Güncelle",
            type="primary",
            disabled=bool(eksikler),
            use_container_width=True,
        ):
            kalan_gun = (secilen_skt - kontrol_tarihi).days
            if kalan_gun > max_omur:
                st.error(
                    f"🚫 **RAF ÖMRÜ KİLİDİ:** {kategori} için max {max_omur} gün girilebilir "
                    f"(seçilen tarih: {kalan_gun} gün sonrası)."
                )
            else:
                hiz_kayit = int(gunluk_hiz) if gunluk_hiz is not None else None
                fiyat_kayit = float(birim_fiyat) if birim_fiyat is not None else None

                if not kayitli:
                    durum["katalog"].append({
                        "Barkod": barkod_giris,
                        "Urun_Adi": urun_adi,
                        "Reyon": reyon,
                        "Kategori": kategori,
                        "Max_Raf_Omru_Gun": max_omur,
                        "Gunluk_Hiz": hiz_kayit,
                        "Birim_Fiyat": fiyat_kayit,
                    })

                yeni_pid = f"P-{durum['parti_sayaci']:03d}"
                durum["parti_sayaci"] += 1
                durum["stok_kayitlari"].append({
                    "Parti_ID": yeni_pid,
                    "Barkod": barkod_giris,
                    "Urun_Adi": urun_adi,
                    "Reyon": reyon,
                    "Dun_Stok": int(yeni_adet),
                    "Satilan_Adet": 0,
                    "Kalan_Stok": int(yeni_adet),
                    "Gunluk_Hiz": hiz_kayit,
                    "SKT": secilen_skt.strftime("%Y-%m-%d"),
                    "Birim_Fiyat": fiyat_kayit,
                    "Kaynak": "Yeni İlk Giriş",
                    "Coklu_Parti_Uyari": False,
                    "Sifir_Dogrulama_Gerekli": False,
                    "Son_Aksiyon_Tarihi": None,
                    "Son_Aksiyon_Tipi": "",
                    "Son_Fiziksel_Kontrol": kontrol_tarihi.isoformat(),
                    "Mudahale_Notu": "",
                })
                durumu_diske_yaz(durum)
                hiz_ek = "" if hiz_kayit is not None else " (Satış hızı henüz bilinmiyor — tarih bazlı izlenecek)"
                st.session_state["son_ilk_giris_mesaj"] = (
                    f"✅ **{urun_adi}** (`{barkod_giris}`, {int(yeni_adet)} adet, SKT: {secilen_skt.strftime('%d.%m.%Y')}) "
                    f"kaydedildi ve **{reyon}** reyonuna işlendi!{hiz_ek}"
                )
                st.session_state["s2_tarama_mesaj"] = None
                st.session_state["s2_bekleyen_barkod"] = ""
                st.rerun()

# ==============================================================================
# SEKME 3: KÖR SAYIM (KARMA SKT KABULÜ + EAN-13 DOĞRULAMA + OTOMATİK TEMİZLEME)
# ==============================================================================
with tab3:
    st.subheader("📦 Arka Kapı Dijital Gümrük (Kör Sayım & Karma SKT)")
    st.caption(
        "Aynı irsaliye satırından farklı tarihler çıkarsa '➕ Farklı SKT Ekle' ile dağıtın; "
        "tüm alt partilerin toplamı gizli irsaliyeyle karşılaştırılır ve her tarih ayrı parti olarak radara yazılır."
    )

    bekleyen_irsaliye = [
        satir for satir in durum["irsaliye"]
        if satir["Satir_ID"] not in durum["islenen_irsaliye_idler"]
    ]
    secenekler = {
        f"[{s['Satir_ID']}] {s['Barkod']} — {s['Urun_Adi']} ({s['Kategori']})": s
        for s in bekleyen_irsaliye
    }
    secenek_listesi = list(secenekler.keys())
    irsaliye_barkod_seti = {s["Barkod"] for s in bekleyen_irsaliye}

    st.session_state.setdefault("s3_barkod", "")
    st.session_state.setdefault("s3_son_barkod", "")
    st.session_state.setdefault("s3_secilen_etiket", None)
    st.session_state.setdefault("s3_son_secilen_etiket", None)
    st.session_state.setdefault("s3_form_versiyon", 0)
    st.session_state.setdefault("s3_skt_satir_sayisi", 1)

    if "s3_bekleyen_barkod" in st.session_state:
        yeni_b3 = st.session_state.pop("s3_bekleyen_barkod")
        st.session_state["s3_barkod"] = yeni_b3
        st.session_state["s3_son_barkod"] = yeni_b3
        st.session_state["s3_form_versiyon"] += 1
        st.session_state["s3_skt_satir_sayisi"] = 1
        st.session_state["bekleyen_kabul_uyari"] = None

        if yeni_b3:
            gec_b3, _, hata_b3 = barkod_dogrula(yeni_b3, irsaliye_barkod_seti)
            if not gec_b3:
                st.session_state["s3_secilen_etiket"] = None
                st.session_state["s3_son_secilen_etiket"] = None
                st.session_state["s3_eslesme_durum"] = ("hata", hata_b3)
            else:
                eslesen_etiket = next(
                    (et for et, s in secenekler.items() if s["Barkod"] == yeni_b3),
                    None,
                )
                st.session_state["s3_secilen_etiket"] = eslesen_etiket
                st.session_state["s3_son_secilen_etiket"] = eslesen_etiket
                if eslesen_etiket:
                    st.session_state["s3_eslesme_durum"] = (
                        "ok",
                        f"✅ İrsaliyede otomatik seçildi: **{secenekler[eslesen_etiket]['Urun_Adi']}**",
                    )
                else:
                    st.session_state["s3_eslesme_durum"] = (
                        "hata",
                        f"🚫 Barkod (`{yeni_b3}`) bekleyen gizli irsaliyede bulunamadı! "
                        "Yanlış ürüne kayıt önlemek için önceki ürün seçimi temizlendi.",
                    )
        else:
            st.session_state["s3_secilen_etiket"] = None
            st.session_state["s3_son_secilen_etiket"] = None
            st.session_state["s3_eslesme_durum"] = None

    if st.session_state.get("s3_secilen_etiket") not in secenek_listesi:
        st.session_state["s3_secilen_etiket"] = None

    m1, m2, m3 = st.columns(3)
    m1.metric("Bekleyen İrsaliye Kalemi", f"{len(bekleyen_irsaliye)}")
    m2.metric("İşlenen / Kilitlenen Kalem", f"{len(durum['islenen_irsaliye_idler'])}")
    engellenen_tl = sum(k["Engellenen_TL"] for k in durum["kabul_kayitlari"])
    m3.metric("Kapıda Yakalanan Risk", f"{engellenen_tl:,.0f} TL")

    st.divider()

    if st.session_state.get("s3_son_islem_mesaj"):
        st.success(st.session_state.s3_son_islem_mesaj)

    def irsaliye_partileri_stoga_ekle(
        satir: dict,
        partiler: list,
        kaynak_etiketi: str,
        sonuc_metni: str,
        risk_tl: float,
        detay: str,
    ):
        if satir["Satir_ID"] in durum["islenen_irsaliye_idler"]:
            return
        durum["islenen_irsaliye_idler"].append(satir["Satir_ID"])

        toplam_teslim = 0
        skt_ozetleri = []
        for prt in partiler:
            adet = int(prt["adet"])
            skt_iso = prt["skt_iso"]
            toplam_teslim += adet
            if adet > 0:
                skt_ozetleri.append(f"{skt_iso} ({adet})")
                yeni_pid = f"P-{durum['parti_sayaci']:03d}"
                durum["parti_sayaci"] += 1
                durum["stok_kayitlari"].append({
                    "Parti_ID": yeni_pid,
                    "Barkod": satir["Barkod"],
                    "Urun_Adi": satir["Urun_Adi"],
                    "Reyon": satir["Reyon"],
                    "Dun_Stok": adet,
                    "Satilan_Adet": 0,
                    "Kalan_Stok": adet,
                    "Gunluk_Hiz": satir.get("Gunluk_Hiz"),
                    "SKT": skt_iso,
                    "Birim_Fiyat": satir.get("Birim_Fiyat"),
                    "Kaynak": kaynak_etiketi,
                    "Coklu_Parti_Uyari": False,
                    "Sifir_Dogrulama_Gerekli": False,
                    "Son_Aksiyon_Tarihi": None,
                    "Son_Aksiyon_Tipi": "",
                    "Son_Fiziksel_Kontrol": kontrol_tarihi.isoformat(),
                    "Mudahale_Notu": "",
                })

        durum["kabul_kayitlari"].insert(0, {
            "Satir_ID": satir["Satir_ID"],
            "Ürün": satir["Urun_Adi"],
            "Teslim_Adet": toplam_teslim,
            "Parti_Dagilimi": " + ".join(skt_ozetleri) if skt_ozetleri else "Reddedildi (0)",
            "Sonuc": sonuc_metni,
            "Engellenen_TL": risk_tl,
            "Detay": detay,
        })
        st.session_state["bekleyen_kabul_uyari"] = None
        st.session_state["s3_tarama_mesaj"] = None
        st.session_state["s3_bekleyen_barkod"] = ""
        st.session_state["s3_skt_satir_sayisi"] = 1
        st.session_state["s3_son_islem_mesaj"] = (
            f"✅ **{satir['Urun_Adi']}** ({sonuc_metni} — toplam {toplam_teslim} adet · "
            f"{' + '.join(skt_ozetleri) if skt_ozetleri else '0'}) işlendi ve satır kilitlendi."
        )
        durumu_diske_yaz(durum)

    if not bekleyen_irsaliye:
        st.success("✅ Bugünkü tüm irsaliye kalemleri kontrol edilip kilitlendi.")
    else:
        tarama_s3 = st.session_state.get("s3_tarama_mesaj")
        if tarama_s3:
            if tarama_s3[0] == "ok":
                st.success(tarama_s3[1])
            else:
                st.error(tarama_s3[1])

        barkod_tarayici_alani("s3", "📷 İrsaliye Barkodu Tara (Kamera veya Fotoğraf — Otomatik Seçim)")

        c_i1, c_i2 = st.columns([3, 1])
        with c_i1:
            irsaliye_barkod_giris = st.text_input(
                "🔍 Barkod ile İrsaliye Bul (Örn: 8691234567890)",
                key="s3_barkod",
                placeholder="Barkodu okutun veya 13 haneli EAN-13 yazıp Enter'a basın...",
            ).strip()
        with c_i2:
            st.write("")
            st.write("")
            if st.button("🧹 Seçimi Temizle", key="s3_temizle", use_container_width=True):
                st.session_state["s3_bekleyen_barkod"] = ""
                st.session_state["s3_tarama_mesaj"] = None
                st.session_state["s3_son_islem_mesaj"] = None
                st.rerun()

        if irsaliye_barkod_giris != st.session_state["s3_son_barkod"]:
            st.session_state["s3_bekleyen_barkod"] = irsaliye_barkod_giris
            st.rerun()

        eslesme_s3 = st.session_state.get("s3_eslesme_durum")
        if eslesme_s3:
            if eslesme_s3[0] == "ok":
                st.info(eslesme_s3[1])
            else:
                st.error(eslesme_s3[1])

        secilen_etiket = st.selectbox(
            "Teslim Alınacak Ürün (Beklenen Adet Gizli)",
            secenek_listesi,
            index=None,
            placeholder="Barkod okutun veya bekleyen irsaliyeden ürün seçin...",
            key="s3_secilen_etiket",
        )

        if secilen_etiket != st.session_state["s3_son_secilen_etiket"]:
            st.session_state["s3_son_secilen_etiket"] = secilen_etiket
            st.session_state["s3_skt_satir_sayisi"] = 1
            st.session_state["bekleyen_kabul_uyari"] = None

        if secilen_etiket is None:
            st.caption("🔒 Bir ürün seçilene veya irsaliyeyle eşleşen barkod okutulana kadar kayıt alanları kilitlidir.")
        else:
            satir = secenekler[secilen_etiket]
            form_v3 = st.session_state["s3_form_versiyon"]
            satir_sayisi = st.session_state["s3_skt_satir_sayisi"]

            st.markdown(
                f"**Aktif İrsaliye Satırı:** `{satir['Satir_ID']}` · **{satir['Urun_Adi']}** "
                f"({satir['Reyon']} / {satir['Kategori']} — Max raf ömrü: {satir['Max_Raf_Omru_Gun']} gün)"
            )

            girilen_satirlar = []
            for idx in range(satir_sayisi):
                c1, c2 = st.columns(2)
                with c1:
                    adet_val = st.number_input(
                        f"Fiziksel Adet (Parti {idx + 1}) *",
                        min_value=0 if satir_sayisi == 1 else 1,
                        max_value=1000,
                        value=None,
                        step=1,
                        placeholder="Bu tarihten kaç adet var?",
                        key=f"s3_adet_{satir['Satir_ID']}_{form_v3}_{idx}",
                    )
                with c2:
                    skt_val = st.date_input(
                        f"Paket Üzerindeki SKT (Parti {idx + 1}) *",
                        value=None,
                        format="DD.MM.YYYY",
                        key=f"s3_skt_{satir['Satir_ID']}_{form_v3}_{idx}",
                    )
                girilen_satirlar.append((adet_val, skt_val))

            b_ekle, b_sil, b_ozet = st.columns([1.2, 1.2, 2])
            with b_ekle:
                if st.button("➕ Farklı SKT Ekle", disabled=(satir_sayisi >= 6), use_container_width=True):
                    st.session_state["s3_skt_satir_sayisi"] = satir_sayisi + 1
                    st.session_state["bekleyen_kabul_uyari"] = None
                    st.rerun()
            with b_sil:
                if st.button("➖ Son Satırı Kaldır", disabled=(satir_sayisi <= 1), use_container_width=True):
                    st.session_state["s3_skt_satir_sayisi"] = satir_sayisi - 1
                    st.session_state["bekleyen_kabul_uyari"] = None
                    st.rerun()
            with b_ozet:
                dolu_toplam = sum(int(a) for a, _ in girilen_satirlar if a is not None)
                st.caption(f"📦 Girilen Toplam Adet: **{dolu_toplam}** ({satir_sayisi} SKT satırı)")

            eksik_var = any(a is None or t is None for a, t in girilen_satirlar)
            if eksik_var:
                st.caption("🔒 Tüm satırlarda fiziksel adet ve SKT doldurulmadan doğrulama açılmaz.")

            if st.button(
                "🔒 Kör Sayımı Doğrula",
                type="primary",
                disabled=eksik_var,
                use_container_width=True,
            ):
                # Aynı SKT iki kez girildiyse otomatik birleştir
                birlesik_map = {}
                for adet_v, skt_v in girilen_satirlar:
                    iso = skt_v.strftime("%Y-%m-%d")
                    birlesik_map[iso] = birlesik_map.get(iso, 0) + int(adet_v)

                partiler_temiz = [
                    {"skt_iso": iso, "adet": adet, "gun": (datetime.date.fromisoformat(iso) - kontrol_tarihi).days}
                    for iso, adet in sorted(birlesik_map.items())
                ]

                beklenen = int(satir["Beklenen_Adet"])
                max_omur = int(satir["Max_Raf_Omru_Gun"])
                hiz = satir.get("Gunluk_Hiz")
                fiyat_sayisal = float(satir["Birim_Fiyat"]) if satir.get("Birim_Fiyat") is not None else 0.0
                toplam_sayilan = sum(x["adet"] for x in partiler_temiz)

                # KAPI 1: Tüm alt partiler için SKT & sahte tarih kontrolü
                kapi1_hata = None
                for prt in partiler_temiz:
                    if prt["gun"] <= 0:
                        kapi1_hata = f"🔴 **KAPI 1 BLOK:** Tarihi geçmiş parti ({prt['skt_iso']}) teslim alınamaz!"
                        break
                    if prt["gun"] > max_omur:
                        kapi1_hata = (
                            f"🚫 **KAPI 1 BLOK (SAHTE SKT):** {satir['Kategori']} max {max_omur} gündür "
                            f"({prt['skt_iso']} tarihi {prt['gun']} gün sonrası)."
                        )
                        break

                if kapi1_hata:
                    st.error(kapi1_hata)
                    st.session_state["bekleyen_kabul_uyari"] = None
                elif toplam_sayilan != beklenen:
                    fark = toplam_sayilan - beklenen
                    kayip_tl = round(abs(fark) * fiyat_sayisal, 2)
                    yon = "EKSİK" if fark < 0 else "FAZLA"
                    st.session_state["bekleyen_kabul_uyari"] = {
                        "tip": "ADET_FARKI",
                        "satir": satir,
                        "partiler": partiler_temiz,
                        "risk_tl": kayip_tl,
                        "mesaj": (
                            f"🚨 **KAPI 2 ALARM ({abs(fark)} ADET {yon} — {kayip_tl:,.0f} TL):** "
                            f"Girilen toplam (**{toplam_sayilan} adet**) gizli irsaliyeyle tutmuyor! "
                            "Tekrar sayın ya da tutanakla fiili dağılımı teslim alın."
                        ),
                    }
                else:
                    # KAPI 3: Karma SKT partileri arası paylaşımlı kapasite kontrolü
                    toplam_elde_kalacak = 0
                    tasan_notlar = []
                    if hiz is not None and int(hiz) > 0:
                        kullanilan_kap = 0
                        for prt in partiler_temiz:
                            uygun_kap = max(0, int(hiz) * prt["gun"] - kullanilan_kap)
                            satilabilir = min(prt["adet"], uygun_kap)
                            tasan = prt["adet"] - satilabilir
                            kullanilan_kap += satilabilir
                            if tasan > 0:
                                toplam_elde_kalacak += tasan
                                tasan_notlar.append(f"{prt['skt_iso']} ({tasan} adet)")

                    if toplam_elde_kalacak > 0:
                        risk_tl = round(toplam_elde_kalacak * fiyat_sayisal, 2)
                        st.session_state["bekleyen_kabul_uyari"] = {
                            "tip": "KISA_OMUR",
                            "satir": satir,
                            "partiler": partiler_temiz,
                            "risk_tl": risk_tl,
                            "mesaj": (
                                f"🟠 **KAPI 3 UYARI — KISA RAF ÖMRÜ ({risk_tl:,.0f} TL RİSK):** "
                                f"Toplam adet tam (**{toplam_sayilan}**) ama {int(hiz)}/gün hızla "
                                f"**{toplam_elde_kalacak} adet elde kalacak** ({', '.join(tasan_notlar)})."
                            ),
                        }
                    else:
                        irsaliye_partileri_stoga_ekle(
                            satir,
                            partiler_temiz,
                            "Kör Sayım Onaylı",
                            "ONAYLANDI",
                            0.0,
                            f"{len(partiler_temiz)} parti 3 kapıdan temiz geçti ve radara işlendi",
                        )
                        st.rerun()

            uyari = st.session_state.get("bekleyen_kabul_uyari")
            if uyari and uyari["satir"]["Satir_ID"] == satir["Satir_ID"]:
                if uyari["tip"] == "ADET_FARKI":
                    st.error(uyari["mesaj"])
                else:
                    st.warning(uyari["mesaj"])

                u1, u2 = st.columns(2)
                with u1:
                    if st.button("⚠️ Riskli / Tutanaklı Fiilen Teslim Aldım (Radara Ekle)", use_container_width=True):
                        irsaliye_partileri_stoga_ekle(
                            uyari["satir"],
                            uyari["partiler"],
                            "Riskli Kabul (Radarda)",
                            f"RİSKLİ KABUL ({uyari['tip']})",
                            uyari["risk_tl"],
                            "Uyarıya rağmen fiilen teslim alındı ve tüm alt partiler radara eklendi",
                        )
                        st.rerun()
                with u2:
                    if st.button("❌ Malı Kapıda Reddettim (İade)", use_container_width=True):
                        irsaliye_partileri_stoga_ekle(
                            uyari["satir"],
                            [],
                            "Kapıda Reddedildi",
                            f"REDDEDİLDİ ({uyari['tip']})",
                            uyari["risk_tl"],
                            "Mal kapıdan geri çevrildi, stoğa eklenmedi",
                        )
                        st.rerun()

    if durum["kabul_kayitlari"]:
        st.divider()
        st.subheader("📋 Bugünkü Mal Kabul Denetim Kayıtları")
        st.dataframe(pd.DataFrame(durum["kabul_kayitlari"]), use_container_width=True, hide_index=True)
