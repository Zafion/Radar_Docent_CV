window.NonDocentUI = window.NonDocentUI || (function () {
  const LOCATION_STORAGE_KEY = "radar_docent_user_origin";

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

  function apiJson(url, payload) {
    return fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify(payload),
    }).then(async (response) => {
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

  function sourceButton(url, label = "PDF oficial") {
    if (!url) return "—";
    return `<a class="button button--ghost button--xs" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)}</a>`;
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

  function publicationKindLabel(kind) {
    return {
      adc_call: "Convocatoria ADC",
      adc_award: "Adjudicación ADC",
      bag_update: "Actualización de bolsa",
      funcion_publica_bag: "Bolsa Función Pública",
    }[kind] || kind || "Publicación";
  }

  function loadStoredOrigin() {
    try {
      const raw = localStorage.getItem(LOCATION_STORAGE_KEY);
      if (!raw) return { lat: null, lon: null };
      const parsed = JSON.parse(raw);
      const lat = Number(parsed.lat);
      const lon = Number(parsed.lon);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
        localStorage.removeItem(LOCATION_STORAGE_KEY);
        return { lat: null, lon: null };
      }
      return { lat, lon };
    } catch {
      return { lat: null, lon: null };
    }
  }

  function hasOrigin(origin = loadStoredOrigin()) {
    return Number.isFinite(origin.lat) && Number.isFinite(origin.lon);
  }

  function saveStoredOrigin(origin) {
    localStorage.setItem(LOCATION_STORAGE_KEY, JSON.stringify(origin));
  }

  function clearStoredOrigin() {
    try {
      localStorage.removeItem(LOCATION_STORAGE_KEY);
    } catch (_) {
      // Sin acción si el navegador bloquea localStorage.
    }
  }

  function appendOriginParams(params) {
    const origin = loadStoredOrigin();
    if (hasOrigin(origin)) {
      params.set("origin_lat", String(origin.lat));
      params.set("origin_lon", String(origin.lon));
    }
    return params;
  }

  function geolocationErrorMessage(error) {
    if (!error) return "No se pudo obtener tu ubicación.";
    if (error.code === 1) return "Permiso de ubicación denegado. Revisa los permisos del navegador para funkcionario.com.";
    if (error.code === 2) return "No se pudo determinar la ubicación del dispositivo.";
    if (error.code === 3) return "La ubicación ha tardado demasiado. Prueba de nuevo.";
    return error.message || "No se pudo obtener tu ubicación.";
  }

  function getCurrentPosition() {
    if (!navigator.geolocation) {
      return Promise.reject(new Error("Tu navegador no permite geolocalización."));
    }
    return new Promise((resolve, reject) => {
      navigator.geolocation.getCurrentPosition(
        (position) => {
          const lat = Number(position.coords.latitude);
          const lon = Number(position.coords.longitude);
          if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
            reject(new Error("El navegador devolvió una ubicación no válida."));
            return;
          }
          const origin = { lat, lon };
          saveStoredOrigin(origin);
          resolve(origin);
        },
        (error) => reject(new Error(geolocationErrorMessage(error))),
        { enableHighAccuracy: false, timeout: 15000, maximumAge: 600000 }
      );
    });
  }

  function bindLocationControls({ statusId, useButtonId, clearButtonId, feedbackId, onChange } = {}) {
    const statusEl = document.getElementById(statusId || "nd-location-status");
    const useButton = document.getElementById(useButtonId || "nd-use-my-location");
    const clearButton = document.getElementById(clearButtonId || "nd-clear-location");
    const feedbackEl = document.getElementById(feedbackId || "nd-page-feedback");

    function setFeedback(message, isError = false) {
      if (!feedbackEl) return;
      feedbackEl.textContent = message || "";
      feedbackEl.classList.toggle("is-error", Boolean(isError));
    }

    function update() {
      const active = hasOrigin();
      if (statusEl) {
        statusEl.classList.toggle("location-status--active", active);
        statusEl.textContent = active ? "Activa · distancia disponible" : "No activada · sin distancia calculada";
      }
      if (useButton) {
        if (!navigator.geolocation) {
          useButton.textContent = "Ubicación no disponible";
          useButton.disabled = true;
        } else {
          useButton.textContent = active ? "Actualizar ubicación" : "Usar mi ubicación";
          useButton.classList.toggle("is-active", active);
        }
      }
      if (clearButton) {
        clearButton.disabled = !active;
        clearButton.classList.toggle("is-hidden", !active);
      }
    }

    useButton?.addEventListener("click", async () => {
      useButton.disabled = true;
      useButton.textContent = "Obteniendo ubicación...";
      try {
        await getCurrentPosition();
        update();
        setFeedback("Ubicación activada correctamente. Se usará para calcular distancias cuando haya centros localizados.");
        if (typeof onChange === "function") await onChange();
      } catch (error) {
        setFeedback(error.message, true);
      } finally {
        useButton.disabled = false;
        update();
      }
    });

    clearButton?.addEventListener("click", async () => {
      clearStoredOrigin();
      update();
      setFeedback("Ubicación borrada. Ya no se calcularán distancias con tu posición.");
      if (typeof onChange === "function") await onChange();
    });

    update();
    return { update };
  }

  function urlBase64ToUint8Array(base64String) {
    const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
    const base64 = (base64String + padding).replaceAll("-", "+").replaceAll("_", "/");
    const rawData = window.atob(base64);
    return Uint8Array.from([...rawData].map((char) => char.charCodeAt(0)));
  }

  async function getPushSubscription() {
    if (!("serviceWorker" in navigator) || !("PushManager" in window)) return null;
    const registration = await navigator.serviceWorker.register("/sw.js");
    return registration.pushManager.getSubscription();
  }

  async function subscribePush() {
    const keyData = await apiGet("/api/push/public-key");
    if (!keyData.configured || !keyData.public_key) {
      throw new Error("Las alertas no están configuradas todavía.");
    }
    const registration = await navigator.serviceWorker.register("/sw.js");
    let subscription = await registration.pushManager.getSubscription();
    if (!subscription) {
      subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(keyData.public_key),
      });
    }
    await apiJson("/api/push/subscribe", subscription);
    return subscription;
  }

  async function unsubscribePush() {
    const registration = await navigator.serviceWorker.register("/sw.js");
    const subscription = await registration.pushManager.getSubscription();
    if (!subscription) return;
    await apiJson("/api/push/unsubscribe", { endpoint: subscription.endpoint });
    await subscription.unsubscribe();
  }

  function bindPushToggle({ buttonId, feedbackId } = {}) {
    const button = document.getElementById(buttonId || "nd-push-toggle-button");
    const feedbackEl = document.getElementById(feedbackId || "nd-page-feedback");

    function setFeedback(message, isError = false) {
      if (!feedbackEl) return;
      feedbackEl.textContent = message || "";
      feedbackEl.classList.toggle("is-error", Boolean(isError));
    }

    async function updateLabel() {
      if (!button) return;
      if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
        button.textContent = "Alertas no disponibles";
        button.disabled = true;
        return;
      }
      try {
        const keyData = await apiGet("/api/push/public-key");
        if (!keyData.configured || !keyData.public_key) {
          button.textContent = "Alertas no disponibles";
          button.disabled = true;
          return;
        }
        const sub = await getPushSubscription();
        button.textContent = sub ? "Desactivar alertas de novedades" : "Activar alertas de novedades";
      } catch (_) {
        button.textContent = "Alertas no disponibles";
        button.disabled = true;
      }
    }

    button?.addEventListener("click", async () => {
      button.disabled = true;
      try {
        const current = await getPushSubscription();
        if (current) {
          button.textContent = "Desactivando alertas...";
          await unsubscribePush();
          setFeedback("Alertas de novedades desactivadas.");
        } else {
          button.textContent = "Activando alertas...";
          await subscribePush();
          setFeedback("Alertas de novedades activadas.");
        }
      } catch (error) {
        setFeedback(error.message || "No se pudo cambiar el estado de las alertas.", true);
      } finally {
        button.disabled = false;
        await updateLabel();
      }
    });

    updateLabel();
    return { updateLabel };
  }

  function renderPager(container, { total, limit, offset, onPage }) {
    if (!container) return;
    const totalNumber = Number(total || 0);
    const limitNumber = Number(limit || 20);
    const offsetNumber = Number(offset || 0);
    const from = totalNumber ? offsetNumber + 1 : 0;
    const to = Math.min(offsetNumber + limitNumber, totalNumber);
    const prevDisabled = offsetNumber <= 0;
    const nextDisabled = offsetNumber + limitNumber >= totalNumber;

    container.innerHTML = `
      <div class="pagination-controls__summary">Mostrando ${compactNumber(from)}–${compactNumber(to)} de ${compactNumber(totalNumber)}. Si no encuentras el resultado, afina la búsqueda.</div>
      <div class="pagination-controls__buttons">
        <button class="button button--ghost" type="button" data-page="prev" ${prevDisabled ? "disabled" : ""}>Anterior</button>
        <button class="button button--ghost" type="button" data-page="next" ${nextDisabled ? "disabled" : ""}>Siguiente</button>
      </div>
    `;

    container.querySelector('[data-page="prev"]')?.addEventListener("click", () => {
      if (prevDisabled || typeof onPage !== "function") return;
      onPage(Math.max(0, offsetNumber - limitNumber));
    });
    container.querySelector('[data-page="next"]')?.addEventListener("click", () => {
      if (nextDisabled || typeof onPage !== "function") return;
      onPage(offsetNumber + limitNumber);
    });
  }

  return {
    escapeHtml,
    formatDate,
    compactNumber,
    apiGet,
    apiJson,
    setSelectOptions,
    sourceButton,
    tableEmpty,
    groupOptionsFromSummary,
    publicationKindLabel,
    loadStoredOrigin,
    hasOrigin,
    clearStoredOrigin,
    appendOriginParams,
    bindLocationControls,
    bindPushToggle,
    renderPager,
  };
})();
