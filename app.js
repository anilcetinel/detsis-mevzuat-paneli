const state = {
  data: null,
  records: [],
  query: "",
  category: "",
};

const els = {
  lastChecked: document.querySelector("#lastChecked"),
  totalCount: document.querySelector("#totalCount"),
  categoryCards: document.querySelector("#categoryCards"),
  searchInput: document.querySelector("#searchInput"),
  categoryFilter: document.querySelector("#categoryFilter"),
  visibleCount: document.querySelector("#visibleCount"),
  sourceUrl: document.querySelector("#sourceUrl"),
  records: document.querySelector("#records"),
  emptyState: document.querySelector("#emptyState"),
};

const collator = new Intl.Collator("tr", { sensitivity: "base" });

function normalize(value) {
  return (value || "").toLocaleLowerCase("tr-TR");
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

function renderSummary() {
  const counts = state.data.kategori_sayilari || countByCategory(state.records);
  const categories = Object.keys(counts).sort((a, b) => collator.compare(a, b));
  els.totalCount.textContent = state.records.length.toLocaleString("tr-TR");
  els.lastChecked.textContent = formatDate(state.data.son_kontrol_tarihi);
  els.sourceUrl.textContent = state.data.kaynak_url || "";

  els.categoryCards.innerHTML = categories
    .map(
      (category) => `
        <article class="metric">
          <span>${category}</span>
          <strong>${Number(counts[category] || 0).toLocaleString("tr-TR")}</strong>
        </article>
      `,
    )
    .join("");

  els.categoryFilter.innerHTML = '<option value="">Tüm kategoriler</option>';
  for (const category of categories) {
    const option = document.createElement("option");
    option.value = category;
    option.textContent = category;
    els.categoryFilter.append(option);
  }
}

function filteredRecords() {
  const query = normalize(state.query);
  return state.records.filter((record) => {
    const matchesCategory = !state.category || record.kategori === state.category;
    const haystack = normalize(`${record.kategori} ${record.mevzuat_adı} ${record.tarih || ""}`);
    return matchesCategory && (!query || haystack.includes(query));
  });
}

function renderRecords() {
  const records = filteredRecords();
  els.visibleCount.textContent = `${records.length.toLocaleString("tr-TR")} kayıt`;
  els.emptyState.hidden = records.length > 0;
  els.records.innerHTML = records
    .map((record) => {
      const hasLink = Boolean(record.resmi_link);
      return `
        <article class="record">
          <div>
            <h2>${record.mevzuat_adı}</h2>
            <div class="meta">
              <span class="pill">${record.kategori}</span>
              <span class="pill">Tarih: ${record.tarih || "-"}</span>
            </div>
          </div>
          <a class="open-link ${hasLink ? "" : "disabled"}" href="${hasLink ? record.resmi_link : "#"}" target="_blank" rel="noopener">
            Resmi linki aç
          </a>
        </article>
      `;
    })
    .join("");
}

async function loadData() {
  const response = await fetch("data/mevzuatlar.json", { cache: "no-store" });
  if (!response.ok) {
    throw new Error("data/mevzuatlar.json yüklenemedi.");
  }
  state.data = await response.json();
  state.records = state.data.kayitlar || [];
  renderSummary();
  renderRecords();
}

els.searchInput.addEventListener("input", (event) => {
  state.query = event.target.value;
  renderRecords();
});

els.categoryFilter.addEventListener("change", (event) => {
  state.category = event.target.value;
  renderRecords();
});

loadData().catch((error) => {
  els.records.innerHTML = `<p class="empty">${error.message}</p>`;
});
