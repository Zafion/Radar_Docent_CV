(function () {
  const PAGE_SIZE = 20;
  const form = document.getElementById("nd-publications-form");
  const groupInput = document.getElementById("nd-publication-group");
  const kindInput = document.getElementById("nd-publication-kind");
  const queryInput = document.getElementById("nd-publication-query");
  const fromInput = document.getElementById("nd-publication-from");
  const toInput = document.getElementById("nd-publication-to");
  const orderInput = document.getElementById("nd-publication-order");
  const metaEl = document.getElementById("nd-publications-meta");
  const bodyEl = document.getElementById("nd-publications-body");
  const pagerEl = document.getElementById("nd-publications-pagination");
  const ui = window.NonDocentUI;

  function parseOrder() {
    const [orderBy, orderDir] = (orderInput.value || "publication_date:desc").split(":");
    return { orderBy, orderDir };
  }

  function render(items, total, offset) {
    metaEl.textContent = `${ui.compactNumber(total)} publicaciones encontradas. Mostrando ${ui.compactNumber(items.length)} por página.`;
    bodyEl.innerHTML = items.map((item) => `
      <tr>
        <td data-label="Fecha">${ui.escapeHtml(ui.formatDate(item.publication_date_iso))}</td>
        <td data-label="Tipo">${ui.escapeHtml(ui.publicationKindLabel(item.publication_kind))}</td>
        <td data-label="Colectivo"><strong>${ui.escapeHtml(item.staff_group_code || "—")}</strong><br><span class="muted">${ui.escapeHtml(item.staff_group_name || "")}</span></td>
        <td data-label="Título"><strong>${ui.escapeHtml(item.title || "Publicación")}</strong></td>
        <td data-label="Código">${ui.escapeHtml(item.publication_code || "—")}</td>
        <td data-label="Datos">${ui.compactNumber(item.positions_count)} plazas · ${ui.compactNumber(item.awards_count)} adjudicaciones · ${ui.compactNumber(item.bag_members_count)} bolsa</td>
        <td data-label="Fuente" class="data-table__actions">${ui.sourceButton(item.document_url)}</td>
      </tr>
    `).join("") || ui.tableEmpty(7, "No hay publicaciones con los filtros actuales.");

    ui.renderPager(pagerEl, {
      total,
      limit: PAGE_SIZE,
      offset,
      onPage: (nextOffset) => loadPublications(nextOffset),
    });
  }

  function loadPublications(offset = 0) {
    const { orderBy, orderDir } = parseOrder();
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset), order_by: orderBy, order_dir: orderDir });
    if (groupInput.value) params.set("staff_group_code", groupInput.value);
    if (kindInput.value) params.set("publication_kind", kindInput.value);
    if (queryInput.value.trim()) params.set("q", queryInput.value.trim());
    if (fromInput.value) params.set("from_date", fromInput.value);
    if (toInput.value) params.set("to_date", toInput.value);
    metaEl.textContent = "Cargando...";
    return ui.apiGet(`/api/non-docent/publications?${params.toString()}`).then((data) => render(data.items || [], data.total || 0, data.offset || 0)).catch((error) => {
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
    loadPublications(0);
  });

  loadOptions().finally(() => loadPublications(0));
})();
