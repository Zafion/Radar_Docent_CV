(function () {
  const form = document.getElementById("nd-positions-form");
  const groupInput = document.getElementById("nd-position-group");
  const provinceInput = document.getElementById("nd-position-province");
  const queryInput = document.getElementById("nd-position-query");
  const orderInput = document.getElementById("nd-position-order");
  const metaEl = document.getElementById("nd-positions-meta");
  const bodyEl = document.getElementById("nd-positions-body");
  const ui = window.NonDocentUI;

  function parseOrder() {
    const [orderBy, orderDir] = (orderInput.value || "publication_date:desc").split(":");
    return { orderBy, orderDir };
  }

  function render(items, total) {
    metaEl.textContent = `${ui.compactNumber(total)} plazas encontradas.`;
    bodyEl.innerHTML = items.map((item) => `
      <tr>
        <td data-label="Colectivo"><strong>${ui.escapeHtml(item.staff_group_code || "—")}</strong><br><span class="muted">${ui.escapeHtml(item.staff_group_name || "")}</span></td>
        <td data-label="Puesto"><strong>${ui.escapeHtml(item.denomination || "—")}</strong><br><span class="muted">${ui.escapeHtml(item.reason || "")}</span></td>
        <td data-label="Centro">${ui.escapeHtml(item.center_name || item.functional_assignment || "—")}</td>
        <td data-label="Localidad">${ui.escapeHtml(item.locality || "—")}</td>
        <td data-label="Provincia">${ui.escapeHtml(item.province || "—")}</td>
        <td data-label="Código">${ui.escapeHtml(item.position_code || "—")}</td>
        <td data-label="Acciones" class="data-table__actions">${ui.sourceButton(item.document_url)}</td>
      </tr>
    `).join("") || ui.tableEmpty(7, "No hay plazas con los filtros actuales.");
  }

  function loadPositions() {
    const { orderBy, orderDir } = parseOrder();
    const params = new URLSearchParams({ limit: "100", order_by: orderBy, order_dir: orderDir });
    if (groupInput.value) params.set("staff_group_code", groupInput.value);
    if (provinceInput.value) params.set("province", provinceInput.value);
    if (queryInput.value.trim()) params.set("q", queryInput.value.trim());
    metaEl.textContent = "Cargando...";
    return ui.apiGet(`/api/non-docent/positions?${params.toString()}`).then((data) => render(data.items || [], data.total || 0)).catch((error) => {
      metaEl.textContent = error.message;
      bodyEl.innerHTML = ui.tableEmpty(7, "No se pudo cargar el listado.");
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
    loadPositions();
  });

  loadOptions().finally(loadPositions);
})();
