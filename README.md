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
- GitHub Actions ile her gün Türkiye saatiyle 03:00'te otomatik güncelleme

## Beklenen Kayıt Sayıları

| Kategori | Beklenen adet |
| --- | ---: |
| Kurum Yönetmeliği | 51 |
| Esas ve Usuller | 24 |
| Yönerge | 149 |
| İlke Kararı | 2 |
| Toplam | 226 |

Scraper bu sayılarla eşleşmeyen bir sonuç üretirse başarılı sayılmaz; açıkça hata verir.

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
4. `.github/workflows/update-mevzuat.yml` workflow dosyası siteyi otomatik yayınlar.

## Otomatik Güncelleme

GitHub Actions workflow her gün `00:00 UTC` saatinde çalışır. Bu saat Türkiye saatiyle `03:00` anlamına gelir.

Workflow şunları yapar:

- Python ve Playwright kurar
- Scraper'ı çalıştırır
- `data/mevzuatlar.json` veya ilgili veri/log dosyaları değiştiyse otomatik commit atar
- Statik siteyi GitHub Pages'e yayınlar

## Notlar

- PDF dosyaları indirilmez; resmi DETSİS/PDF linkleri saklanır.
- JSON çıktısı `ensure_ascii=False` ile yazılır, Türkçe karakterler korunur.
- CSV çıktısı Excel uyumluluğu için `utf-8-sig` ile yazılır.
- Kod Windows ve macOS/Linux üzerinde çalışacak şekilde platform bağımsız tutulmuştur.
