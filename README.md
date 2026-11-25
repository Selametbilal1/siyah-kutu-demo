# Siyah Kutu v0.3.4 — Saha Pilot Sürümü

Yerel ve orta ölçekli marketler için mevcut ERP/POS sistemlerinin üzerine oturan, **FEFO (First-Expired, First-Out)** raf takibi, **denetim cezası kalkanı** ve **kör sayım mal kabul** uygulaması.

## v0.3.4'te Neler İyileştirildi?

- **Takip dışı barkod & POS uzlaştırma toleransı:** Gerçek POS satış dosyalarındaki takip dışı ürünler sessizce atlanır; stoktan fazla satışta gün kilitlenmez, stok `0`'a çekilip sabah radarında uzlaştırma uyarısı verilir.
- **Sert EAN-8 / EAN-13 barkod kilidi:** Harf, 12/14 hane veya yanlış kontrol basamağı girildiğinde yeni ürün formu kilitlenir ve doğru kontrol basamağı bildirilir.
- **Gerçek kör sayım (Kapı 2):** İlk sayım uyuşmazlığında gizli irsaliye farkı (`-2` gibi) ekrana sızdırılmaz; fark detayı yalnızca tutanak/kabul defterine yazılır.
- **Raftaki mevcut stoğu gören Kapı 3:** Mal kabul hız/kapasite kontrolü, yeni gelen partilerin yanında aynı barkodun rafta hâlihazırda bekleyen aktif stoğunu da sıraya dahil eder.
- **Ayrı Yeni Gün T+1 & Sıfırlama akışı:** Artımlı satış yükleyicisi sıfırlamadan ayrıldı; çift dosya kilidi ve Excel `3.0` ondalık format desteği eklendi.
- **Kademeli hız öğrenme & veri tutarlılığı:** Hızı bilinmeyen yeni ürünlerde hız tek günlük satışla sabitlenmez, günler geçtikçe hareketli ortalamayla öğrenilir; `dun_stok.csv` ve `irsaliye.csv` hız değerleri eşitlendi.

## Hızlı Kurulum (Yerel)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

## 2 Dakikalık Saha Doğrulama Akışı

1. **Denetim önceliği (Sekme 1):** 24 Kasım açılışında `SKT_DOLDU` ürünleri ve `0 TL` riskli `Kinder Pingui 30g` doğrulama görevinin listenin en üstünde olduğunu görün.
2. **Barkod kilidi (Sekme 2):** `8691234567891` veya `123456789012` yazıp formun kilitlendiğini doğrulayın.
3. **Karma SKT + mevcut stoklu Kapı 3 (Sekme 3):** `ornek/yogurt_ean13.png` görselini yükleyin (`8691234567890` otomatik seçilir), `8 + 4` adet iki farklı SKT girin; raftaki 40 adet stok dahil Kapı 3 uyarısını görün ve tutanakla kabul edin.
4. **Artımlı Yeni Gün T+1 (Sol Menü):** `ornek/25_kasim_acilis.csv` dosyasını **Yeni Gün T+1 Satışı İşle** alanına yükleyip `24.11.2026` için çalıştırın; takvimin otomatik `25.11.2026` açılışına geçtiğini görün.
5. **Çift dosya kilidi:** Aynı `25_kasim_acilis.csv` dosyasını tekrar işlemeye çalışıp engellendiğini doğrulayın.
