(function () {
  const form = document.getElementById("nd-person-search-form");
  const queryInput = document.getElementById("nd-person-query");
  const feedbackEl = document.getElementById("nd-person-feedback");
  const resultsEl = document.getElementById("nd-person-results");
  const ui = window.NonDocentUI;

  function saveSelectedPerson(item) {
    sessionStorage.setItem("radar_non_docent_selected_person", JSON.stringify({
      normalizedName: item.normalized_name,
      displayName: item.display_name,
    }));
  }

  function render(items) {
    if (!items.length) {
      feedbackEl.textContent = "Sin coincidencias.";
      resultsEl.innerHTML = "";
      return;
    }
    feedbackEl.textContent = `${items.length} coincidencias.`;
    resultsEl.innerHTML = items.map((item, index) => `
      <article class="result-item">
        <div>
          <h3>${ui.escapeHtml(item.display_name)}</h3>
          <p>${ui.compactNumber(item.total_awards)} adjudicaciones · ${ui.compactNumber(item.total_bag_records)} registros de bolsa · última fecha ${ui.escapeHtml(ui.formatDate(item.last_seen_date))}</p>
        </div>
        <button class="button button--secondary" type="button" data-index="${index}">Ver ficha</button>
      </article>
    `).join("");

    Array.from(resultsEl.querySelectorAll("button[data-index]")).forEach((button) => {
      button.addEventListener("click", () => {
        const item = items[Number(button.dataset.index)];
        saveSelectedPerson(item);
        window.location.href = "/no-docente/resultado-persona";
      });
    });
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const q = queryInput.value.trim();
    if (q.length < 2) {
      feedbackEl.textContent = "Introduce al menos dos caracteres.";
      return;
    }
    feedbackEl.textContent = "Buscando...";
    resultsEl.innerHTML = "";
    ui.apiGet(`/api/non-docent/persons/search?q=${encodeURIComponent(q)}`)
      .then((data) => render(data.items || []))
      .catch((error) => {
        feedbackEl.textContent = error.message;
      });
  });
})();
