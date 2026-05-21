from __future__ import annotations

import asyncio
import concurrent.futures
import csv
import html as html_lib
import io
import json
import re
import shutil
import threading
import urllib.request
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError, async_playwright


SOURCE_URL = "https://detsis.gov.tr/birim/35955870/35955870/2026-05-20"
DETSIS_ID = "35955870"
MEVZUAT_API_URL = f"https://yetkiliapi.detsis.gov.tr/api/backoffice/unauthorizedintegration/kunye/mevzuatlar?detsisId={DETSIS_ID}"
CATEGORIES = ["Kurum Yönetmeliği", "Esas ve Usuller", "Yönerge", "İlke Kararı"]

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
ARCHIVE_DIR = DATA_DIR / "archive"
LOG_DIR = ROOT / "logs"
JSON_PATH = DATA_DIR / "mevzuatlar.json"
CSV_PATH = DATA_DIR / "mevzuatlar.csv"
ERROR_LOG_PATH = LOG_DIR / "hata_log.csv"
NEW_RECORDS_LOG_PATH = LOG_DIR / "new_records.log"
CHANGE_REPORT_PATH = LOG_DIR / "son_degisim_raporu.json"
USER_AGENT = "Mozilla/5.0 (compatible; DETSIS-Mevzuat-Paneli/1.0)"
LOG_LOCK = threading.Lock()
CHANGE_DATE_ERRORS: list[dict[str, str]] = []
PAGE_GOTO_TIMEOUT_MS = 120_000
NETWORK_IDLE_TIMEOUT_MS = 45_000
SCRAPE_MAX_ATTEMPTS = 4
SCRAPE_RETRY_DELAY_SECONDS = 20


@dataclass(frozen=True)
class Regulation:
    kategori: str
    mevzuat_adı: str
    tarih: str
    resmi_link: str
    kaynak_url: str
    son_degisim_tarihi: str = "-"
    son_degisim_yontemi: str = "Bulunamadı"


def log(message: str) -> None:
    print(f"[{datetime.now().isoformat(timespec='seconds')}] {message}", flush=True)


def ensure_dirs() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(exist_ok=True)


def archive_existing_data() -> None:
    if not JSON_PATH.exists():
        return
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    archive_path = ARCHIVE_DIR / f"mevzuatlar_{stamp}.json"
    if archive_path.exists():
        archive_path = ARCHIVE_DIR / f"mevzuatlar_{stamp}-{datetime.now().second:02d}.json"
    shutil.copy2(JSON_PATH, archive_path)


def append_error(message: str, details: str = "") -> None:
    ensure_dirs()
    with LOG_LOCK:
        new_file = not ERROR_LOG_PATH.exists()
        with ERROR_LOG_PATH.open("a", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["zaman", "hata", "detay"])
            if new_file:
                writer.writeheader()
            writer.writerow(
                {
                    "zaman": datetime.now().isoformat(timespec="seconds"),
                    "hata": message,
                    "detay": details,
                }
            )


def append_change_date_error(record_name: str, url: str, error_type: str, details: str) -> None:
    with LOG_LOCK:
        CHANGE_DATE_ERRORS.append(
            {
                "mevzuat_adı": record_name,
                "url": url,
                "hata": error_type,
                "detay": details,
            }
        )


def append_new_records(records: list["Regulation"]) -> None:
    if not records:
        return
    ensure_dirs()
    with NEW_RECORDS_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"\n{datetime.now().isoformat(timespec='seconds')} - Yeni kayıtlar\n")
        for record in records:
            handle.write(f"- [{record.kategori}] {record.mevzuat_adı} | {record.resmi_link}\n")


def load_previous_records() -> list[dict[str, str]]:
    return load_previous_payload().get("kayitlar", [])


def load_previous_payload() -> dict[str, object]:
    if not JSON_PATH.exists():
        return {}
    try:
        return json.loads(JSON_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        append_error("Önceki JSON okunamadı", str(exc))
        return {}


def previous_records_are_valid(records: list[dict[str, str]]) -> bool:
    if not records:
        append_error("Mevcut veri geçersiz", "Önceki JSON içinde kayıt yok.")
        return False
    return True


def previous_dicts_to_records(records: list[dict[str, str]]) -> list[Regulation]:
    normalized: list[Regulation] = []
    for record in records:
        normalized.append(
            Regulation(
                kategori=record.get("kategori", ""),
                mevzuat_adı=record.get("mevzuat_adı", ""),
                tarih=record.get("tarih") or "-",
                resmi_link=record.get("resmi_link", ""),
                kaynak_url=record.get("kaynak_url") or SOURCE_URL,
                son_degisim_tarihi=record.get("son_degisim_tarihi") or "-",
                son_degisim_yontemi=record.get("son_degisim_yontemi") or "Bulunamadı",
            )
        )
    return normalized


def record_fingerprint(record: Regulation | dict[str, str]) -> tuple[str, str, str, str, str]:
    if isinstance(record, Regulation):
        return (
            record.kategori,
            record.mevzuat_adı,
            record.tarih,
            record.resmi_link,
            record.son_degisim_tarihi,
        )
    return (
        record.get("kategori", ""),
        record.get("mevzuat_adı", ""),
        record.get("tarih", ""),
        record.get("resmi_link", ""),
        record.get("son_degisim_tarihi", ""),
    )


def records_changed(records: list[Regulation], previous_records: list[dict[str, str]]) -> bool:
    if len(records) != len(previous_records):
        return True
    current = sorted(record_fingerprint(record) for record in records)
    previous = sorted(record_fingerprint(record) for record in previous_records)
    return current != previous


async def click_text(page: Page, text: str, timeout: int = 8_000) -> bool:
    pattern = re.compile(rf"^\s*{re.escape(text)}(?:\s*\(\d+\))?\s*$", re.I)
    candidates = [
        page.get_by_role("tab", name=pattern),
        page.get_by_role("button", name=pattern),
        page.get_by_text(text, exact=True),
        page.get_by_text(pattern, exact=False),
        page.locator(f"text={text}").first,
    ]
    for locator in candidates:
        try:
            count = await locator.count()
            for index in range(min(count, 8)):
                item = locator.nth(index)
                try:
                    if await item.is_visible(timeout=1_000):
                        await item.click(timeout=timeout)
                        await page.wait_for_timeout(500)
                        return True
                except Exception:
                    continue
        except Exception:
            continue
    return False


async def accept_cookie_notice(page: Page) -> None:
    for label in ("Tamam", "Kabul Et", "Kabul ediyorum"):
        try:
            await page.get_by_text(label, exact=True).click(timeout=3_000)
            await page.wait_for_timeout(300)
            return
        except Exception:
            continue


async def abort_heavy_assets(route) -> None:
    await route.abort()


async def open_mevzuat_tab(page: Page) -> None:
    log("Mevzuat sekmesi açılıyor.")
    opened = await click_text(page, "Mevzuat", timeout=12_000)
    if not opened:
        try:
            opened = await page.evaluate(
                """
                () => {
                  const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim().toLocaleLowerCase('tr-TR');
                  const node = [...document.querySelectorAll('button, a, [role="tab"], [role="button"], li, div, span')]
                    .filter((el) => {
                      const text = normalize(el.innerText || el.textContent || '');
                      return text === 'mevzuat' && el.getBoundingClientRect().width > 0 && el.getBoundingClientRect().height > 0;
                    })
                    .sort((a, b) => (a.innerText || '').length - (b.innerText || '').length)[0];
                  if (!node) return false;
                  node.scrollIntoView({ block: 'center', inline: 'center' });
                  node.click();
                  return true;
                }
                """
            )
            if opened:
                await page.wait_for_timeout(800)
        except Exception:
            opened = False
    if not opened:
        body = clean_text((await page.locator("body").inner_text())[:1_500])
        raise RuntimeError(f"Mevzuat sekmesi bulunamadı veya açılamadı. Sayfa metni: {body}")
    try:
        await page.get_by_text(re.compile(r"Toplam\s+\d+\s+adet\s+mevzuat", re.I)).wait_for(timeout=12_000)
    except PlaywrightTimeoutError as exc:
        body = clean_text((await page.locator("body").inner_text())[:1_500])
        raise RuntimeError(f"Mevzuat sekmesi açıldı ancak kayıt özeti görünmedi. Sayfa metni: {body}") from exc


async def expand_category(page: Page, category: str) -> None:
    log(f"Kategori genişletiliyor: {category}")
    opened = await click_text(page, category, timeout=10_000)
    if not opened:
        raise RuntimeError(f"Kategori açılamadı: {category}")


async def category_html(page: Page, category: str) -> str:
    """Return nearby HTML for the category when possible, otherwise the full body."""
    escaped = re.escape(category)
    script = f"""
    (category) => {{
      const nodes = [...document.querySelectorAll('body *')]
        .filter((node) => (node.innerText || '').trim().match(new RegExp(category, 'i')));
      const header = nodes.find((node) => (node.innerText || '').trim().split(/\\n/)[0].match(new RegExp(category, 'i')));
      if (!header) return document.body.innerHTML;

      const containers = [];
      let node = header;
      for (let i = 0; node && i < 5; i++, node = node.parentElement) containers.push(node);
      const useful = containers.find((node) => {{
        const text = node.innerText || '';
        const links = node.querySelectorAll('a').length;
        return text.length > category.length + 40 && links > 0;
      }});
      return (useful || header.parentElement || document.body).innerHTML;
    }}
    """
    try:
        return await page.evaluate(script, escaped)
    except Exception:
        return await page.locator("body").inner_html()


DATE_RE = re.compile(r"(?<!\d)(\d{1,2}[./-]\d{1,2}[./-]\d{4}|\d{4}[./-]\d{1,2}[./-]\d{1,2})(?!\d)")
CHANGE_HTML_LABELS = ("Son Güncelleme Tarihi", "Revizyon Tarihi", "Değişiklik Tarihi")
CHANGE_PDF_LABELS = (
    "Son Değişiklik",
    "Revizyon Tarihi",
    "Değişiklik Tarihi",
    "Güncelleme Tarihi",
    "Değişiklik Yapılan Tarih",
)


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip(" -–—\t\r\n")


def parse_date_value(value: str) -> datetime | None:
    match = DATE_RE.search(value or "")
    if not match:
        return None
    raw = match.group(1).replace("-", ".").replace("/", ".")
    parts = [int(part) for part in raw.split(".")]
    if len(parts) != 3:
        return None
    if parts[0] > 1900:
        year, month, day = parts
    else:
        day, month, year = parts
    try:
        return datetime(year, month, day)
    except ValueError:
        return None


def format_short_date(value: datetime) -> str:
    return value.strftime("%d.%m.%Y")


def format_api_date(value: str) -> str:
    if not value:
        return "-"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%d/%m/%Y")
    except ValueError:
        parsed = parse_date_value(value)
        return parsed.strftime("%d/%m/%Y") if parsed else "-"


def fetch_bytes(url: str, timeout: int = 18) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_text(url: str, timeout: int = 18) -> str:
    body = fetch_bytes(url, timeout=timeout)
    return body.decode("utf-8", errors="ignore")


def fetch_json(url: str, timeout: int = 45) -> dict[str, object]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://detsis.gov.tr",
            "Referer": SOURCE_URL,
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return json.loads(response.read().decode(charset, errors="ignore"))


def strip_html(value: str) -> str:
    without_scripts = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", value, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", without_scripts)
    return clean_text(html_lib.unescape(text))


def newest_date_near_labels(text: str, labels: Iterable[str]) -> str:
    dates: list[datetime] = []
    lowered = text.casefold()
    for label in labels:
        label_lower = label.casefold()
        start = 0
        while True:
            index = lowered.find(label_lower, start)
            if index == -1:
                break
            window = text[max(0, index - 80) : index + len(label) + 220]
            for match in DATE_RE.finditer(window):
                parsed = parse_date_value(match.group(1))
                if parsed:
                    dates.append(parsed)
            start = index + len(label_lower)
    return format_short_date(max(dates)) if dates else "-"


def find_pdf_urls(html: str, base_url: str) -> list[str]:
    candidates = re.findall(r"""(?:href|src)=["']([^"']+)["']""", html, flags=re.I)
    result: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        url = urljoin(base_url, html_lib.unescape(candidate))
        lower = url.casefold()
        if ".pdf" not in lower and "pdf" not in lower:
            continue
        if url in seen:
            continue
        seen.add(url)
        result.append(url)
    return result


def extract_pdf_text(pdf_url: str, record_name: str) -> str:
    try:
        from pypdf import PdfReader
    except Exception as exc:
        append_error("PDF okuma bağımlılığı yüklenemedi", str(exc))
        append_change_date_error(record_name, pdf_url, "PDF bağımlılığı eksik", str(exc))
        return ""

    try:
        reader = PdfReader(io.BytesIO(fetch_bytes(pdf_url, timeout=25)))
        pages = []
        for page in reader.pages[:8]:
            pages.append(page.extract_text() or "")
        return clean_text(" ".join(pages))
    except Exception as exc:
        details = f"{pdf_url} | {exc}"
        append_error("PDF metni okunamadı", details)
        append_change_date_error(record_name, pdf_url, "PDF indirilemedi veya okunamadı", str(exc))
        return ""


def extract_change_date(record: Regulation) -> tuple[str, str]:
    if not record.resmi_link:
        append_change_date_error(record.mevzuat_adı, "", "Resmi link yok", "Son değişiklik tarihi kontrolü atlandı.")
        return "-", "Bulunamadı"
    try:
        html = fetch_text(record.resmi_link)
    except Exception as exc:
        append_error("Resmi DETSİS sayfası okunamadı", f"{record.resmi_link} | {exc}")
        append_change_date_error(record.mevzuat_adı, record.resmi_link, "Resmi sayfa okunamadı", str(exc))
        return "-", "Bulunamadı"

    html_date = newest_date_near_labels(strip_html(html), CHANGE_HTML_LABELS)
    if html_date != "-":
        return html_date, "HTML"

    for pdf_url in find_pdf_urls(html, record.resmi_link)[:2]:
        pdf_text = extract_pdf_text(pdf_url, record.mevzuat_adı)
        if not pdf_text:
            continue
        pdf_date = newest_date_near_labels(pdf_text, CHANGE_PDF_LABELS)
        if pdf_date != "-":
            return pdf_date, "PDF"
    return "-", "Bulunamadı"


def clean_regulation_name(value: str) -> str:
    name = clean_text(value)
    return re.sub(r"^Sayı\s+[0-9A-Za-zÇĞİÖŞÜçğıöşü./-]+\s+", "", name, flags=re.I).strip()


async def extract_from_dom(page: Page, category: str) -> list[Regulation]:
    section_link_records = await page.evaluate(
        """
        ({ sourceUrl, category, categories }) => {
          const isVisible = (el) => {
            const style = window.getComputedStyle(el);
            const box = el.getBoundingClientRect();
            return style && style.visibility !== 'hidden' && style.display !== 'none' && box.width > 0 && box.height > 0;
          };
          const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
          const categoryPattern = (name) => new RegExp(`^${name.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&')}(?:\\\\s*\\\\(\\\\d+\\\\))?$`, 'i');
          const allNodes = [...document.querySelectorAll('body *')];
          const header = allNodes
            .filter((node) => isVisible(node) && categoryPattern(category).test(normalize(node.innerText)))
            .sort((a, b) => normalize(a.innerText).length - normalize(b.innerText).length)[0];
          if (!header) return [];

          const otherHeaders = allNodes
            .filter((node) => isVisible(node) && categories.some((name) => name !== category && categoryPattern(name).test(normalize(node.innerText))))
            .filter((node) => Boolean(header.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING))
            .sort((a, b) => {
              if (a === b) return 0;
              return a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1;
            });
          const nextHeader = otherHeaders[0] || null;
          const inSection = (node) => {
            const afterHeader = Boolean(header.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING);
            const beforeNext = !nextHeader || Boolean(node.compareDocumentPosition(nextHeader) & Node.DOCUMENT_POSITION_FOLLOWING);
            return afterHeader && beforeNext;
          };

          const rows = [];
          for (const link of document.querySelectorAll('a[href]')) {
            const href = new URL(link.getAttribute('href'), sourceUrl).toString();
            if (!href.includes('kms.kaysis.gov.tr/Home/Goster')) continue;
            if (!isVisible(link)) continue;
            if (!inSection(link)) continue;
            const name = (link.innerText || link.getAttribute('aria-label') || link.getAttribute('title') || '').replace(/ yeni sekmede aç$/i, '').trim();
            if (!name) continue;

            let node = link;
            let text = name;
            for (let i = 0; node && i < 8; i++, node = node.parentElement) {
              const candidate = (node.innerText || '').replace(/\\s+/g, ' ').trim();
              if (candidate.includes(name) && (candidate.includes('Resmi Gazete') || candidate.includes('Sayı'))) {
                text = candidate;
                break;
              }
            }
            rows.push({ text, href });
          }
          return rows;
        }
        """,
        {"sourceUrl": SOURCE_URL, "category": category, "categories": [*CATEGORIES, "Uygulama / Program Esasları"]},
    )

    if section_link_records:
        return dedupe(
            Regulation(
                kategori=category,
                mevzuat_adı=clean_regulation_name(re.sub(r"\s+yeni sekmede aç$", "", row["text"].split(" Resmi Gazete ")[0].replace("Mevzuat ", ""))),
                tarih=(DATE_RE.search(row["text"]).group(1) if DATE_RE.search(row["text"]) else "-"),
                resmi_link=row["href"],
                kaynak_url=SOURCE_URL,
            )
            for row in section_link_records
        )

    html = await category_html(page, category)
    records = await page.evaluate(
        """
        ({ html, category, sourceUrl }) => {
          const root = document.createElement('div');
          root.innerHTML = html;
          const rows = [];
          const tables = [...root.querySelectorAll('table')];

          for (const table of tables) {
            for (const tr of table.querySelectorAll('tr')) {
              const cells = [...tr.querySelectorAll('th,td')].map((cell) => cell.innerText.trim()).filter(Boolean);
              if (!cells.length || cells.join(' ').toLocaleLowerCase('tr-TR').includes(category.toLocaleLowerCase('tr-TR'))) continue;
              const link = tr.querySelector('a[href]');
              rows.push({ text: cells.join(' | '), href: link ? link.getAttribute('href') : '' });
            }
          }

          const links = [...root.querySelectorAll('a[href]')];
          for (const link of links) {
            const text = link.innerText.trim() || link.getAttribute('title') || link.getAttribute('href') || '';
            rows.push({ text, href: link.getAttribute('href') || '' });
          }

          if (!rows.length) {
            const items = [...root.querySelectorAll('li, .list-group-item, [role="row"]')];
            for (const item of items) {
              const link = item.querySelector('a[href]');
              rows.push({ text: item.innerText.trim(), href: link ? link.getAttribute('href') : '' });
            }
          }

          const seen = new Set();
          return rows
            .map((row) => ({
              text: row.text.replace(/\\s+/g, ' ').trim(),
              href: row.href ? new URL(row.href, sourceUrl).toString() : ''
            }))
            .filter((row) => row.text && !row.text.toLocaleLowerCase('tr-TR').includes('mevzuat adı'))
            .filter((row) => {
              const key = `${row.text}|${row.href}`;
              if (seen.has(key)) return false;
              seen.add(key);
              return true;
            });
        }
        """,
        {"html": html, "category": category, "sourceUrl": SOURCE_URL},
    )

    regulations: list[Regulation] = []
    for row in records:
        text = clean_text(row["text"])
        if not text or text.casefold() == category.casefold():
            continue
        date_match = DATE_RE.search(text)
        date = date_match.group(1) if date_match else "-"
        name = clean_regulation_name(DATE_RE.sub("", text))
        name = re.sub(r"^\d+\s*[.)-]\s*", "", name).strip()
        if len(name) < 3:
            continue
        regulations.append(
            Regulation(
                kategori=category,
                mevzuat_adı=name,
                tarih=date,
                resmi_link=row.get("href", ""),
                kaynak_url=SOURCE_URL,
            )
        )
    return dedupe(regulations)


def dedupe(records: Iterable[Regulation]) -> list[Regulation]:
    seen: set[tuple[str, str, str]] = set()
    result: list[Regulation] = []
    for record in records:
        key = (record.kategori.casefold(), record.mevzuat_adı.casefold(), record.resmi_link)
        if key in seen:
            continue
        seen.add(key)
        result.append(record)
    return result


async def scrape() -> list[Regulation]:
    try:
        return scrape_from_api()
    except Exception as exc:
        append_error("DETSİS mevzuat API başarısız; arayüz fallback deneniyor", str(exc))
        log(f"UYARI: API scrape başarısız, arayüz fallback deneniyor: {exc}")
    return await scrape_from_page()


def scrape_from_api() -> list[Regulation]:
    log(f"DETSİS mevzuat API okunuyor: {MEVZUAT_API_URL}")
    payload = fetch_json(MEVZUAT_API_URL)
    items = payload.get("data")
    if not isinstance(items, list):
        raise RuntimeError("DETSİS mevzuat API beklenen listeyi döndürmedi.")

    records: list[Regulation] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        category = clean_text(str(item.get("tur") or ""))
        if category not in CATEGORIES:
            continue
        regulation_id = str(item.get("mevzuatId") or "").strip()
        name = clean_regulation_name(str(item.get("ad") or ""))
        if not name:
            continue
        records.append(
            Regulation(
                kategori=category,
                mevzuat_adı=name,
                tarih=format_api_date(str(item.get("rgTarih") or "")),
                resmi_link=f"https://kms.kaysis.gov.tr/Home/Goster/{regulation_id}" if regulation_id else "",
                kaynak_url=SOURCE_URL,
            )
        )

    records = dedupe(records)
    log(f"DETSİS API toplam benzersiz kayıt: {len(records)}")
    for category in CATEGORIES:
        log(f"{category}: {sum(1 for record in records if record.kategori == category)} kayıt")
    if not records:
        raise RuntimeError("DETSİS mevzuat API 0 kayıt döndürdü.")
    return records


async def scrape_from_page() -> list[Regulation]:
    log(f"Kaynak sayfa açılıyor: {SOURCE_URL}")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-dev-shm-usage", "--no-sandbox"])
        page = await browser.new_page(locale="tr-TR", viewport={"width": 1440, "height": 1200})
        page.set_default_timeout(30_000)
        await page.set_extra_http_headers({"User-Agent": USER_AGENT})
        await page.route(
            re.compile(r".*\.(?:png|jpg|jpeg|gif|webp|svg|ico|woff|woff2|ttf|mp4|webm)(?:\?.*)?$", re.I),
            abort_heavy_assets,
        )
        try:
            await page.goto(SOURCE_URL, wait_until="commit", timeout=PAGE_GOTO_TIMEOUT_MS)
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=PAGE_GOTO_TIMEOUT_MS)
            except PlaywrightTimeoutError:
                log("UYARI: domcontentloaded beklemesi zaman aşımına uğradı, mevcut DOM ile devam ediliyor.")
            try:
                await page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_TIMEOUT_MS)
            except PlaywrightTimeoutError:
                log("UYARI: networkidle beklemesi zaman aşımına uğradı, mevcut DOM ile devam ediliyor.")
            await accept_cookie_notice(page)
            await open_mevzuat_tab(page)

            all_records: list[Regulation] = []
            for category in CATEGORIES:
                await expand_category(page, category)
                await page.wait_for_timeout(750)
                category_records = await extract_from_dom(page, category)
                log(f"{category}: {len(category_records)} kayıt bulundu.")
                all_records.extend(category_records)
            records = dedupe(all_records)
            log(f"Toplam benzersiz kayıt: {len(records)}")
            return records
        finally:
            await browser.close()


async def scrape_with_retries(max_attempts: int = SCRAPE_MAX_ATTEMPTS) -> list[Regulation]:
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            log(f"Scrape denemesi {attempt}/{max_attempts}")
            return await scrape()
        except Exception as exc:
            last_error = exc
            append_error(f"Scrape denemesi başarısız ({attempt}/{max_attempts})", str(exc))
            log(f"UYARI: scrape denemesi başarısız ({attempt}/{max_attempts}): {exc}")
            if attempt < max_attempts:
                await asyncio.sleep(SCRAPE_RETRY_DELAY_SECONDS)
    assert last_error is not None
    raise last_error


def validate(records: list[Regulation], previous_records: list[dict[str, str]]) -> None:
    errors: list[str] = []

    if not records:
        errors.append("Toplam kayıt 0 geldi")

    missing_categories = [record.mevzuat_adı for record in records if not record.kategori]
    if missing_categories:
        append_error("Kategori boş kayıt algılandı", f"Kategori boş kayıt sayısı: {len(missing_categories)}")

    missing_links = [record.mevzuat_adı for record in records if not record.resmi_link]
    if missing_links:
        append_error("Link eksik kayıt algılandı", f"Link eksik kayıt sayısı: {len(missing_links)}")

    if errors:
        message = "Mevzuat veri bütünlüğü kontrolü başarısız."
        append_error(message, " | ".join(errors))
        raise RuntimeError(f"{message} {' | '.join(errors)}")

    previous_count = len(previous_records)
    if previous_count and previous_count != len(records):
        append_error("Yeni mevzuat sayısı algılandı", f"Yeni mevzuat sayısı algılandı: {len(records)}")
        log(f"Bilgi: Yeni mevzuat sayısı algılandı: {len(records)}")

    previous_links = {record.get("resmi_link", "") for record in previous_records}
    if previous_links:
        new_records = [record for record in records if record.resmi_link and record.resmi_link not in previous_links]
        append_new_records(new_records)
        if new_records:
            log(f"Yeni kayıt sayısı: {len(new_records)}")


def enrich_change_dates(records: list[Regulation]) -> list[Regulation]:
    log("Son değişiklik tarihi kontrolü başlıyor.")
    enriched: list[Regulation] = []

    def enrich_one(record: Regulation) -> Regulation:
        date, method = extract_change_date(record)
        return replace(record, son_degisim_tarihi=date, son_degisim_yontemi=method)

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        for index, record in enumerate(executor.map(enrich_one, records), start=1):
            enriched.append(record)
            if index == 1 or index % 25 == 0 or index == len(records):
                log(f"Son değişiklik kontrolü: {index}/{len(records)}")
    return enriched


def build_change_report(records: list[Regulation]) -> dict[str, object]:
    html_count = sum(1 for record in records if record.son_degisim_yontemi == "HTML")
    pdf_count = sum(1 for record in records if record.son_degisim_yontemi == "PDF")
    not_found = sum(1 for record in records if record.son_degisim_yontemi == "Bulunamadı")
    found = html_count + pdf_count
    missing = len(records) - found
    pdf_errors = [error for error in CHANGE_DATE_ERRORS if "PDF" in error.get("hata", "")]
    return {
        "toplam": len(records),
        "bulunan": found,
        "bulunamayan": missing,
        "html": html_count,
        "pdf": pdf_count,
        "bulunamadi": not_found,
        "pdf_indirilemeyen": len(pdf_errors),
        "hata_sayisi": len(CHANGE_DATE_ERRORS),
        "hatalar": CHANGE_DATE_ERRORS[-100:],
        "olusturma_tarihi": datetime.now().isoformat(timespec="seconds"),
        "yontemler": {
            "HTML": "DETSİS resmi kayıt sayfasındaki son güncelleme, revizyon veya değişiklik tarihi alanı",
            "PDF": "Resmi kayıt sayfasından bulunan PDF içinde değişiklik tarihi ifadeleri",
            "Bulunamadı": "Son değişiklik tarihi bulunamadı veya PDF indirilemedi; workflow bu nedenle başarısız sayılmaz",
        },
    }


def write_outputs(
    records: list[Regulation],
    *,
    status: str = "success",
    status_message: str = "DETSİS verisi başarıyla güncellendi.",
    previous_payload: dict[str, object] | None = None,
) -> None:
    ensure_dirs()
    archive_existing_data()
    log("JSON ve CSV çıktıları yazılıyor.")
    change_report = build_change_report(records)
    now = datetime.now().isoformat(timespec="seconds")
    successful_statuses = {"success", "updated", "checked"}
    successful_check = now if status in successful_statuses else str((previous_payload or {}).get("son_basarili_veri_kontrolu") or (previous_payload or {}).get("son_kontrol_tarihi") or "")
    payload = {
        "kaynak_url": SOURCE_URL,
        "son_kontrol_tarihi": successful_check,
        "son_basarili_veri_kontrolu": successful_check,
        "son_otomatik_deneme": now,
        "son_otomatik_guncelleme": now,
        "guncelleme_durumu": status,
        "guncelleme_mesaji": status_message,
        "toplam": len(records),
        "kategori_sayilari": {category: sum(1 for item in records if item.kategori == category) for category in CATEGORIES},
        "son_degisim_raporu": change_report,
        "kayitlar": [asdict(record) for record in records],
    }
    JSON_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    CHANGE_REPORT_PATH.write_text(json.dumps(change_report, ensure_ascii=False, indent=2), encoding="utf-8")
    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "kategori",
                "mevzuat_adı",
                "tarih",
                "son_degisim_tarihi",
                "son_degisim_yontemi",
                "resmi_link",
                "kaynak_url",
            ],
        )
        writer.writeheader()
        writer.writerows(asdict(record) for record in records)


async def main() -> None:
    ensure_dirs()
    try:
        previous_payload = load_previous_payload()
        previous_records = previous_payload.get("kayitlar", [])
        log(f"Önceki kayıt sayısı: {len(previous_records)}")
        try:
            records = await scrape_with_retries()
        except Exception as scrape_exc:
            append_error("Canlı scrape başarısız; mevcut veri korunuyor", str(scrape_exc))
            if previous_records_are_valid(previous_records) and JSON_PATH.exists() and CSV_PATH.exists():
                log("UYARI: Canlı scrape başarısız oldu ancak mevcut JSON/CSV geçerli. Workflow yeşil kalacak, mevcut veri korunacak.")
                log(f"Korunan kayıt sayısı: {len(previous_records)}")
                fallback_records = previous_dicts_to_records(previous_records)
                write_outputs(
                    fallback_records,
                    status="warning",
                    status_message=f"DETSİS erişilemedi, mevcut veri korundu: {type(scrape_exc).__name__}: {scrape_exc}",
                    previous_payload=previous_payload,
                )
                return
            raise
        validate(records, previous_records)
        records = enrich_change_dates(records)
        changed = records_changed(records, previous_records)
        write_outputs(
            records,
            status="updated" if changed else "checked",
            status_message=(
                "DETSİS başarıyla kontrol edildi ve veri güncellendi."
                if changed
                else "DETSİS başarıyla kontrol edildi, değişiklik yok."
            ),
            previous_payload=previous_payload,
        )
        if not JSON_PATH.exists():
            raise RuntimeError("JSON çıktı dosyası oluşmadı.")
        log(f"Başarılı: {len(records)} kayıt yazıldı.")
    except Exception as exc:
        append_error(type(exc).__name__, str(exc))
        log(f"HATA: {type(exc).__name__}: {exc}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
