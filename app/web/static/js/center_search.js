(function () {
  const PAGE_SIZE = 20;
  const form = document.getElementById("center-search-form");
  const queryInput = document.getElementById("center-query");
  const provinceInput = document.getElementById("center-province");
  const localityInput = document.getElementById("center-locality");
  const regimeInput = document.getElementById("center-regime");
  const orderInput = document.getElementById("center-order");
  const metaEl = document.getElementById("center-results-meta");
  const bodyEl = document.getElementById("center-results-body");
  const pagerEl = document.getElementById("center-results-pagination");
  const ui = window.NonDocentUI;
  let currentOffset = 0;

  function parseOrder() {
    const [orderBy, orderDir] = (orderInput.value || "denomination:asc").split(":");
    return { orderBy, orderDir };
  }

  function formatDistance(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
      return ui.hasOrigin() ? "—" : "Activa ubicación";
    }
    return `${Number(value).toFixed(2)} km`;
  }

  function actionButtons(item) {
    const buttons = [];
    if (item.center_code) {
      buttons.push(`<a class="button button--ghost button--xs" href="/centros/${encodeURIComponent(item.center_code)}">Ficha</a>`);
    }
    if (item.maps_url) {
      buttons.push(`<a class="button button--ghost button--xs" href="${ui.escapeHtml(item.maps_url)}" target="_blank" rel="noopener noreferrer">Mapa</a>`);
    }
    if (item.directions_url) {
      buttons.push(`<a class="button button--ghost button--xs" href="${ui.escapeHtml(item.directions_url)}" target="_blank" rel="noopener noreferrer">Ruta</a>`);
    }
    return buttons.join("") || "—";
  }

  function render(items, total, offset) {
    metaEl.textContent = `${ui.compactNumber(total)} centros encontrados. Mostrando ${ui.compactNumber(items.length)} por página.`;
    bodyEl.innerHTML = items.map((item) => `
      <tr>
        <td data-label="Centro"><strong>${ui.escapeHtml(item.denomination || "—")}</strong><br><span class="muted">${ui.escapeHtml(item.center_code || "")}${item.full_address ? ` · ${ui.escapeHtml(item.full_address)}` : ""}</span></td>
        <td data-label="Localidad">${ui.escapeHtml(item.locality || "—")}</td>
        <td data-label="Provincia">${ui.escapeHtml(item.province || "—")}</td>
        <td data-label="Régimen">${ui.escapeHtml(item.regime || "—")}</td>
        <td data-label="Distancia">${ui.escapeHtml(formatDistance(item.distance_km))}</td>
        <td data-label="Acciones" class="data-table__actions">${actionButtons(item)}</td>
      </tr>
    `).join("") || ui.tableEmpty(6, "No hay centros para los filtros actuales.");

    ui.renderPager(pagerEl, {
      total,
      limit: PAGE_SIZE,
      offset,
      onPage: (nextOffset) => loadCenters(nextOffset),
    });
  }

  function buildParams(offset = 0) {
    const { orderBy, orderDir } = parseOrder();
    const params = new URLSearchParams({
      limit: String(PAGE_SIZE),
      offset: String(offset),
      order_by: orderBy,
      order_dir: orderDir,
    });
    if (queryInput.value.trim()) params.set("q", queryInput.value.trim());
    if (provinceInput.value) params.set("province", provinceInput.value);
    if (localityInput.value) params.set("locality", localityInput.value);
    if (regimeInput.value) params.set("regime", regimeInput.value);
    ui.appendOriginParams(params);
    return params;
  }

  function loadCenters(offset = 0) {
    currentOffset = offset;
    const params = buildParams(offset);
    metaEl.textContent = "Cargando...";
    return ui.apiGet(`/api/centers?${params.toString()}`)
      .then((data) => render(data.items || [], data.total || 0, data.offset || 0))
      .catch((error) => {
        metaEl.textContent = error.message;
        bodyEl.innerHTML = ui.tableEmpty(6, "No se pudo cargar el listado de centros.");
        if (pagerEl) pagerEl.innerHTML = "";
      });
  }

  function loadOptions() {
    return ui.apiGet("/api/centers/options")
      .then((data) => {
        ui.setSelectOptions(provinceInput, "Todas", (data.provinces || []).map((value) => ({ value, label: value })));
        ui.setSelectOptions(localityInput, "Todas", (data.localities || []).map((value) => ({ value, label: value })));
        ui.setSelectOptions(regimeInput, "Todos", (data.regimes || []).map((value) => ({ value, label: value })));
      });
  }

  function applyInitialQuery() {
    const params = new URLSearchParams(window.location.search);
    const q = params.get("q") || params.get("query") || "";
    if (q && queryInput) queryInput.value = q;
  }

  provinceInput?.addEventListener("change", () => {
    // Evita dejar una localidad incompatible con la provincia seleccionada.
    localityInput.value = "";
  });

  form?.addEventListener("submit", (event) => {
    event.preventDefault();
    loadCenters(0);
  });

  ui.bindLocationControls?.({
    statusId: "center-location-status",
    useButtonId: "center-use-my-location",
    clearButtonId: "center-clear-location",
    feedbackId: "center-page-feedback",
    onChange: () => loadCenters(currentOffset),
  });

  applyInitialQuery();
  loadOptions().finally(() => loadCenters(0));
})();
