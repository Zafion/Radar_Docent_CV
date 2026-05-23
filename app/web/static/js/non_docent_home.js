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
      tableEmpty(colspan, message) {
        return `<tr><td colspan="${colspan}" class="muted data-table__empty">${escapeHtml(message)}</td></tr>`;
      },
      bindLocationControls() {},
      bindPushToggle() {},
    };
  }

  function run() {
    const summaryEl = document.getElementById("non-docent-summary");
    const feedbackEl = document.getElementById("non-docent-summary-feedback");
    const groupsMetaEl = document.getElementById("non-docent-groups-meta");
    const groupsBodyEl = document.getElementById("non-docent-groups-body");
    const latestPublicationsEl = document.getElementById("non-docent-latest-publications");
    const ui = window.NonDocentUI || fallbackUI();
    const i18n = window.FunkI18n;
    const t = (value) => (i18n && typeof i18n.t === "function" ? i18n.t(value) : value);

    ui.bindLocationControls?.({
      statusId: "nd-location-status",
      useButtonId: "nd-use-my-location",
      clearButtonId: "nd-clear-location",
      feedbackId: "nd-page-feedback",
    });
    ui.bindPushToggle?.({ buttonId: "nd-push-toggle-button", feedbackId: "nd-page-feedback" });

    if (!summaryEl || !feedbackEl || !groupsMetaEl || !groupsBodyEl) return;

    function renderLatestPublications(data) {
      if (!latestPublicationsEl) return;

      const latest = (data.latest_publications || []).slice(0, 8);
      if (!latest.length) {
        latestPublicationsEl.innerHTML = '<p class="muted">No hay publicaciones no docentes detectadas todavía.</p>';
        return;
      }

      latestPublicationsEl.innerHTML = latest.map((item) => {
        const title = item.title || item.publication_title || "Publicación no docente";
        const group = item.staff_group_name || item.staff_group_code || "Personal no docente";
        const date = item.publication_date_iso || item.document_date_iso || "";
        const kind = item.publication_kind || item.asset_role || "";
        const pdfButton = item.document_url
          ? `<a class="button button--ghost button--xs" href="${ui.escapeHtml(item.document_url)}" target="_blank" rel="noopener noreferrer">PDF oficial</a>`
          : "";

        return `
          <article class="result-item">
            <div>
              <h3>${ui.escapeHtml(title)}</h3>
              <p>${ui.escapeHtml(group)} · ${ui.escapeHtml(ui.formatDate(date))}${kind ? " · " + ui.escapeHtml(kind) : ""}</p>
            </div>
            <div class="data-table__actions">
              ${pdfButton || '<span class="muted">Sin PDF</span>'}
            </div>
          </article>
        `;
      }).join("");
    }

    function renderSummary(data) {
      const totals = data.totals || {};
      summaryEl.innerHTML = `
        <div class="status-grid__item"><span>Publicaciones</span><strong>${ui.compactNumber(totals.publications)}</strong></div>
        <div class="status-grid__item"><span>Plazas disponibles</span><strong>${ui.compactNumber(totals.available_positions || 0)}</strong></div>
        <div class="status-grid__item"><span>Adjudicaciones</span><strong>${ui.compactNumber(totals.awards)}</strong></div>
        <div class="status-grid__item"><span>Personas en bolsa</span><strong>${ui.compactNumber(totals.bag_members)}</strong></div>
      `;
      feedbackEl.textContent = `Datos cargados desde publicaciones oficiales procesadas. Las plazas adjudicadas o ya no visibles no se muestran como disponibles. ${ui.compactNumber(totals.offered_positions)} plazas detectadas en total.`;
      renderLatestPublications(data);

      groupsMetaEl.textContent = `${ui.compactNumber((data.by_group || []).length)} ${t("colectivos con datos detectados")}.`;
      groupsBodyEl.innerHTML = (data.by_group || []).map((group) => `
        <tr>
          <td data-label="Colectivo"><strong>${ui.escapeHtml(group.staff_group_code || "—")}</strong><br><span class="muted">${ui.escapeHtml(group.staff_group_name || "—")}</span></td>
          <td data-label="Publicaciones">${ui.compactNumber(group.publications_count)}</td>
          <td data-label="Plazas detectadas">${ui.compactNumber(group.offered_positions_count)}</td>
          <td data-label="Adjudicaciones">${ui.compactNumber(group.awards_count)}</td>
          <td data-label="Bolsa">${ui.compactNumber(group.bag_members_count)}</td>
          <td data-label="Última fecha">${ui.escapeHtml(ui.formatDate(group.latest_publication_date))}</td>
        </tr>
      `).join("") || ui.tableEmpty(6, t("No hay colectivos cargados."));
    }

    Promise.all([
      ui.apiGet("/api/non-docent/summary"),
      ui.apiGet("/api/non-docent/positions?limit=1").catch(() => ({ total: 0 })),
    ])
      .then(([summary, positions]) => {
        summary.totals = summary.totals || {};
        summary.totals.available_positions = Number(positions.total || 0);
        renderSummary(summary);
      })
      .catch((error) => {
        feedbackEl.textContent = `${t("No se pudo cargar el resumen:")} ${error.message}`;
        groupsMetaEl.textContent = t("No se pudo cargar el resumen.");
        groupsBodyEl.innerHTML = ui.tableEmpty(6, t("No se pudo cargar el resumen."));
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", run);
  } else {
    run();
  }
})();
