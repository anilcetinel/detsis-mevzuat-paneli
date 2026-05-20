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

const categoryOrder = ["Kurum Yönetmeliği", "Esas ve Usuller", "Yönerge", "İlke Kararı"];
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

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("tr-TR", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
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
  els.lastChecked.textContent = formatDate(state.data.son_kontrol_tarihi);
  els.sourceUrl.textContent = state.data.kaynak_url || "";

  const cards = document.createDocumentFragment();
  for (const category of categories) {
    const card = document.createElement("article");
    card.className = "metric";
    card.innerHTML = `
      <span>${escapeHtml(category)}</span>
      <strong>${Number(counts[category] || 0).toLocaleString("tr-TR")}</strong>
    `;
    cards.append(card);
  }
  els.categoryCards.replaceChildren(cards);

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
    .map((record) => ({ ...record, parsedDate: parseDate(record.tarih) }))
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
      <span>${escapeHtml(record.kategori)} · ${escapeHtml(record.tarih)}</span>
    `;
    fragment.append(link);
  }
  els.latestRecords.replaceChildren(fragment);
}

function filteredRecords() {
  const query = normalize(state.query);
  return state.records.filter((record) => {
    const matchesCategory = !state.category || record.kategori === state.category;
    const haystack = normalize(`${record.kategori} ${record.mevzuat_adı} ${record.tarih || ""}`);
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
          <span class="pill">${escapeHtml(record.kategori)}</span>
          <span class="pill">Tarih: ${escapeHtml(record.tarih || "-")}</span>
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

async function loadData() {
  try {
    const response = await fetch("./data/mevzuatlar.json", { cache: "no-store" });
    if (!response.ok) throw new Error("data/mevzuatlar.json yüklenemedi.");
    state.data = await response.json();
    state.records = state.data.kayitlar || [];
    renderSummary();
    renderLatest();
    renderRecords();
    els.body.classList.add("is-loaded");
  } catch (error) {
    els.body.classList.add("is-loaded");
    els.errorState.hidden = false;
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
