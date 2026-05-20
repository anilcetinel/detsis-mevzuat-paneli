from __future__ import annotations

import asyncio
import csv
import json
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError, async_playwright


SOURCE_URL = "https://detsis.gov.tr/birim/35955870/35955870/2026-05-20"
CATEGORIES = ["Kurum Yönetmeliği", "Esas ve Usuller", "Yönerge", "İlke Kararı"]

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
ARCHIVE_DIR = DATA_DIR / "archive"
LOG_DIR = ROOT / "logs"
JSON_PATH = DATA_DIR / "mevzuatlar.json"
CSV_PATH = DATA_DIR / "mevzuatlar.csv"
ERROR_LOG_PATH = LOG_DIR / "hata_log.csv"
NEW_RECORDS_LOG_PATH = LOG_DIR / "new_records.log"


@dataclass(frozen=True)
class Regulation:
    kategori: str
    mevzuat_adı: str
    tarih: str
    resmi_link: str
    kaynak_url: str


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


def append_new_records(records: list["Regulation"]) -> None:
    if not records:
        return
    ensure_dirs()
    with NEW_RECORDS_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"\n{datetime.now().isoformat(timespec='seconds')} - Yeni kayıtlar\n")
        for record in records:
            handle.write(f"- [{record.kategori}] {record.mevzuat_adı} | {record.resmi_link}\n")


def load_previous_records() -> list[dict[str, str]]:
    if not JSON_PATH.exists():
        return []
    try:
        payload = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        append_error("Önceki JSON okunamadı", str(exc))
        return []
    return payload.get("kayitlar", [])


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


async def open_mevzuat_tab(page: Page) -> None:
    opened = await click_text(page, "Mevzuat", timeout=12_000)
    if not opened:
        raise RuntimeError("Mevzuat sekmesi bulunamadı veya açılamadı.")
    try:
        await page.get_by_text(re.compile(r"Toplam\s+\d+\s+adet\s+mevzuat", re.I)).wait_for(timeout=12_000)
    except PlaywrightTimeoutError as exc:
        body = clean_text((await page.locator("body").inner_text())[:1_500])
        raise RuntimeError(f"Mevzuat sekmesi açıldı ancak kayıt özeti görünmedi. Sayfa metni: {body}") from exc


async def expand_category(page: Page, category: str) -> None:
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


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip(" -–—\t\r\n")


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
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(locale="tr-TR", viewport={"width": 1440, "height": 1200})
        try:
            await page.goto(SOURCE_URL, wait_until="domcontentloaded", timeout=60_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=20_000)
            except PlaywrightTimeoutError:
                pass
            await accept_cookie_notice(page)
            await open_mevzuat_tab(page)

            all_records: list[Regulation] = []
            for category in CATEGORIES:
                await expand_category(page, category)
                await page.wait_for_timeout(750)
                category_records = await extract_from_dom(page, category)
                all_records.extend(category_records)
            return dedupe(all_records)
        finally:
            await browser.close()


def validate(records: list[Regulation], previous_records: list[dict[str, str]]) -> None:
    errors: list[str] = []

    if not records:
        errors.append("Toplam kayıt 0 geldi")

    missing_categories = [record.mevzuat_adı for record in records if not record.kategori]
    if missing_categories:
        errors.append(f"Kategori boş kayıt sayısı: {len(missing_categories)}")

    missing_links = [record.mevzuat_adı for record in records if not record.resmi_link]
    if missing_links:
        errors.append(f"Link eksik kayıt sayısı: {len(missing_links)}")

    if errors:
        message = "Mevzuat veri bütünlüğü kontrolü başarısız."
        append_error(message, " | ".join(errors))
        raise RuntimeError(f"{message} {' | '.join(errors)}")

    previous_count = len(previous_records)
    if previous_count and previous_count != len(records):
        append_error("Yeni mevzuat sayısı algılandı", f"Yeni mevzuat sayısı algılandı: {len(records)}")

    previous_links = {record.get("resmi_link", "") for record in previous_records}
    new_records = [record for record in records if record.resmi_link and record.resmi_link not in previous_links]
    append_new_records(new_records)


def write_outputs(records: list[Regulation]) -> None:
    ensure_dirs()
    archive_existing_data()
    payload = {
        "kaynak_url": SOURCE_URL,
        "son_kontrol_tarihi": datetime.now().isoformat(timespec="seconds"),
        "toplam": len(records),
        "kategori_sayilari": {category: sum(1 for item in records if item.kategori == category) for category in CATEGORIES},
        "kayitlar": [asdict(record) for record in records],
    }
    JSON_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["kategori", "mevzuat_adı", "tarih", "resmi_link", "kaynak_url"])
        writer.writeheader()
        writer.writerows(asdict(record) for record in records)


async def main() -> None:
    ensure_dirs()
    try:
        previous_records = load_previous_records()
        records = await scrape()
        validate(records, previous_records)
        write_outputs(records)
        if not JSON_PATH.exists():
            raise RuntimeError("JSON çıktı dosyası oluşmadı.")
        print(f"Başarılı: {len(records)} kayıt yazıldı.")
    except Exception as exc:
        append_error(type(exc).__name__, str(exc))
        raise


if __name__ == "__main__":
    asyncio.run(main())
