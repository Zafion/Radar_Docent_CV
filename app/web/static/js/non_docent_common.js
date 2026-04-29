window.NonDocentUI = window.NonDocentUI || (function () {
  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function formatDate(dateIso) {
    if (!dateIso) return "Sin fecha";
    const [year, month, day] = String(dateIso).split("-");
    if (!year || !month || !day) return dateIso;
    return `${day}/${month}/${year}`;
  }

  function compactNumber(value) {
    const number = Number(value || 0);
    if (!Number.isFinite(number)) return "0";
    return new Intl.NumberFormat("es-ES").format(number);
  }

  function apiGet(url) {
    return fetch(url, { headers: { Accept: "application/json" } })
      .then(async (response) => {
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          const detail = data?.detail || `Error ${response.status}`;
          throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
        }
        return data;
      });
  }

  function setSelectOptions(selectEl, placeholder, options) {
    if (!selectEl) return;
    const current = selectEl.value;
    const optionHtml = options
      .filter((item) => item && item.value)
      .map((item) => `<option value="${escapeHtml(item.value)}">${escapeHtml(item.label || item.value)}</option>`)
      .join("");
    selectEl.innerHTML = `<option value="">${escapeHtml(placeholder)}</option>${optionHtml}`;
    if (Array.from(selectEl.options).some((option) => option.value === current)) {
      selectEl.value = current;
    }
  }

  function sourceButton(url) {
    if (!url) return "—";
    return `<a class="button button--ghost button--xs" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">PDF</a>`;
  }

  function tableEmpty(colspan, message) {
    return `<tr><td colspan="${colspan}" class="muted data-table__empty">${escapeHtml(message)}</td></tr>`;
  }

  function groupOptionsFromSummary(summary) {
    return (summary.by_group || []).map((group) => ({
      value: group.staff_group_code,
      label: `${group.staff_group_code} · ${group.staff_group_name}`,
    }));
  }

  return {
    escapeHtml,
    formatDate,
    compactNumber,
    apiGet,
    setSelectOptions,
    sourceButton,
    tableEmpty,
    groupOptionsFromSummary,
  };
})();
