(function () {
  const titleEl = document.getElementById("nd-person-title");
  const subtitleEl = document.getElementById("nd-person-subtitle");
  const summaryEl = document.getElementById("nd-person-summary");
  const awardsMetaEl = document.getElementById("nd-person-awards-meta");
  const awardsBodyEl = document.getElementById("nd-person-awards-body");
  const bagMetaEl = document.getElementById("nd-person-bag-meta");
  const bagBodyEl = document.getElementById("nd-person-bag-body");
  const ui = window.NonDocentUI;

  function loadSelectedPerson() {
    try {
      const raw = sessionStorage.getItem("radar_non_docent_selected_person");
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      return typeof parsed.normalizedName === "string" ? parsed : null;
    } catch {
      return null;
    }
  }

  function renderSummary(profile) {
    const view = profile.user_view || {};
    titleEl.textContent = view.display_name || profile.person?.display_name || "Resultado por persona";
    subtitleEl.textContent = `${ui.compactNumber(profile.summary?.total_awards)} adjudicaciones · ${ui.compactNumber(profile.summary?.total_bag_records)} registros de bolsa`;
    summaryEl.innerHTML = `
      <div class="status-card">
        <span class="status-pill status-pill--info">${ui.escapeHtml(view.current_result_label || "Estado")}</span>
        <div>
          <h3>${ui.escapeHtml(view.display_name || profile.person?.display_name || "—")}</h3>
          <p>${ui.escapeHtml(view.current_result_message || "")}</p>
        </div>
        <div class="status-grid">
          <div class="status-grid__item"><span>Última fecha</span><strong>${ui.escapeHtml(ui.formatDate(view.latest_date))}</strong></div>
          <div class="status-grid__item"><span>Colectivo</span><strong>${ui.escapeHtml(view.latest_staff_group_label || "—")}</strong></div>
          <div class="status-grid__item"><span>Estado bolsa</span><strong>${ui.escapeHtml(view.latest_bag_status || "—")}</strong></div>
          <div class="status-grid__item"><span>Último puesto</span><strong>${ui.escapeHtml(view.latest_awarded_position || "—")}</strong></div>
        </div>
        <p class="panel-card__hint">${ui.escapeHtml(view.recommended_action || "Contrasta siempre con la fuente oficial.")}</p>
      </div>
    `;
  }

  function renderAwards(awards) {
    awardsMetaEl.textContent = `${ui.compactNumber(awards.length)} adjudicaciones.`;
    awardsBodyEl.innerHTML = awards.map((item) => `
      <tr>
        <td data-label="Fecha">${ui.escapeHtml(ui.formatDate(item.publication_date_iso))}</td>
        <td data-label="Colectivo">${ui.escapeHtml(item.staff_group_name || item.staff_group_code || "—")}</td>
        <td data-label="Puesto">${ui.escapeHtml(item.position_text || "—")}</td>
        <td data-label="Bolsa">${ui.escapeHtml(item.bag_code || "—")}</td>
        <td data-label="Puntuación">${ui.escapeHtml(item.score || "—")}</td>
        <td data-label="Fuente" class="data-table__actions">${ui.sourceButton(item.document_url)}</td>
      </tr>
    `).join("") || ui.tableEmpty(6, "No hay adjudicaciones para esta persona.");
  }

  function renderBag(records) {
    bagMetaEl.textContent = `${ui.compactNumber(records.length)} registros mostrados.`;
    bagBodyEl.innerHTML = records.map((item) => `
      <tr>
        <td data-label="Fecha">${ui.escapeHtml(ui.formatDate(item.snapshot_date_iso))}</td>
        <td data-label="Bolsa"><strong>${ui.escapeHtml(item.bag_code || "—")}</strong><br><span class="muted">${ui.escapeHtml(item.staff_group_name || "")}</span></td>
        <td data-label="Zona">${ui.escapeHtml(item.zone_text || "—")}</td>
        <td data-label="Orden">${ui.escapeHtml(item.order_number || "—")}</td>
        <td data-label="Puntuación">${ui.escapeHtml(item.total_score || "—")}</td>
        <td data-label="Estado">${ui.escapeHtml(item.status_text || item.annotation_text || "—")}</td>
        <td data-label="Fuente" class="data-table__actions">${ui.sourceButton(item.document_url)}</td>
      </tr>
    `).join("") || ui.tableEmpty(7, "No hay registros de bolsa para esta persona.");
  }

  const selected = loadSelectedPerson();
  if (!selected?.normalizedName) {
    titleEl.textContent = "No hay persona seleccionada";
    subtitleEl.textContent = "Vuelve a la búsqueda y selecciona una coincidencia.";
    summaryEl.innerHTML = '<a class="button button--secondary" href="/no-docente/consulta-persona">Ir a búsqueda</a>';
    awardsBodyEl.innerHTML = ui.tableEmpty(6, "Sin persona seleccionada.");
    bagBodyEl.innerHTML = ui.tableEmpty(7, "Sin persona seleccionada.");
    return;
  }

  ui.apiGet(`/api/non-docent/persons/profile?normalized_name=${encodeURIComponent(selected.normalizedName)}`)
    .then((profile) => {
      renderSummary(profile);
      renderAwards(profile.awards || []);
      renderBag(profile.bag_records || []);
    })
    .catch((error) => {
      titleEl.textContent = "No se pudo cargar la ficha";
      subtitleEl.textContent = error.message;
      awardsBodyEl.innerHTML = ui.tableEmpty(6, "No se pudo cargar la ficha.");
      bagBodyEl.innerHTML = ui.tableEmpty(7, "No se pudo cargar la ficha.");
    });
})();
