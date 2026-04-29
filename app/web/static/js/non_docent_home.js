(function () {
  function fallbackUI() {
    function escapeHtml(value) {
      return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
    }
    return {
      escapeHtml,
      formatDate(dateIso) {
        if (!dateIso) return "Sin fecha";
        const [year, month, day] = String(dateIso).split("-");
        return year && month && day ? `${day}/${month}/${year}` : dateIso;
      },
      compactNumber(value) {
        const number = Number(value || 0);
        return Number.isFinite(number) ? new Intl.NumberFormat("es-ES").format(number) : "0";
      },
      apiGet(url) {
        return fetch(url, { headers: { Accept: "application/json" } }).then(async (response) => {
          const data = await response.json().catch(() => ({}));
          if (!response.ok) {
            const detail = data?.detail || `Error ${response.status}`;
            throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
          }
          return data;
        });
      },
      sourceButton(url) {
        if (!url) return "—";
        return `<a class="button button--ghost button--xs" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">PDF</a>`;
      },
      tableEmpty(colspan, message) {
        return `<tr><td colspan="${colspan}" class="muted data-table__empty">${escapeHtml(message)}</td></tr>`;
      },
    };
  }

  function run() {
    const summaryEl = document.getElementById("non-docent-summary");
    const feedbackEl = document.getElementById("non-docent-summary-feedback");
    const groupsMetaEl = document.getElementById("non-docent-groups-meta");
    const groupsBodyEl = document.getElementById("non-docent-groups-body");
    const latestEl = document.getElementById("non-docent-latest-publications");
    const ui = window.NonDocentUI || fallbackUI();

    if (!summaryEl || !feedbackEl || !groupsMetaEl || !groupsBodyEl || !latestEl) return;

    function publicationKindLabel(kind) {
      return {
        adc_call: "Convocatoria ADC",
        adc_award: "Adjudicación ADC",
        bag_update: "Actualización de bolsa",
        funcion_publica_bag: "Bolsa Función Pública",
      }[kind] || kind || "Publicación";
    }

    function renderSummary(data) {
      const totals = data.totals || {};
      summaryEl.innerHTML = `
        <div class="status-grid__item"><span>Publicaciones</span><strong>${ui.compactNumber(totals.publications)}</strong></div>
        <div class="status-grid__item"><span>Plazas</span><strong>${ui.compactNumber(totals.offered_positions)}</strong></div>
        <div class="status-grid__item"><span>Adjudicaciones</span><strong>${ui.compactNumber(totals.awards)}</strong></div>
        <div class="status-grid__item"><span>Personas en bolsa</span><strong>${ui.compactNumber(totals.bag_members)}</strong></div>
      `;
      feedbackEl.textContent = "Datos cargados desde publicaciones oficiales procesadas.";

      groupsMetaEl.textContent = `${(data.by_group || []).length} colectivos con datos detectados.`;
      groupsBodyEl.innerHTML = (data.by_group || []).map((group) => `
        <tr>
          <td data-label="Colectivo"><strong>${ui.escapeHtml(group.staff_group_code || "—")}</strong><br><span class="muted">${ui.escapeHtml(group.staff_group_name || "—")}</span></td>
          <td data-label="Publicaciones">${ui.compactNumber(group.publications_count)}</td>
          <td data-label="Plazas">${ui.compactNumber(group.offered_positions_count)}</td>
          <td data-label="Adjudicaciones">${ui.compactNumber(group.awards_count)}</td>
          <td data-label="Bolsa">${ui.compactNumber(group.bag_members_count)}</td>
          <td data-label="Última fecha">${ui.escapeHtml(ui.formatDate(group.latest_publication_date))}</td>
        </tr>
      `).join("") || ui.tableEmpty(6, "No hay colectivos cargados.");

      latestEl.innerHTML = (data.latest_publications || []).slice(0, 8).map((item) => `
        <article class="result-item">
          <div>
            <h3>${ui.escapeHtml(item.title || "Publicación")}</h3>
            <p>${ui.escapeHtml(publicationKindLabel(item.publication_kind))} · ${ui.escapeHtml(item.staff_group_name || "Sin colectivo")} · ${ui.escapeHtml(ui.formatDate(item.publication_date_iso))}</p>
          </div>
          <div class="data-table__actions">${ui.sourceButton(item.document_url)}</div>
        </article>
      `).join("") || '<p class="muted">No hay publicaciones.</p>';
    }

    ui.apiGet("/api/non-docent/summary")
      .then(renderSummary)
      .catch((error) => {
        feedbackEl.textContent = `No se pudo cargar el resumen: ${error.message}`;
        groupsMetaEl.textContent = "No se pudo cargar el resumen.";
        groupsBodyEl.innerHTML = ui.tableEmpty(6, "No se pudo cargar el resumen.");
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", run);
  } else {
    run();
  }
})();
