(function () {
  const PAGE_SIZE = 20;
  const form = document.getElementById("nd-awards-form");
  const groupInput = document.getElementById("nd-award-group");
  const queryInput = document.getElementById("nd-award-query");
  const orderInput = document.getElementById("nd-award-order");
  const metaEl = document.getElementById("nd-awards-meta");
  const bodyEl = document.getElementById("nd-awards-body");
  const pagerEl = document.getElementById("nd-awards-pagination");
  const ui = window.NonDocentUI;

  function parseOrder() {
    const [orderBy, orderDir] = (orderInput.value || "publication_date:desc").split(":");
    return { orderBy, orderDir };
  }

  function saveSelectedPerson(item) {
    sessionStorage.setItem("radar_non_docent_selected_person", JSON.stringify({
      normalizedName: item.person_name_normalized,
      displayName: item.person_display_name,
      selectedAt: new Date().toISOString(),
    }));
  }

  function attachPersonButtons(items) {
    bodyEl.querySelectorAll("button[data-person-index]").forEach((button) => {
      button.addEventListener("click", () => {
        const item = items[Number(button.dataset.personIndex)];
        if (!item?.person_name_normalized) return;
        saveSelectedPerson(item);
        window.location.href = "/no-docente/resultado-persona";
      });
    });
  }

  function render(items, total, offset) {
    metaEl.textContent = `${ui.compactNumber(total)} adjudicaciones encontradas. Mostrando ${ui.compactNumber(items.length)} por página.`;
    bodyEl.innerHTML = items.map((item, index) => `
      <tr>
        <td data-label="Fecha">${ui.escapeHtml(ui.formatDate(item.publication_date_iso))}</td>
        <td data-label="Persona"><strong>${ui.escapeHtml(item.person_display_name || "—")}</strong><br><span class="muted">${ui.escapeHtml(item.scope_text || "")}</span></td>
        <td data-label="Colectivo">${ui.escapeHtml(item.staff_group_name || item.staff_group_code || "—")}</td>
        <td data-label="Puesto">${ui.escapeHtml(item.position_text || "—")}</td>
        <td data-label="Bolsa">${ui.escapeHtml(item.bag_code || "—")}</td>
        <td data-label="Puntuación">${ui.escapeHtml(item.score || "—")}</td>
        <td data-label="Acciones" class="data-table__actions"><button class="button button--secondary button--xs" type="button" data-person-index="${index}">Ver ficha</button></td>
      </tr>
    `).join("") || ui.tableEmpty(7, "No hay adjudicaciones con los filtros actuales.");

    attachPersonButtons(items);
    ui.renderPager(pagerEl, {
      total,
      limit: PAGE_SIZE,
      offset,
      onPage: (nextOffset) => loadAwards(nextOffset),
    });
  }

  function loadAwards(offset = 0) {
    const { orderBy, orderDir } = parseOrder();
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset), order_by: orderBy, order_dir: orderDir });
    if (groupInput.value) params.set("staff_group_code", groupInput.value);
    if (queryInput.value.trim()) params.set("q", queryInput.value.trim());
    metaEl.textContent = "Cargando...";
    return ui.apiGet(`/api/non-docent/awards?${params.toString()}`).then((data) => render(data.items || [], data.total || 0, data.offset || 0)).catch((error) => {
      metaEl.textContent = error.message;
      bodyEl.innerHTML = ui.tableEmpty(7, "No se pudo cargar el listado.");
      if (pagerEl) pagerEl.innerHTML = "";
    });
  }

  function loadOptions() {
    return ui.apiGet("/api/non-docent/summary").then((summary) => {
      ui.setSelectOptions(groupInput, "Todos", ui.groupOptionsFromSummary(summary));
    });
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    loadAwards(0);
  });

  loadOptions().finally(() => loadAwards(0));
})();
