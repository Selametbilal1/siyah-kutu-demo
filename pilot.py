import datetime
import hashlib
import json
from copy import deepcopy
import pandas as pd


def check_digit(prefix: str) -> str:
    total = sum(
        int(c) * (3 if i % 2 == 0 else 1)
        for i, c in enumerate(reversed(prefix))
    )
    return str(-total % 10)


def validate_code(code: str, known_codes=None):
    """Barkod format ve EAN-8 / EAN-13 Mod-10 kontrol basamağı doğrulaması."""
    code = (code or "").strip()
    if not code:
        return False, "Barkod boş olamaz."
    if known_codes and code in known_codes:
        return True, ""
    if not (code.isascii() and code.isdigit()):
        return False, "🚫 Barkod yalnızca rakamlardan oluşmalıdır."
    if len(code) not in (8, 13):
        return (
            False,
            f"🚫 Geçersiz barkod uzunluğu ({len(code)} hane). Standart 13 haneli EAN-13 veya 8 haneli EAN-8 girin.",
        )
    expected = check_digit(code[:-1])
    if code[-1] != expected:
        return (
            False,
            f"🚫 Geçersiz EAN-{len(code)} barkod (`{code}`): Kontrol basamağı **`{expected}`** olmalıydı (`{code[-1]}` girildi).",
        )
    return True, ""


def parse_nonneg_int(value):
    """Excel'den gelen '3.0' gibi tam sayı değerli ondalıkları da güvenle okur."""
    if pd.isna(value):
        return None
    text = str(value).strip().replace(",", ".")
    if not text:
        return None
    try:
        num = float(text)
    except ValueError:
        return None
    if num < 0 or not num.is_integer():
        return None
    return int(num)


def parse_optional_pos_int(value):
    num = parse_nonneg_int(value)
    return num if (num is not None and num > 0) else None


def parse_optional_pos_float(value):
    if pd.isna(value):
        return None
    text = str(value).strip().replace(",", ".")
    if not text:
        return None
    try:
        num = float(text)
    except ValueError:
        return None
    return num if num > 0 else None


def sales_map(frame: pd.DataFrame, known_codes=None) -> dict:
    """
    POS satis CSV'sini okur.
    - Bozuk 8/13 haneli EAN barkodlarini ve gecersiz adetleri yakalar.
    - Magaza ici kisa/takip disi POS kodlarini reddetmeden gecirir (new_day bunlari atlayip raporlar).
    """
    needed = {"Barkod", "Satilan_Adet"}
    if not needed.issubset(frame.columns):
        raise ValueError("Satış dosyasında `Barkod` ve `Satilan_Adet` sütunları gerekli.")
    known_set = set(known_codes) if known_codes else set()
    counts = {}
    for code, amount in frame[["Barkod", "Satilan_Adet"]].itertuples(index=False, name=None):
        code = str(code).strip()
        qty = parse_nonneg_int(amount)
        if code in ("", "nan") or qty is None:
            raise ValueError(f"Geçersiz satış satırı (Barkod: `{code}`, Adet: `{amount}`).")
        if code not in known_set and code.isascii() and code.isdigit() and len(code) in (8, 13):
            expected = check_digit(code[:-1])
            if code[-1] != expected:
                raise ValueError(
                    f"Satış dosyasında bozuk EAN-{len(code)} barkod (`{code}`): "
                    f"Kontrol basamağı `{expected}` olmalıydı."
                )
        counts[code] = counts.get(code, 0) + qty
    return counts


def signature(counts: dict, day: str) -> str:
    payload = json.dumps([day, sorted(counts.items())], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalize_lots(raw_lots: list) -> list:
    """Aynı SKT satırlarını birleştirir, çoklu girişte 0 adetli boş satırları eler."""
    merged = {}
    for qty, skt_iso in raw_lots:
        q = int(qty)
        merged[skt_iso] = merged.get(skt_iso, 0) + q
    non_zero = [(q, d) for d, q in sorted(merged.items()) if q > 0]
    if non_zero:
        return non_zero
    if merged:
        first_date = sorted(merged.keys())[0]
        return [(0, first_date)]
    return []


def evaluate_receiving(
    satir: dict,
    lots: list,
    existing_stock: list,
    kontrol_tarihi: datetime.date,
) -> dict:
    """
    3 Kapili Mal Kabul Degerlendirmesi:
    - Kapi 1: Tarih araligi (gecmis veya kategori max raf omru ustu -> BLOK)
    - Kapi 2: Toplam adet gizli irsaliye kontrolu (kor sayim: ilk uyarida tam farki sizdirmaz)
    - Kapi 3: Raftaki MEVCUT aktif stok + yeni gelen partiler birlikte paylasimli kapasite kontrolu
    """
    clean_lots = normalize_lots(lots)
    days_list = [(datetime.date.fromisoformat(d) - kontrol_tarihi).days for _, d in clean_lots]
    max_omur = int(satir["Max_Raf_Omru_Gun"])

    for (qty, skt_iso), gun in zip(clean_lots, days_list):
        if gun <= 0:
            return {
                "status": "BLOK",
                "reason": f"🔴 **KAPI 1 BLOK:** Tarihi geçmiş parti (`{skt_iso}`) teslim alınamaz!",
                "clean_lots": clean_lots,
            }
        if gun > max_omur:
            return {
                "status": "BLOK",
                "reason": (
                    f"🚫 **KAPI 1 BLOK (SAHTE SKT):** {satir['Kategori']} için max {max_omur} gün girilebilir "
                    f"(`{skt_iso}` tarihi {gun} gün sonrası)."
                ),
                "clean_lots": clean_lots,
            }

    total_qty = sum(q for q, _ in clean_lots)
    beklenen = int(satir["Beklenen_Adet"])
    fark = total_qty - beklenen
    fiyat = float(satir["Birim_Fiyat"]) if satir.get("Birim_Fiyat") is not None else 0.0
    hiz = satir.get("Gunluk_Hiz")

    mevcut_aktif_toplam = 0
    kuyruk = []
    for p in existing_stock:
        if p["Barkod"] != satir["Barkod"]:
            continue
        p_stok = int(p["Kalan_Stok"])
        p_gun = (datetime.date.fromisoformat(p["SKT"]) - kontrol_tarihi).days
        if p_stok > 0 and p_gun >= 1:
            mevcut_aktif_toplam += p_stok
            kuyruk.append((p_gun, 0, p_stok, p["SKT"], False))

    for qty, skt_iso in clean_lots:
        if qty > 0:
            gun = (datetime.date.fromisoformat(skt_iso) - kontrol_tarihi).days
            kuyruk.append((gun, 1, qty, skt_iso, True))

    kuyruk.sort(key=lambda x: (x[0], x[1]))

    yeni_elden_kalacak = 0
    toplam_elden_kalacak = 0
    if hiz is not None and int(hiz) > 0:
        kullanilan = 0
        for gun, _, adet, _, yeni_mi in kuyruk:
            uygun = max(0, int(hiz) * gun - kullanilan)
            satilabilir = min(adet, uygun)
            tasan = adet - satilabilir
            kullanilan += satilabilir
            toplam_elden_kalacak += tasan
            if yeni_mi:
                yeni_elden_kalacak += tasan

    uyari_tipleri = []
    if fark != 0:
        uyari_tipleri.append("ADET_FARKI")
    if yeni_elden_kalacak > 0:
        uyari_tipleri.append("KISA_OMUR")

    if not uyari_tipleri:
        return {
            "status": "ONAYLANDI",
            "clean_lots": clean_lots,
            "total_qty": total_qty,
            "fark": 0,
            "excess_new": 0,
            "excess_total": toplam_elden_kalacak,
            "risk_tl": 0.0,
        }

    mesajlar = []
    if fark != 0:
        mesajlar.append(
            f"🚨 **KAPI 2 ALARM (KÖR SAYIM UYUŞMAZLIĞI):** Girdiğiniz toplam (**{total_qty} adet**) "
            "gizli irsaliye ile uyuşmuyor! Lütfen koliyi yeniden sayın ya da tutanakla işlem yapın."
        )
    if yeni_elden_kalacak > 0:
        raf_notu = (
            f" (raftaki mevcut **{mevcut_aktif_toplam} adet** stok dahil)"
            if mevcut_aktif_toplam > 0
            else ""
        )
        mesajlar.append(
            f"🟠 **KAPI 3 UYARI (KISA RAF ÖMRÜ):** {int(hiz)}/gün satış hızıyla{raf_notu} "
            f"bu teslimattan **{yeni_elden_kalacak} adet** satılamadan elde kalacak."
        )

    risk_adet = max(abs(fark), yeni_elden_kalacak)
    risk_tl = round(risk_adet * fiyat, 2)
    tutanak_detay = (
        f"Sayılan: {total_qty}, İrsaliye Farkı: {fark:+d}, "
        f"Yeni Teslimat Fazla Stok Riski: {yeni_elden_kalacak} adet"
    )
    return {
        "status": "UYARI",
        "uyari_tipleri": uyari_tipleri,
        "clean_lots": clean_lots,
        "total_qty": total_qty,
        "fark": fark,
        "excess_new": yeni_elden_kalacak,
        "excess_total": toplam_elden_kalacak,
        "risk_tl": risk_tl,
        "blind_message": "\n\n".join(mesajlar),
        "audit_detail": tutanak_detay,
    }


def new_day(state: dict, frame: pd.DataFrame, day: str):
    """
    Artimli Yeni Gun T+1 Satis Isleme:
    - Cift dosya kilidi (imza + tarih + icerik hash)
    - Takip disi barkodlari kilitlemeden atlar ve ozette raporlar
    - Stoktan fazla POS satışında kilitlemez, 0'a cekip uzlastirma uyarisi uretir
    - Hizi bilinmeyen urunlerde kademeli gunluk ortalama ile hiz ogrenir
    """
    known_codes = {p["Barkod"] for p in state["stok_kayitlari"]} | {
        k["Barkod"] for k in state.get("katalog", [])
    }
    counts = sales_map(frame, known_codes)
    key = signature(counts, day)
    history = state.get("pos_gecmisi", [])

    if any(h.get("id") == key for h in history):
        raise ValueError("🚫 ÇİFT İŞLEME KİLİDİ: Bu günün satış dosyası daha önce işlendi.")
    if history and day <= history[-1]["gun"]:
        raise ValueError(
            f"🚫 POS günü ({day}) son işlenen günden ({history[-1]['gun']}) sonra olmalıdır."
        )
    if "POS_Gunu" in frame.columns:
        gunler = {str(x).strip() for x in frame["POS_Gunu"].dropna()}
        if gunler != {day}:
            raise ValueError(
                f"🚫 Dosyadaki POS_Gunu ({', '.join(sorted(gunler))}) seçilen günle ({day}) uyuşmuyor."
            )
    digest = signature(counts, "")
    if "POS_Gunu" not in frame.columns and any(h.get("icerik") == digest for h in history):
        raise ValueError(
            "🚫 ÇİFT İŞLEME KİLİDİ: Aynı satış içeriği daha önce işlendi. Farklı güne aitse CSV'ye `POS_Gunu` ekleyin."
        )

    updated = deepcopy(state)

    for p in updated["stok_kayitlari"]:
        p["Dun_Stok"] = int(p["Kalan_Stok"])
        p["Satilan_Adet"] = 0
        p["Coklu_Parti_Uyari"] = False

    groups = {}
    for p in updated["stok_kayitlari"]:
        if p.get("Giris_Gunu", "") <= day:
            groups.setdefault(p["Barkod"], []).append(p)

    atlanan_barkodlar = []
    uzlastirma_uyarilari = []
    toplam_dusulen = 0

    for code, amount in counts.items():
        if code not in groups:
            if amount > 0:
                atlanan_barkodlar.append(code)
            continue

        lots = groups[code]
        lots.sort(key=lambda p: (p["SKT"], p["Parti_ID"]))
        mevcut_toplam = sum(int(p["Kalan_Stok"]) for p in lots)

        if amount > mevcut_toplam:
            fazla = amount - mevcut_toplam
            uzlastirma_uyarilari.append(
                f"`{code}` ({lots[0]['Urun_Adi']}): POS satışı ({amount}) kayıtlı stoktan ({mevcut_toplam}) "
                f"**{fazla} adet fazla**. Stok 0'a çekildi — fiziksel uzlaştırma önerilir."
            )

        remaining = amount
        for p in lots:
            stock = int(p["Kalan_Stok"])
            sold = min(stock, remaining)
            p["Satilan_Adet"] = sold
            p["Kalan_Stok"] = stock - sold
            remaining -= sold
            toplam_dusulen += sold

        for i, p in enumerate(lots):
            later_stock = any(q["SKT"] > p["SKT"] and int(q["Kalan_Stok"]) > 0 for q in lots[i + 1:])
            if p["Satilan_Adet"] > 0 and later_stock:
                if int(p["Kalan_Stok"]) == 0:
                    p["Sifir_Dogrulama_Gerekli"] = True
                else:
                    p["Coklu_Parti_Uyari"] = True

        katalog_kayit = next((k for k in updated.get("katalog", []) if k["Barkod"] == code), None)
        ogrenme_modu = (
            (katalog_kayit and (katalog_kayit.get("Gunluk_Hiz") is None or katalog_kayit.get("Hiz_Ogreniliyor")))
            or any(p.get("Gunluk_Hiz") is None or p.get("Hiz_Ogreniliyor") for p in lots)
        )
        if ogrenme_modu:
            kaynak = katalog_kayit or lots[0]
            yeni_gun = int(kaynak.get("Hiz_Gozlem_Gun", 0)) + 1
            yeni_toplam = int(kaynak.get("Hiz_Gozlem_Toplam", 0)) + amount
            yeni_hiz = max(1, round(yeni_toplam / yeni_gun)) if yeni_toplam > 0 else None

            hedefler = list(lots) + ([katalog_kayit] if katalog_kayit else [])
            for item in hedefler:
                item["Hiz_Ogreniliyor"] = True
                item["Hiz_Gozlem_Gun"] = yeni_gun
                item["Hiz_Gozlem_Toplam"] = yeni_toplam
                item["Gunluk_Hiz"] = yeni_hiz

    updated.setdefault("pos_gecmisi", []).append({
        "gun": day,
        "id": key,
        "icerik": digest,
        "mod": "Artımlı T+1 satış düşümü",
        "toplam_dusulen": toplam_dusulen,
    })
    updated["satis_dusuldu_mu"] = True
    updated["son_uzlastirma_uyarilari"] = uzlastirma_uyarilari

    ozet = {
        "toplam_dusulen": toplam_dusulen,
        "atlanan_sayisi": len(atlanan_barkodlar),
        "atlanan_barkodlar": atlanan_barkodlar,
        "uzlastirma_uyarilari": uzlastirma_uyarilari,
    }
    return updated, ozet


def audit_priority(p: dict):
    """
    Denetim ve kalan gun oncelikli siralama:
    1) SKT_DOLDU veya son 1 gune dusmus SIFIR_DOGRULAMA (Denetim Riski)
    2) Renk: KIRMIZI -> TURUNCU -> YESIL
    3) Kalan gun (azdan coka)
    4) Henuz fiziksel kontrol edilmemis olan once
    5) Fire riski TL (yuksekten dusuge)
    """
    critical = (
        p["Gorev_Tipi"] == "SKT_DOLDU"
        or (p["Gorev_Tipi"] == "SIFIR_DOGRULAMA" and p["Kalan_Gun"] <= 1)
    )
    not_checked = 0 if not p.get("Son_Fiziksel_Kontrol") else 1
    return (
        not critical,
        {"KIRMIZI": 0, "TURUNCU": 1, "YESIL": 2}[p["Durum"]],
        p["Kalan_Gun"],
        not_checked,
        -p["Risk_TL"],
    )
