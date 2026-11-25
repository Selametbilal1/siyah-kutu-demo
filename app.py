import datetime
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
    page_title="Siyah Kutu v0.3.2 | Güvenli FEFO & Otomatik Barkod",
    page_icon="🟩",
    layout="wide",
)

BASE_DIR = Path(__file__).resolve().parent
DURUM_DOSYASI = BASE_DIR / "siyah_kutu_durum.json"
AKTIF_SURUM = "0.3.2"

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


# --- 0. DOĞRUDAN PYTHON BARKOD ÇÖZÜCÜ (KOPYALA-YAPIŞTIR YOK) ---
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
                "Barkod çözücü paketi kurulu değil. Terminalde "
                "`pip install pillow zxing-cpp` çalıştırın veya `requirements.txt` dosyasını güncelleyin."
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
                    "❌ Barkod okunamadı. Yanlış ürüne kayıt önlemek için önceki seçim temizlendi — daha net ve parlamadan tekrar çekin.",
                )
            st.rerun()


# --- 1. KALICI DİSK KAYDI ---
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


def csvden_baslat(stok_dosya, satis_dosya, irsaliye_dosya, satis_dusulsun_mu: bool):
    df_stok = pd.read_csv(
        stok_dosya if stok_dosya else BASE_DIR / "dun_stok.csv",
        dtype={"Barkod": str},
    )
    df_satis = pd.read_csv(
        satis_dosya if satis_dosya else BASE_DIR / "t1_satis.csv",
        dtype={"Barkod": str},
    )
    df_irsaliye = pd.read_csv(
        irsaliye_dosya if irsaliye_dosya else BASE_DIR / "irsaliye.csv",
        dtype={"Barkod": str},
    )

    satis_haritasi = (
        df_satis.groupby("Barkod")["Satilan_Adet"].sum().to_dict()
        if not df_satis.empty
        else {}
    )

    df_stok["_skt_sira"] = pd.to_datetime(df_stok["SKT"])
    df_stok = df_stok.sort_values(by=["Barkod", "_skt_sira"]).reset_index(drop=True)

    kalan_satis_havuzu = {str(k).strip(): int(v) for k, v in satis_haritasi.items()}
    stok_kayitlari = []

    for idx, r in df_stok.iterrows():
        barkod = str(r["Barkod"]).strip()
        dun_stok = int(r["Dun_Stok"])

        if satis_dusulsun_mu:
            havuz = kalan_satis_havuzu.get(barkod, 0)
            dusulen = min(dun_stok, havuz)
            kalan_stok = max(0, dun_stok - dusulen)
            kalan_satis_havuzu[barkod] = max(0, havuz - dusulen)
        else:
            dusulen = 0
            kalan_stok = dun_stok

        stok_kayitlari.append({
            "Parti_ID": f"P-{idx + 1:03d}",
            "Barkod": barkod,
            "Urun_Adi": str(r["Urun_Adi"]),
            "Reyon": str(r["Reyon"]),
            "Dun_Stok": dun_stok,
            "Satilan_Adet": dusulen,
            "Kalan_Stok": kalan_stok,
            "Gunluk_Hiz": max(int(r["Gunluk_Hiz"]), 1),
            "SKT": str(r["SKT"]),
            "Birim_Fiyat": float(r["Birim_Fiyat"]),
            "Kaynak": "T+1 FEFO Motoru" if satis_dusulsun_mu else "Gün Sonu Kapanış Stoğu",
            "Coklu_Parti_Uyari": False,
            "Sifir_Dogrulama_Gerekli": False,
            "Son_Aksiyon_Tarihi": None,
            "Son_Aksiyon_Tipi": "",
            "Mudahale_Notu": "",
        })

    gruplar = {}
    for s in stok_kayitlari:
        gruplar.setdefault(s["Barkod"], []).append(s)

    for barkod, partiler in gruplar.items():
        if len(partiler) < 2:
            continue

        partiler_sirali = sorted(partiler, key=lambda x: x["SKT"])
        toplam_satis = int(satis_haritasi.get(barkod, 0)) if satis_dusulsun_mu else 0
        if toplam_satis <= 0:
            continue

        for idx, p in enumerate(partiler_sirali):
            yeni_aktif_var_mi = any(
                diger["Kalan_Stok"] > 0 for diger in partiler_sirali[idx + 1:]
            )
            if p["Satilan_Adet"] > 0 and p["Kalan_Stok"] == 0 and yeni_aktif_var_mi:
                p["Sifir_Dogrulama_Gerekli"] = True
            elif p["Satilan_Adet"] > 0 and p["Kalan_Stok"] > 0 and yeni_aktif_var_mi:
                p["Coklu_Parti_Uyari"] = True

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
            "Gunluk_Hiz": max(int(r["Gunluk_Hiz"]), 1),
            "Birim_Fiyat": float(r["Birim_Fiyat"]),
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
            "Gunluk_Hiz": max(int(r["Gunluk_Hiz"]), 1),
            "Birim_Fiyat": float(r["Birim_Fiyat"]),
        }
        irsaliye_listesi.append({
            "Satir_ID": f"IRS-{idx + 1:03d}",
            "Barkod": b,
            "Urun_Adi": str(r["Urun_Adi"]),
            "Reyon": str(r["Reyon"]),
            "Kategori": str(r["Kategori"]),
            "Beklenen_Adet": int(r["Beklenen_Adet"]),
            "Max_Raf_Omru_Gun": int(r["Max_Raf_Omru_Gun"]),
            "Gunluk_Hiz": max(int(r["Gunluk_Hiz"]), 1),
            "Birim_Fiyat": float(r["Birim_Fiyat"]),
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
        "parti_sayaci": len(stok_kayitlari) + 1,
    }
    durumu_diske_yaz(durum)
    return durum


# --- 2. PAYLAŞIMLI KAPASİTE + TARİH BAZLI RİSK ANALİZ MOTORU ---
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
            if stok > 0 and gun >= 1:
                aktifler.append((gun, p["Parti_ID"], p))

        aktifler.sort(key=lambda x: (x[0], x[1]))
        kullanilan_kapasite = 0

        for gun, pid, p in aktifler:
            hiz = max(int(p["Gunluk_Hiz"]), 1)
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
        hiz = max(int(p["Gunluk_Hiz"]), 1)
        fiyat = float(p["Birim_Fiyat"])
        kap = kapasite_sonuclari.get(
            pid, {"Paylasimli_Kapasite": 0, "Elde_Kalacak_Adet": 0, "Onceki_Parti_Yuk": 0}
        )

        bugun_one_cekildi = (
            p.get("Son_Aksiyon_Tarihi") == tarih_str
            and p.get("Son_Aksiyon_Tipi") == "AKSIYON_ALINDI"
        )

        if p.get("Sifir_Dogrulama_Gerekli"):
            durum_kod = "KIRMIZI" if gun <= 1 else "TURUNCU"
            sonuc = {
                "Kalan_Gun": gun,
                "Durum": durum_kod,
                "Risk_Adet": 0,
                "Risk_TL": 0.0,
                "Satilabilir_Mi": gun > 0,
                "Gorev_Tipi": "SIFIR_DOGRULAMA",
                "Paylasimli_Kapasite": kap["Paylasimli_Kapasite"],
                "Elde_Kalacak_Adet": 0,
                "Aksiyon_Mesaji": (
                    f"Hesapta 0 görünüyor (dün {p['Satilan_Adet']} düşüldü) ama öndeki yeni koliden satılmış olabilir. "
                    f"{p['SKT']} tarihli paketten rafta kaldı mı?"
                ),
            }
        elif stok <= 0:
            sonuc = {
                "Kalan_Gun": gun,
                "Durum": "YESIL",
                "Risk_Adet": 0,
                "Risk_TL": 0.0,
                "Satilabilir_Mi": False,
                "Gorev_Tipi": "TEMIZ",
                "Paylasimli_Kapasite": 0,
                "Elde_Kalacak_Adet": 0,
                "Aksiyon_Mesaji": p.get("Mudahale_Notu") or "Stok sıfır — kontrole gerek yok",
            }
        elif gun <= 0:
            sonuc = {
                "Kalan_Gun": gun,
                "Durum": "KIRMIZI",
                "Risk_Adet": stok,
                "Risk_TL": round(stok * fiyat, 2),
                "Satilabilir_Mi": False,
                "Gorev_Tipi": "SKT_DOLDU",
                "Paylasimli_Kapasite": 0,
                "Elde_Kalacak_Adet": stok,
                "Aksiyon_Mesaji": "SKT doldu — raftan topla ve fire/iadeye ayır",
            }
        elif bugun_one_cekildi:
            sonuc = {
                "Kalan_Gun": gun,
                "Durum": "YESIL",
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
                "Risk_Adet": stok,
                "Risk_TL": round(stok * fiyat, 2),
                "Satilabilir_Mi": True,
                "Gorev_Tipi": "SON_GUN",
                "Paylasimli_Kapasite": kap["Paylasimli_Kapasite"],
                "Elde_Kalacak_Adet": kap["Elde_Kalacak_Adet"],
                "Aksiyon_Mesaji": "Yarın son gün — raftaki adedi doğrula, acil indirim/öne çek",
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
                "Risk_Adet": elde_kalacak,
                "Risk_TL": round(elde_kalacak * fiyat, 2),
                "Satilabilir_Mi": True,
                "Gorev_Tipi": "HIZ_RISKI",
                "Paylasimli_Kapasite": kap["Paylasimli_Kapasite"],
                "Elde_Kalacak_Adet": elde_kalacak,
                "Aksiyon_Mesaji": (
                    f"{gun} gün kaldı; {hiz}/gün hızla bu partiye kalan kapasite {kap['Paylasimli_Kapasite']} adet"
                    f"{paylasim_notu}. {elde_kalacak} adet elde kalacak — indirim & öne çek"
                ),
            }
        elif p.get("Coklu_Parti_Uyari"):
            sonuc = {
                "Kalan_Gun": gun,
                "Durum": "TURUNCU",
                "Risk_Adet": stok,
                "Risk_TL": round(stok * fiyat, 2),
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


# --- 3. YAN MENÜ ---
st.sidebar.header("⚙️ Siyah Kutu v0.3.2 Ayarları")
kontrol_tarihi = st.sidebar.date_input(
    "Aktif İşlem Tarihi (Gün İlerletme Testi)",
    value=datetime.date(2026, 11, 24),
    format="DD.MM.YYYY",
)

pos_stok_tipi = st.sidebar.radio(
    "POS Stok Dosyasının Saati",
    [
        "Satış Öncesi Stok (T+1 Satışı FEFO Düş)",
        "Satış Sonrası Kapanış Stoğu (Tekrar Düşme)",
    ],
    index=0,
)
satis_dusulsun_mu = pos_stok_tipi.startswith("Satış Öncesi")

stok_dosya = st.sidebar.file_uploader("Dün Stok CSV (Opsiyonel)", type=["csv"])
satis_dosya = st.sidebar.file_uploader("Gece T+1 Satış CSV (Opsiyonel)", type=["csv"])
irsaliye_dosya = st.sidebar.file_uploader("Gizli İrsaliye CSV (Opsiyonel)", type=["csv"])

if st.sidebar.button("🔄 Demoyu Sıfırla (CSV'den Yeniden Başlat)", use_container_width=True):
    if DURUM_DOSYASI.exists():
        DURUM_DOSYASI.unlink()
    st.session_state.durum = csvden_baslat(stok_dosya, satis_dosya, irsaliye_dosya, satis_dusulsun_mu)
    st.session_state.bekleyen_kabul_uyari = None
    st.session_state.son_ilk_giris_mesaj = None
    st.session_state.s3_son_islem_mesaj = None
    st.session_state.s2_bekleyen_barkod = ""
    st.session_state.s3_bekleyen_barkod = ""
    st.rerun()

if "durum" not in st.session_state:
    disk_durum = durumdan_oku()
    if disk_durum is not None:
        st.session_state.durum = disk_durum
    else:
        try:
            st.session_state.durum = csvden_baslat(stok_dosya, satis_dosya, irsaliye_dosya, satis_dusulsun_mu)
        except FileNotFoundError as e:
            st.error(f"Eksik CSV dosyası: {e.filename}. Tüm CSV dosyaları `app.py` ile aynı klasörde olmalı.")
            st.stop()

durum = st.session_state.durum
if durum.get("satis_dusuldu_mu") != satis_dusulsun_mu:
    st.sidebar.info("POS stok saati değişti. Uygulamak için 'Demoyu Sıfırla'ya basın.")

yarin_satirlari = [
    {
        "Barkod": p["Barkod"],
        "Urun_Adi": p["Urun_Adi"],
        "Reyon": p["Reyon"],
        "Dun_Stok": p["Kalan_Stok"],
        "Gunluk_Hiz": p["Gunluk_Hiz"],
        "SKT": p["SKT"],
        "Birim_Fiyat": p["Birim_Fiyat"],
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
st.sidebar.caption("🛡️ Saha Pilot Güvenliği: Boş varsayılanlar ve otomatik temizleme aktif.")


# --- 4. ANA BAŞLIK VE 3 SEKME ---
st.title("🟩 Siyah Kutu v0.3.2 — Güvenli FEFO & Otomatik Barkod")
tab1, tab2, tab3 = st.tabs([
    "🗺️ 1. Sabah T+1 Renk Radarı",
    "➕ 2. İlk Ürün / Parti Girişi",
    "📦 3. Kör Sayım (Mal Kabul)",
])

# ==============================================================================
# SEKME 1: SABAH T+1 RENK RADARI
# ==============================================================================
with tab1:
    st.caption(
        f"Aktif Kontrol Tarihi: {kontrol_tarihi.strftime('%d.%m.%Y')} | "
        "Tarih bazlı kapanış, matematiksel sıfır doğrulama ve paylaşımlı FEFO kapasitesi aktif."
    )

    analizli_partiler = tum_partileri_analiz_et(durum["stok_kayitlari"], kontrol_tarihi)
    oncelik_skoru = {"KIRMIZI": 3, "TURUNCU": 2, "YESIL": 1}

    reyon_sirasi = list(dict.fromkeys(p["Reyon"] for p in analizli_partiler))
    reyon_ozetleri = []
    for reyon in reyon_sirasi:
        alt = [p for p in analizli_partiler if p["Reyon"] == reyon]
        alt.sort(key=lambda x: (oncelik_skoru[x["Durum"]], x["Risk_TL"]), reverse=True)
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
            "Aksiyon": alt[0]["Aksiyon_Mesaji"],
        })

    toplam_reyon = len(reyon_ozetleri)
    yesil_reyon = sum(1 for r in reyon_ozetleri if r["Durum"] == "YESIL")
    saglik_skoru = int((yesil_reyon / toplam_reyon) * 100) if toplam_reyon else 100
    aktif_risk_tl = sum(r["Toplam_Risk_TL"] for r in reyon_ozetleri)
    kurtarma_aksiyon_tl = sum(log.get("Potansiyel_Kurtarilan_TL", 0.0) for log in durum["aksiyon_loglari"])
    gerceklesen_fire_tl = sum(log.get("Gerceklesen_Fire_TL", 0.0) for log in durum["aksiyon_loglari"])

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Mağaza Sağlık Skoru", f"%{saglik_skoru}", f"{yesil_reyon}/{toplam_reyon} Reyon Yeşil")
    k2.metric("Aktif Risk (TL)", f"{aktif_risk_tl:,.0f} TL")
    k3.metric("İndirim / Öne Çekme", f"{kurtarma_aksiyon_tl:,.0f} TL")
    k4.metric("Gerçekleşen Fire (TL)", f"{gerceklesen_fire_tl:,.0f} TL")

    st.divider()

    st.subheader("🗺️ 2D Mağaza Reyon Haritası")
    renk_stili = {
        "KIRMIZI": ("#fef2f2", "#dc2626", "#991b1b", "🔴 KRİTİK ALARM"),
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
                        min-height: 185px;
                    ">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <strong style="font-size:18px; color:{title_color};">{r['Reyon']}</strong>
                            <span style="font-size:11px; font-weight:700; color:{title_color};">{etiket}</span>
                        </div>
                        <hr style="border:none; border-top:1px solid {border}40; margin:8px 0;">
                        <div style="font-size:13px; margin-bottom:4px;"><strong>Durum:</strong> {gorev_yazisi}</div>
                        <div style="font-size:13px; margin-bottom:4px;"><strong>Öncelikli Ürün:</strong> {r['Odak_Urun']}</div>
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

    st.subheader("🎯 Ürün Bazlı Sabah Görevleri")
    acik_partiler = [p for p in analizli_partiler if p["Durum"] != "YESIL"]
    acik_partiler.sort(key=lambda x: (oncelik_skoru[x["Durum"]], x["Risk_TL"]), reverse=True)

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
        hedef_parti["Mudahale_Notu"] = f"{islem_adi} ({kontrol_tarihi.strftime('%d.%m.%Y')})"

        kurtarilan = 0.0
        fire = 0.0

        if yeni_tip == "AKSIYON_ALINDI":
            hedef_parti["Kalan_Stok"] = sayilan
            kalan_gun = parti_analiz["Kalan_Gun"]
            kapasite = parti_analiz["Paylasimli_Kapasite"]
            riskli_adet = sayilan if kalan_gun <= 1 else max(0, sayilan - kapasite)
            kurtarilan = round(riskli_adet * float(hedef_parti["Birim_Fiyat"]), 2)
        elif yeni_tip == "TUKENDI":
            hedef_parti["Kalan_Stok"] = 0
        elif yeni_tip == "FIRE_AYRILDI":
            fire = round(sayilan * float(hedef_parti["Birim_Fiyat"]), 2)
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
        st.success(f"🎉 {kontrol_tarihi.strftime('%d.%m.%Y')} için tüm riskli partiler doğrulandı ve kapatıldı. Mağaza %100 Yeşil.")
    else:
        for p in acik_partiler:
            pid = p["Parti_ID"]
            ikon = "🔴" if p["Durum"] == "KIRMIZI" else "🟠"
            sifir_mi = p["Gorev_Tipi"] == "SIFIR_DOGRULAMA"

            etiket_ek = ""
            if sifir_mi:
                etiket_ek = " | 🔍 SIFIRLANAN ESKİ PARTİ DOĞRULAMASI"
            elif p["Gorev_Tipi"] == "COKLU_DOGRULAMA":
                etiket_ek = " | ⚠️ ÇOKLU SKT DOĞRULAMASI"

            with st.container(border=True):
                st.markdown(
                    f"**{ikon} {p['Reyon']} → {p['Urun_Adi']}** "
                    f"(Parti: `{pid}` · SKT: **{p['SKT']}** · Kalan Gün: **{p['Kalan_Gun']}**{etiket_ek})"
                )
                st.caption(
                    f"Tahmini Kalan: **{p['Kalan_Stok']} adet** (Dün düşülen: {p['Satilan_Adet']}) | "
                    f"Satış Hızı: {p['Gunluk_Hiz']}/gün | Risk: **{p['Risk_TL']:,.0f} TL** — {p['Aksiyon_Mesaji']}"
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
                                st.warning("Öne çekmek için rafta kalan adedi 0'dan büyük girin veya 'Rafta Kalmadı'ya basın.")
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
    st.dataframe(
        pd.DataFrame(analizli_partiler)[
            [
                "Parti_ID", "Reyon", "Urun_Adi", "Dun_Stok", "Satilan_Adet",
                "Kalan_Stok", "Gunluk_Hiz", "SKT", "Kalan_Gun",
                "Paylasimli_Kapasite", "Elde_Kalacak_Adet", "Durum", "Risk_TL", "Son_Aksiyon_Tarihi"
            ]
        ],
        use_container_width=True,
        hide_index=True,
    )

# ==============================================================================
# SEKME 2: İLK ÜRÜN / PARTİ GİRİŞİ (OTOMATİK SEÇİM + BOŞ VARSAYILANLAR)
# ==============================================================================
with tab2:
    st.subheader("➕ Yeni Ürün / Parti Girişi")
    st.caption("Barkodu okuttuğunuzda ürün otomatik tanınır; yeni barkodda önceki seçim temizlenir ve tüm alanlar boş açılır.")

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
            "Barkod Numarası",
            key="s2_barkod",
            placeholder="Yukarıdan barkod okutun veya 13 haneli EAN-13 yazıp Enter'a basın...",
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

    if not barkod_giris:
        st.info("👆 Kayıt açmak için yukarıdan bir ürün barkodu okutun veya barkod numarasını yazın.")
    else:
        katalog_map = {k["Barkod"]: k for k in durum["katalog"]}
        kayitli = katalog_map.get(barkod_giris)

        if kayitli:
            urun_adi = kayitli["Urun_Adi"]
            reyon = kayitli["Reyon"]
            kategori = kayitli["Kategori"]
            max_omur = int(kayitli["Max_Raf_Omru_Gun"])
            birim_fiyat = float(kayitli["Birim_Fiyat"])
            gunluk_hiz = int(kayitli["Gunluk_Hiz"])
            st.info(
                f"✅ **Katalogdan Otomatik Tanındı:** **{urun_adi}** | Reyon: **{reyon}** | "
                f"Kategori: **{kategori}** (Max {max_omur} gün) | Fiyat: **{birim_fiyat:.0f} TL**"
            )
        else:
            st.warning(f"🆕 **Kayıtsız Barkod (`{barkod_giris}`):** Önceki ürün temizlendi. Paket üzerindeki bilgileri boş alanlara girin:")
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
                    "Birim Fiyat (TL) *",
                    min_value=1.0,
                    max_value=5000.0,
                    value=None,
                    step=1.0,
                    placeholder="Örn: 45",
                    key=f"s2_fiyat_{form_v}",
                )
            with n4:
                gunluk_hiz = st.number_input(
                    "Tahmini Günlük Satış Hızı *",
                    min_value=1,
                    max_value=500,
                    value=None,
                    step=1,
                    placeholder="Örn: 5",
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
        if birim_fiyat is None:
            eksikler.append("Birim Fiyat")
        if gunluk_hiz is None:
            eksikler.append("Günlük Hız")
        if yeni_adet is None:
            eksikler.append("Adet")
        if secilen_skt is None:
            eksikler.append("SKT")

        if eksikler:
            st.caption(f"🔒 Kayıt için doldurulması gereken zorunlu alanlar: **{', '.join(eksikler)}**")

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
                if not kayitli:
                    durum["katalog"].append({
                        "Barkod": barkod_giris,
                        "Urun_Adi": urun_adi,
                        "Reyon": reyon,
                        "Kategori": kategori,
                        "Max_Raf_Omru_Gun": max_omur,
                        "Gunluk_Hiz": int(gunluk_hiz),
                        "Birim_Fiyat": float(birim_fiyat),
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
                    "Gunluk_Hiz": int(gunluk_hiz),
                    "SKT": secilen_skt.strftime("%Y-%m-%d"),
                    "Birim_Fiyat": float(birim_fiyat),
                    "Kaynak": "Yeni İlk Giriş",
                    "Coklu_Parti_Uyari": False,
                    "Sifir_Dogrulama_Gerekli": False,
                    "Son_Aksiyon_Tarihi": None,
                    "Son_Aksiyon_Tipi": "",
                    "Mudahale_Notu": "",
                })
                durumu_diske_yaz(durum)
                st.session_state["son_ilk_giris_mesaj"] = (
                    f"✅ **{urun_adi}** (`{barkod_giris}`, {int(yeni_adet)} adet, SKT: {secilen_skt.strftime('%d.%m.%Y')}) "
                    f"kaydedildi ve **{reyon}** reyonuna işlendi!"
                )
                st.session_state["s2_tarama_mesaj"] = None
                st.session_state["s2_bekleyen_barkod"] = ""
                st.rerun()

# ==============================================================================
# SEKME 3: KÖR SAYIM (MAL KABUL — OTOMATİK İRSALİYE EŞLEŞTİRME & TEMİZLEME)
# ==============================================================================
with tab3:
    st.subheader("📦 Arka Kapı Dijital Gümrük (Kör Sayım)")
    st.caption("Barkodu okuttuğunuzda bekleyen irsaliyedeki satır kendiliğinden seçilir; eşleşme yoksa önceki ürün silinir.")

    bekleyen_irsaliye = [
        satir for satir in durum["irsaliye"]
        if satir["Satir_ID"] not in durum["islenen_irsaliye_idler"]
    ]
    secenekler = {
        f"[{s['Satir_ID']}] {s['Barkod']} — {s['Urun_Adi']} ({s['Kategori']})": s
        for s in bekleyen_irsaliye
    }
    secenek_listesi = list(secenekler.keys())

    st.session_state.setdefault("s3_barkod", "")
    st.session_state.setdefault("s3_son_barkod", "")
    st.session_state.setdefault("s3_secilen_etiket", None)
    st.session_state.setdefault("s3_son_secilen_etiket", None)
    st.session_state.setdefault("s3_form_versiyon", 0)

    if "s3_bekleyen_barkod" in st.session_state:
        yeni_b3 = st.session_state.pop("s3_bekleyen_barkod")
        st.session_state["s3_barkod"] = yeni_b3
        st.session_state["s3_son_barkod"] = yeni_b3
        st.session_state["s3_form_versiyon"] += 1
        st.session_state["bekleyen_kabul_uyari"] = None

        if yeni_b3:
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

    def irsaliye_stoga_ekle(satir: dict, adet: int, skt_str: str, kaynak_etiketi: str, sonuc_metni: str, risk_tl: float, detay: str):
        if satir["Satir_ID"] in durum["islenen_irsaliye_idler"]:
            return
        durum["islenen_irsaliye_idler"].append(satir["Satir_ID"])

        if adet > 0:
            yeni_pid = f"P-{durum['parti_sayaci']:03d}"
            durum["parti_sayaci"] += 1
            durum["stok_kayitlari"].append({
                "Parti_ID": yeni_pid,
                "Barkod": satir["Barkod"],
                "Urun_Adi": satir["Urun_Adi"],
                "Reyon": satir["Reyon"],
                "Dun_Stok": int(adet),
                "Satilan_Adet": 0,
                "Kalan_Stok": int(adet),
                "Gunluk_Hiz": int(satir["Gunluk_Hiz"]),
                "SKT": skt_str,
                "Birim_Fiyat": float(satir["Birim_Fiyat"]),
                "Kaynak": kaynak_etiketi,
                "Coklu_Parti_Uyari": False,
                "Sifir_Dogrulama_Gerekli": False,
                "Son_Aksiyon_Tarihi": None,
                "Son_Aksiyon_Tipi": "",
                "Mudahale_Notu": "",
            })

        durum["kabul_kayitlari"].insert(0, {
            "Satir_ID": satir["Satir_ID"],
            "Ürün": satir["Urun_Adi"],
            "Teslim_Adet": adet,
            "SKT": skt_str,
            "Sonuc": sonuc_metni,
            "Engellenen_TL": risk_tl,
            "Detay": detay,
        })
        st.session_state["bekleyen_kabul_uyari"] = None
        st.session_state["s3_tarama_mesaj"] = None
        st.session_state["s3_bekleyen_barkod"] = ""
        st.session_state["s3_son_islem_mesaj"] = (
            f"✅ **{satir['Urun_Adi']}** ({sonuc_metni} — {adet} adet) işlendi ve satır kilitlendi."
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
                "🔍 Barkod ile İrsaliye Bul",
                key="s3_barkod",
                placeholder="Barkodu okutun veya elle yazıp Enter'a basın...",
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
            st.session_state["bekleyen_kabul_uyari"] = None

        if secilen_etiket is None:
            st.caption("🔒 Bir ürün seçilene veya irsaliyeyle eşleşen barkod okutulana kadar kayıt alanları kilitlidir.")
        else:
            satir = secenekler[secilen_etiket]
            form_v3 = st.session_state["s3_form_versiyon"]

            st.markdown(
                f"**Aktif İrsaliye Satırı:** `{satir['Satir_ID']}` · **{satir['Urun_Adi']}** "
                f"({satir['Reyon']} / {satir['Kategori']} — Max raf ömrü: {satir['Max_Raf_Omru_Gun']} gün)"
            )

            c1, c2 = st.columns(2)
            with c1:
                sayilan_adet = st.number_input(
                    "Fiziksel Sayılan Adet *",
                    min_value=0,
                    max_value=1000,
                    value=None,
                    step=1,
                    placeholder="Fiilen saydığınız adedi yazın...",
                    key=f"s3_adet_{satir['Satir_ID']}_{form_v3}",
                )
            with c2:
                girilen_skt = st.date_input(
                    "Paket Üzerindeki SKT *",
                    value=None,
                    format="DD.MM.YYYY",
                    key=f"s3_skt_{satir['Satir_ID']}_{form_v3}",
                )

            eksik_s3 = []
            if sayilan_adet is None:
                eksik_s3.append("Fiziksel Adet")
            if girilen_skt is None:
                eksik_s3.append("SKT")

            if eksik_s3:
                st.caption(f"🔒 Doğrulama için doldurun: **{', '.join(eksik_s3)}**")

            if st.button(
                "🔒 Kör Sayımı Doğrula",
                type="primary",
                disabled=bool(eksik_s3),
                use_container_width=True,
            ):
                beklenen = satir["Beklenen_Adet"]
                max_omur = satir["Max_Raf_Omru_Gun"]
                hiz = satir["Gunluk_Hiz"]
                fiyat = satir["Birim_Fiyat"]
                kalan_gun = (girilen_skt - kontrol_tarihi).days
                skt_iso = girilen_skt.strftime("%Y-%m-%d")

                if kalan_gun <= 0:
                    st.error(f"🔴 **KAPI 1 BLOK:** Tarihi geçmiş ürün ({girilen_skt.strftime('%d.%m.%Y')}) teslim alınamaz!")
                    st.session_state["bekleyen_kabul_uyari"] = None
                elif kalan_gun > max_omur:
                    st.error(
                        f"🚫 **KAPI 1 BLOK (SAHTE SKT):** {satir['Kategori']} max {max_omur} gündür "
                        f"(siz {kalan_gun} gün sonrası girdiniz)."
                    )
                    st.session_state["bekleyen_kabul_uyari"] = None
                elif int(sayilan_adet) != beklenen:
                    fark = int(sayilan_adet) - beklenen
                    kayip_tl = round(abs(fark) * fiyat, 2)
                    yon = "EKSİK" if fark < 0 else "FAZLA"
                    st.session_state["bekleyen_kabul_uyari"] = {
                        "tip": "ADET_FARKI",
                        "satir": satir,
                        "sayilan": int(sayilan_adet),
                        "skt_iso": skt_iso,
                        "risk_tl": kayip_tl,
                        "mesaj": (
                            f"🚨 **KAPI 2 ALARM ({abs(fark)} ADET {yon} — {kayip_tl:,.0f} TL):** "
                            "Gizli irsaliye tutmuyor! Tekrar sayın ya da tutanakla fiili adedi teslim alın."
                        ),
                    }
                elif int(sayilan_adet) > (hiz * kalan_gun):
                    satilabilir = hiz * kalan_gun
                    elde_kalacak = int(sayilan_adet) - satilabilir
                    risk_tl = round(elde_kalacak * fiyat, 2)
                    st.session_state["bekleyen_kabul_uyari"] = {
                        "tip": "KISA_OMUR",
                        "satir": satir,
                        "sayilan": int(sayilan_adet),
                        "skt_iso": skt_iso,
                        "risk_tl": risk_tl,
                        "mesaj": (
                            f"🟠 **KAPI 3 UYARI — KISA RAF ÖMRÜ ({risk_tl:,.0f} TL RİSK):** "
                            f"Adet tam ({int(sayilan_adet)}) ama {hiz}/gün hızla {kalan_gun} günde en fazla {satilabilir} adet satılır; "
                            f"**{elde_kalacak} adet elde kalacak.**"
                        ),
                    }
                else:
                    irsaliye_stoga_ekle(
                        satir, int(sayilan_adet), skt_iso, "Kör Sayım Onaylı",
                        "ONAYLANDI", 0.0, "3 kapıdan temiz geçti ve radara işlendi"
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
                        irsaliye_stoga_ekle(
                            uyari["satir"],
                            uyari["sayilan"],
                            uyari["skt_iso"],
                            "Riskli Kabul (Radarda)",
                            f"RİSKLİ KABUL ({uyari['tip']})",
                            uyari["risk_tl"],
                            "Uyarıya rağmen fiilen teslim alındı ve T+1 radarına eklendi",
                        )
                        st.rerun()
                with u2:
                    if st.button("❌ Malı Kapıda Reddettim (İade)", use_container_width=True):
                        irsaliye_stoga_ekle(
                            uyari["satir"],
                            0,
                            uyari["skt_iso"],
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
