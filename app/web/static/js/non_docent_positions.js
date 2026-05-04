(function () {
  const PAGE_SIZE = 20;
  const form = document.getElementById("nd-positions-form");
  const groupInput = document.getElementById("nd-position-group");
  const provinceInput = document.getElementById("nd-position-province");
  const queryInput = document.getElementById("nd-position-query");
  const orderInput = document.getElementById("nd-position-order");
  const metaEl = document.getElementById("nd-positions-meta");
  const bodyEl = document.getElementById("nd-positions-body");
  const pagerEl = document.getElementById("nd-positions-pagination");
  const ui = window.NonDocentUI;
  let currentOffset = 0;

  function parseOrder() {
    const [orderBy, orderDir] = (orderInput.value || "publication_date:desc").split(":");
    return { orderBy, orderDir };
  }

  function formatDistance(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
      return ui.hasOrigin() ? "—" : "Activa ubicación";
    }
    return `${Number(value).toFixed(2)} km`;
  }

  function actions(item) {
    const buttons = [];
    const centerSearchText = item.center_name || item.functional_assignment || item.locality || "";
    if (centerSearchText) {
      buttons.push(`<a class="button button--ghost button--xs" href="/centros?q=${encodeURIComponent(centerSearchText)}">Buscar centro</a>`);
    }
    if (item.document_url) {
      buttons.push(ui.sourceButton(item.document_url));
    }
    return buttons.join("") || "—";
  }

  function render(items, total, offset) {
    metaEl.textContent = `${ui.compactNumber(total)} plazas disponibles encontradas. Mostrando ${ui.compactNumber(items.length)} por página.`;
    bodyEl.innerHTML = items.map((item) => `
      <tr>
        <td data-label="Colectivo"><strong>${ui.escapeHtml(item.staff_group_code || "—")}</strong><br><span class="muted">${ui.escapeHtml(item.staff_group_name || "")}</span></td>
        <td data-label="Puesto"><strong>${ui.escapeHtml(item.denomination || "—")}</strong><br><span class="muted">${ui.escapeHtml(item.reason || "")}</span></td>
        <td data-label="Centro"><strong>${ui.escapeHtml(item.center_name || item.functional_assignment || "—")}</strong>${item.center_full_address ? `<br><span class="muted">${ui.escapeHtml(item.center_full_address)}</span>` : ""}</td>
        <td data-label="Localidad">${ui.escapeHtml(item.locality || "—")}</td>
        <td data-label="Provincia">${ui.escapeHtml(item.province || "—")}</td>
        <td data-label="Código">${ui.escapeHtml(item.position_code || "—")}</td>
        <td data-label="Acciones" class="data-table__actions">${actions(item)}</td>
      </tr>
    `).join("") || ui.tableEmpty(7, "No hay plazas no docentes disponibles actualmente para los filtros seleccionados.");

    ui.renderPager(pagerEl, {
      total,
      limit: PAGE_SIZE,
      offset,
      onPage: (nextOffset) => loadPositions(nextOffset),
    });
  }

  function loadPositions(offset = 0) {
    currentOffset = offset;
    const { orderBy, orderDir } = parseOrder();
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset), order_by: orderBy, order_dir: orderDir });
    if (groupInput.value) params.set("staff_group_code", groupInput.value);
    if (provinceInput.value) params.set("province", provinceInput.value);
    if (queryInput.value.trim()) params.set("q", queryInput.value.trim());
    metaEl.textContent = "Cargando...";
    return ui.apiGet(`/api/non-docent/positions?${params.toString()}`).then((data) => render(data.items || [], data.total || 0, data.offset || 0)).catch((error) => {
      metaEl.textContent = error.message;
      bodyEl.innerHTML = ui.tableEmpty(7, "No se pudo cargar el listado.");
      if (pagerEl) pagerEl.innerHTML = "";
    });
  }

  function loadOptions() {
    return ui.apiGet("/api/non-docent/summary").then((summary) => {
      ui.setSelectOptions(groupInput, "Todos", ui.groupOptionsFromSummary(summary));
    }).then(() => ui.apiGet("/api/non-docent/positions?limit=200&order_by=publication_date&order_dir=desc"))
      .then((data) => {
        const provinces = Array.from(new Set((data.items || []).map((item) => item.province).filter(Boolean))).sort();
        ui.setSelectOptions(provinceInput, "Todas", provinces.map((value) => ({ value, label: value })));
      });
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    loadPositions(0);
  });


  loadOptions().finally(() => loadPositions(0));
})();
