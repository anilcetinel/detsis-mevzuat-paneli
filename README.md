# DETSİS Mevzuat Paneli

Sakarya Üniversitesi Rektörlüğü DETSİS sayfasındaki **Mevzuat** sekmesini otomatik tarayan, veriyi JSON/CSV olarak saklayan ve ücretsiz GitHub Pages üzerinde yayınlanabilecek statik bir panel üreten projedir.

Kaynak: <https://detsis.gov.tr/birim/35955870/35955870/2026-05-20>

## Canlı Panel Linki

<https://anilcetinel.github.io/detsis-mevzuat-paneli/>

## Özellikler

- Python + Playwright scraper
- `Kurum Yönetmeliği`, `Esas ve Usuller`, `Yönerge`, `İlke Kararı` kategorilerini sırayla açar
- Kayıtları `data/mevzuatlar.json` ve `data/mevzuatlar.csv` dosyalarına yazar
- Önceki JSON çıktısını `data/archive/mevzuatlar_YYYY-MM-DD_HH-MM.json` olarak yedekler
- Eksik veri olduğunda hata verir ve `logs/hata_log.csv` dosyasına yazar
- Arama, kategori filtresi, kayıt sayısı, kategori kartları ve resmi link butonu olan statik panel
- Windows Görev Zamanlayıcı ile 6 saatte bir yerel otomatik güncelleme

## Veri Bütünlüğü Kontrolleri

DETSİS kayıt sayısı zaman içinde değişebilir. Scraper sabit toplam veya kategori adedi beklemek yerine şu kontrolleri yapar:

- Toplam kayıt `0` ise hata verir.
- Kategorisi boş kayıt varsa hata verir.
- Resmi linki eksik kayıt varsa hata verir.
- JSON çıktı dosyası oluşmazsa hata verir.
- Kayıt sayısı değişirse `logs/hata_log.csv` içine bilgi yazar.
- Yeni kayıtları `logs/new_records.log` içine ekler.

## Kurulum

Python 3.12 önerilir.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

Windows PowerShell için:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
```

## Scraper Çalıştırma

```bash
python scraper.py
```

Başarılı çalışırsa şu dosyalar güncellenir:

- `data/mevzuatlar.json`
- `data/mevzuatlar.csv`

Hata veya eksik sayı varsa:

- Komut hata koduyla biter
- Ayrıntılar `logs/hata_log.csv` dosyasına yazılır
- Eksik veri varken başarılı kabul edilmez

## Paneli Yerelde Açma

Scraper çalıştıktan sonra `index.html` dosyasını tarayıcıda açabilirsiniz. Bazı tarayıcılar yerel dosyada `fetch` kısıtı uygulayabilir. Bu durumda basit bir yerel sunucu kullanın:

```bash
python -m http.server 8000
```

Ardından <http://localhost:8000> adresini açın.

## GitHub Pages Yayınlama

1. Projeyi GitHub deposuna gönderin.
2. Depoda **Settings > Pages** bölümüne gidin.
3. Source olarak **GitHub Actions** seçin.
4. `.github/workflows/deploy-pages.yml` workflow dosyası siteyi otomatik yayınlar.

## Otomatik Güncelleme

DETSİS, GitHub Actions sunucularından gelen isteklerde zaman zaman timeout verebildiği için en sağlam ücretsiz yöntem scraper'ı DETSİS'e erişebilen bu Windows bilgisayarda çalıştırmaktır.

Yerel otomatik güncelleme şunları yapar:

- Windows Görev Zamanlayıcı scraper'ı 6 saatte bir çalıştırır.
- Veri DETSİS'e yerel ağdan erişilerek güncellenir.
- Değişiklik varsa GitHub'a commit/push yapılır.
- GitHub Pages deploy workflow'u siteyi ücretsiz yayınlar.

Kurulum:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\register_windows_task.ps1
```

Elle test:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\local_update.ps1
```

Log dosyası:

```text
logs/local_scheduler.log
```

GitHub Actions içindeki `update-mevzuat.yml` manuel çalıştırma için bırakılmıştır. Otomatik 6 saatlik güncelleme yerel Windows göreviyle yapılır.

## Notlar

- PDF dosyaları indirilmez; resmi DETSİS/PDF linkleri saklanır.
- JSON çıktısı `ensure_ascii=False` ile yazılır, Türkçe karakterler korunur.
- CSV çıktısı Excel uyumluluğu için `utf-8-sig` ile yazılır.
- Kod Windows ve macOS/Linux üzerinde çalışacak şekilde platform bağımsız tutulmuştur.
