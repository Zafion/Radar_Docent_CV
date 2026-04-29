(function () {
  const form = document.getElementById("nd-awards-form");
  const groupInput = document.getElementById("nd-award-group");
  const queryInput = document.getElementById("nd-award-query");
  const orderInput = document.getElementById("nd-award-order");
  const metaEl = document.getElementById("nd-awards-meta");
  const bodyEl = document.getElementById("nd-awards-body");
  const ui = window.NonDocentUI;

  function parseOrder() {
    const [orderBy, orderDir] = (orderInput.value || "publication_date:desc").split(":");
    return { orderBy, orderDir };
  }

  function render(items, total) {
    metaEl.textContent = `${ui.compactNumber(total)} adjudicaciones encontradas.`;
    bodyEl.innerHTML = items.map((item) => `
      <tr>
        <td data-label="Fecha">${ui.escapeHtml(ui.formatDate(item.publication_date_iso))}</td>
        <td data-label="Persona"><strong>${ui.escapeHtml(item.person_display_name || "—")}</strong><br><span class="muted">${ui.escapeHtml(item.scope_text || "")}</span></td>
        <td data-label="Colectivo">${ui.escapeHtml(item.staff_group_name || item.staff_group_code || "—")}</td>
        <td data-label="Puesto">${ui.escapeHtml(item.position_text || "—")}</td>
        <td data-label="Bolsa">${ui.escapeHtml(item.bag_code || "—")}</td>
        <td data-label="Puntuación">${ui.escapeHtml(item.score || "—")}</td>
        <td data-label="Fuente" class="data-table__actions">${ui.sourceButton(item.document_url)}</td>
      </tr>
    `).join("") || ui.tableEmpty(7, "No hay adjudicaciones con los filtros actuales.");
  }

  function loadAwards() {
    const { orderBy, orderDir } = parseOrder();
    const params = new URLSearchParams({ limit: "100", order_by: orderBy, order_dir: orderDir });
    if (groupInput.value) params.set("staff_group_code", groupInput.value);
    if (queryInput.value.trim()) params.set("q", queryInput.value.trim());
    metaEl.textContent = "Cargando...";
    return ui.apiGet(`/api/non-docent/awards?${params.toString()}`).then((data) => render(data.items || [], data.total || 0)).catch((error) => {
      metaEl.textContent = error.message;
      bodyEl.innerHTML = ui.tableEmpty(7, "No se pudo cargar el listado.");
    });
  }

  function loadOptions() {
    return ui.apiGet("/api/non-docent/summary").then((summary) => {
      ui.setSelectOptions(groupInput, "Todos", ui.groupOptionsFromSummary(summary));
    });
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    loadAwards();
  });

  loadOptions().finally(loadAwards);
})();
