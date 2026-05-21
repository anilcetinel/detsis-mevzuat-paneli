const state = {
  data: null,
  records: [],
  query: "",
  category: "",
  renderToken: 0,
};

const els = {
  body: document.body,
  lastChecked: document.querySelector("#lastChecked"),
  lastDataCheck: document.querySelector("#lastDataCheck"),
  lastAutoUpdate: document.querySelector("#lastAutoUpdate"),
  updateStatusText: document.querySelector("#updateStatusText"),
  totalCount: document.querySelector("#totalCount"),
  categoryCards: document.querySelector("#categoryCards"),
  latestRecords: document.querySelector("#latestRecords"),
  searchInput: document.querySelector("#searchInput"),
  categoryFilter: document.querySelector("#categoryFilter"),
  visibleCount: document.querySelector("#visibleCount"),
  sourceUrl: document.querySelector("#sourceUrl"),
  records: document.querySelector("#records"),
  emptyState: document.querySelector("#emptyState"),
  errorState: document.querySelector("#errorState"),
  themeToggle: document.querySelector("#themeToggle"),
};

const categoryOrder = ["Kurum Yönetmeliği", "Yönerge", "Esas ve Usuller", "Uygulama / Program Esasları", "İlke Kararı"];
const categoryLabels = {
  "Kurum Yönetmeliği": "Yönetmelik",
  "Esas ve Usuller": "Esas ve Usuller",
  "Yönerge": "Yönerge",
  "Uygulama / Program Esasları": "Uygulama / Program Esasları",
  "İlke Kararı": "İlke Kararları",
};
const collator = new Intl.Collator("tr", { sensitivity: "base" });
const prefersDark = window.matchMedia("(prefers-color-scheme: dark)");

function normalize(value) {
  return (value || "").toLocaleLowerCase("tr-TR");
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function parseDate(value) {
  if (!value || value === "-") return null;
  const match = value.match(/^(\d{1,2})[./-](\d{1,2})[./-](\d{4})$/);
  if (!match) return null;
  const [, day, month, year] = match;
  const date = new Date(Number(year), Number(month) - 1, Number(day));
  return Number.isNaN(date.getTime()) ? null : date;
}

function displayDate(value) {
  return value && value !== "" ? value : "-";
}

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("tr-TR", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function setText(element, value) {
  if (element) element.textContent = value;
}

function countByCategory(records) {
  return records.reduce((acc, record) => {
    acc[record.kategori] = (acc[record.kategori] || 0) + 1;
    return acc;
  }, {});
}

function debounce(callback, delay = 160) {
  let timer;
  return (...args) => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => callback(...args), delay);
  };
}

function animateCount(element, target) {
  const duration = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 850;
  const start = performance.now();
  const formatter = new Intl.NumberFormat("tr-TR");

  function tick(now) {
    const progress = duration ? Math.min((now - start) / duration, 1) : 1;
    const eased = 1 - Math.pow(1 - progress, 3);
    element.textContent = formatter.format(Math.round(target * eased));
    if (progress < 1) requestAnimationFrame(tick);
  }

  requestAnimationFrame(tick);
}

function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("theme", theme);
  const isDark = theme === "dark";
  els.themeToggle.setAttribute("aria-pressed", String(isDark));
  els.themeToggle.setAttribute("aria-label", isDark ? "Aydınlık modu aç" : "Karanlık modu aç");
  els.themeToggle.querySelector(".theme-label").textContent = isDark ? "Aydınlık mod" : "Karanlık mod";
}

function initTheme() {
  const saved = localStorage.getItem("theme");
  setTheme(saved || (prefersDark.matches ? "dark" : "light"));
}

function renderSummary() {
  const counts = state.data.kategori_sayilari || countByCategory(state.records);
  const categories = Object.keys(counts).sort((a, b) => {
    const ai = categoryOrder.indexOf(a);
    const bi = categoryOrder.indexOf(b);
    if (ai !== -1 || bi !== -1) return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
    return collator.compare(a, b);
  });

  animateCount(els.totalCount, state.records.length);
  const lastCheck = formatDate(state.data.son_basarili_veri_kontrolu || state.data.son_kontrol_tarihi);
  const lastAttempt = formatDate(state.data.son_otomatik_deneme || state.data.son_otomatik_guncelleme || state.data.son_kontrol_tarihi);
  const status = state.data.guncelleme_durumu || "success";
  const isWarning = status === "warning";
  const statusLabel = status === "checked" ? "Kontrol edildi, değişiklik yok" : status === "updated" || status === "success" ? "Güncellendi" : "DETSİS erişilemedi";
  setText(els.lastChecked, statusLabel);
  setText(els.lastDataCheck, lastCheck);
  setText(els.lastAutoUpdate, lastAttempt);
  setText(els.sourceUrl, state.data.kaynak_url || "");

  const cards = document.createDocumentFragment();
  for (const category of categories) {
    const card = document.createElement("article");
    card.className = "metric";
    card.innerHTML = `
      <span>${escapeHtml(categoryLabels[category] || category)}</span>
      <strong>${Number(counts[category] || 0).toLocaleString("tr-TR")}</strong>
    `;
    cards.append(card);
  }
  els.categoryCards.replaceChildren(cards);

  setText(
    els.updateStatusText,
    isWarning
      ? "Son otomatik kontrolde DETSİS erişilemedi; mevcut geçerli veri korunuyor."
      : state.data.guncelleme_mesaji || "Son otomatik kontrolde DETSİS başarıyla kontrol edildi.",
  );

  els.categoryFilter.innerHTML = '<option value="">Tüm kategoriler</option>';
  for (const category of categories) {
    const option = document.createElement("option");
    option.value = category;
    option.textContent = category;
    els.categoryFilter.append(option);
  }
}

function renderLatest() {
  const latest = state.records
    .map((record) => ({ ...record, parsedDate: parseDate(record.son_degisim_tarihi) || parseDate(record.tarih) }))
    .filter((record) => record.parsedDate)
    .sort((a, b) => b.parsedDate - a.parsedDate)
    .slice(0, 5);

  const fragment = document.createDocumentFragment();
  for (const record of latest) {
    const link = document.createElement("a");
    link.className = "latest-card";
    link.href = record.resmi_link || "#";
    link.target = "_blank";
    link.rel = "noopener";
    link.innerHTML = `
      <strong>${escapeHtml(record.mevzuat_adı)}</strong>
      <span>${escapeHtml(record.kategori)} · Yürürlük: ${escapeHtml(displayDate(record.tarih))}</span>
    `;
    fragment.append(link);
  }
  els.latestRecords.replaceChildren(fragment);
}

function filteredRecords() {
  const query = normalize(state.query);
  return state.records.filter((record) => {
    const matchesCategory = !state.category || record.kategori === state.category;
    const haystack = normalize(`${record.kategori} ${record.mevzuat_adı} ${record.tarih || ""} ${record.son_degisim_tarihi || ""}`);
    return matchesCategory && (!query || haystack.includes(query));
  });
}

function recordTemplate(record) {
  const hasLink = Boolean(record.resmi_link);
  return `
    <article class="record">
      <div>
        <h3>${escapeHtml(record.mevzuat_adı)}</h3>
        <div class="meta">
          <span class="pill pill-category">${escapeHtml(record.kategori)}</span>
          <span class="pill pill-date">Yürürlük: ${escapeHtml(displayDate(record.tarih))}</span>
          <span class="pill pill-change">Son değişiklik: ${escapeHtml(displayDate(record.son_degisim_tarihi))}</span>
        </div>
      </div>
      <a class="open-link ${hasLink ? "" : "disabled"}" href="${hasLink ? escapeHtml(record.resmi_link) : "#"}" target="_blank" rel="noopener" aria-label="${escapeHtml(record.mevzuat_adı)} resmi kaydını yeni sekmede aç">
        Resmi linki aç
      </a>
    </article>
  `;
}

function renderRecords() {
  const token = ++state.renderToken;
  const records = filteredRecords();
  const formatter = new Intl.NumberFormat("tr-TR");
  els.visibleCount.textContent = `${formatter.format(records.length)} kayıt`;
  els.emptyState.hidden = records.length > 0;
  els.records.replaceChildren();

  const chunkSize = 60;
  let index = 0;

  function renderChunk() {
    if (token !== state.renderToken) return;
    const slice = records.slice(index, index + chunkSize);
    if (!slice.length) return;
    els.records.insertAdjacentHTML("beforeend", slice.map(recordTemplate).join(""));
    index += chunkSize;
    if (index < records.length) requestAnimationFrame(renderChunk);
  }

  renderChunk();
}

function alignHashTarget() {
  if (window.location.hash !== "#records") return;
  window.setTimeout(() => {
    els.records?.scrollIntoView({ block: "start" });
  }, 150);
}

async function loadData() {
  try {
    const dataUrl = new URL("./data/mevzuatlar.json", window.location.href);
    dataUrl.searchParams.set("v", Date.now().toString());
    const response = await fetch(dataUrl.toString(), {
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      throw new Error(`JSON fetch başarısız: ${response.status} ${response.statusText} (${dataUrl.toString()})`);
    }
    state.data = await response.json();
    state.records = state.data.kayitlar || [];
    if (!Array.isArray(state.records) || state.records.length === 0) {
      throw new Error(`JSON okundu ancak kayıt listesi boş veya geçersiz: ${dataUrl.toString()}`);
    }
    renderSummary();
    renderLatest();
    renderRecords();
    els.body.classList.add("is-loaded");
    alignHashTarget();
  } catch (error) {
    els.body.classList.add("is-loaded");
    els.errorState.hidden = false;
    els.errorState.textContent = `Veriler yüklenemedi. Teknik ayrıntı: ${error.message}`;
    els.totalCount.textContent = "!";
    els.visibleCount.textContent = "Veri yok";
    els.records.replaceChildren();
    console.error(error);
  }
}

initTheme();

els.themeToggle.addEventListener("click", () => {
  const current = document.documentElement.dataset.theme === "dark" ? "dark" : "light";
  setTheme(current === "dark" ? "light" : "dark");
});

els.searchInput.addEventListener(
  "input",
  debounce((event) => {
    state.query = event.target.value;
    renderRecords();
  }),
);

els.categoryFilter.addEventListener("change", (event) => {
  state.category = event.target.value;
  renderRecords();
});

loadData();
