(function () {
  const latestOffersDateEl = document.getElementById("latest-offers-date");

  const personSearchForm = document.getElementById("person-search-form");
  const personQueryInput = document.getElementById("person-query");
  const personSearchFeedback = document.getElementById("person-search-feedback");
  const personSearchResults = document.getElementById("person-search-results");
  const pageFeedback = document.getElementById("page-feedback");

  const locationStatusEl = document.getElementById("location-status");
  const useMyLocationButton = document.getElementById("use-my-location");
  const pushToggleButton = document.getElementById("push-toggle-button");

  let userOrigin = loadStoredOrigin();

  function feedbackTarget() {
    return personSearchFeedback || pageFeedback;
  }

  function setFeedback(message, isError = false) {
    const target = feedbackTarget();
    if (!target) return;
    target.textContent = message || "";
    target.classList.toggle("is-error", Boolean(isError));
  }

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
    const [year, month, day] = dateIso.split("-");
    if (!year || !month || !day) return dateIso;
    return `${day}/${month}/${year}`;
  }

  function loadStoredOrigin() {
    try {
      const raw = localStorage.getItem("radar_docent_user_origin");
      if (!raw) return { lat: null, lon: null };
      const parsed = JSON.parse(raw);
      return {
        lat: typeof parsed.lat === "number" ? parsed.lat : null,
        lon: typeof parsed.lon === "number" ? parsed.lon : null,
      };
    } catch {
      return { lat: null, lon: null };
    }
  }

  function saveStoredOrigin(origin) {
    localStorage.setItem("radar_docent_user_origin", JSON.stringify(origin));
  }

  function updateLocationStatus() {
    if (!locationStatusEl) return;

    if (userOrigin.lat !== null && userOrigin.lon !== null) {
      locationStatusEl.textContent = `Activa (${userOrigin.lat.toFixed(4)}, ${userOrigin.lon.toFixed(4)})`;
      return;
    }

    locationStatusEl.textContent = "No activada · sin distancia calculada";
  }

  async function ensureUserLocation() {
    if (!navigator.geolocation) {
      throw new Error("Tu navegador no permite geolocalización.");
    }

    return new Promise((resolve, reject) => {
      navigator.geolocation.getCurrentPosition(
        (position) => {
          userOrigin = {
            lat: position.coords.latitude,
            lon: position.coords.longitude,
          };
          saveStoredOrigin(userOrigin);
          updateLocationStatus();
          resolve(userOrigin);
        },
        () => {
          reject(new Error("No se pudo obtener tu ubicación."));
        },
        {
          enableHighAccuracy: true,
          timeout: 10000,
          maximumAge: 300000,
        }
      );
    });
  }

  async function apiGet(url) {
    const response = await fetch(url, { headers: { Accept: "application/json" } });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = data?.detail || `Error ${response.status}`;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data;
  }

  async function apiJson(url, payload) {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = data?.detail || `Error ${response.status}`;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data;
  }

  function urlBase64ToUint8Array(base64String) {
    const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
    const base64 = (base64String + padding).replaceAll("-", "+").replaceAll("_", "/");
    const rawData = window.atob(base64);
    return Uint8Array.from([...rawData].map((char) => char.charCodeAt(0)));
  }

  async function getPushSubscription() {
    if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
      return null;
    }
    const registration = await navigator.serviceWorker.register("/sw.js");
    return registration.pushManager.getSubscription();
  }

  async function updatePushToggleLabel() {
    if (!pushToggleButton) return;
    if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
      pushToggleButton.textContent = "Alertas no disponibles";
      pushToggleButton.disabled = true;
      return;
    }

    try {
      const keyData = await apiGet("/api/push/public-key");
      if (!keyData.configured || !keyData.public_key) {
        pushToggleButton.textContent = "Alertas no disponibles";
        pushToggleButton.disabled = true;
        return;
      }

      const sub = await getPushSubscription();
      pushToggleButton.textContent = sub
        ? "Desactivar alertas de novedades"
        : "Activar alertas de novedades";
    } catch (_) {
      pushToggleButton.textContent = "Alertas no disponibles";
      pushToggleButton.disabled = true;
    }
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

    if (!subscription) {
      return;
    }

    await apiJson("/api/push/unsubscribe", { endpoint: subscription.endpoint });
    await subscription.unsubscribe();
  }

  async function loadLatestOffersMeta() {
    if (!latestOffersDateEl) return;

    latestOffersDateEl.textContent = "Cargando...";

    try {
      const data = await apiGet("/api/offered-positions?limit=1&order_by=document_date&order_dir=desc");
      const latestItem = data.items?.[0];
      const latestOffersDate = latestItem?.document_date_iso || null;
      latestOffersDateEl.textContent = latestOffersDate ? formatDate(latestOffersDate) : "Sin datos";
    } catch (_) {
      latestOffersDateEl.textContent = "No disponible";
    }
  }

  function renderSearchResults(items) {
    if (!personSearchResults) return;

    if (!items.length) {
      personSearchResults.innerHTML = '<div class="result-item"><div><h3>Sin coincidencias</h3><p>Prueba con menos términos o revisa el formato del nombre.</p></div></div>';
      return;
    }

    personSearchResults.innerHTML = items.map((item) => `
      <div class="result-item">
        <div>
          <h3>${escapeHtml(item.display_name)}</h3>
          <p>${escapeHtml(item.total_records)} registros · ${escapeHtml(item.total_awarded)} adjudicaciones · ${escapeHtml(item.total_difficult_positions)} difícil cobertura</p>
        </div>
        <button class="button button--secondary" type="button" data-person-detail="true" data-normalized-name="${escapeHtml(item.normalized_name)}" data-display-name="${escapeHtml(item.display_name)}">Ver ficha</button>
      </div>
    `).join("");

    personSearchResults.querySelectorAll("button[data-person-detail]").forEach((button) => {
      button.addEventListener("click", () => {
        const normalizedName = button.dataset.normalizedName;
        if (!normalizedName) return;

        sessionStorage.setItem("radar_docent_selected_person", JSON.stringify({
          normalizedName,
          displayName: button.dataset.displayName || "",
          selectedAt: new Date().toISOString(),
        }));

        window.location.href = "/resultado-persona";
      });
    });
  }

  async function handleSearchSubmit(event) {
    event.preventDefault();
    if (!personQueryInput || !personSearchResults) return;

    const query = personQueryInput.value.trim();
    if (query.length < 2) {
      setFeedback("Introduce al menos 2 caracteres.", true);
      return;
    }

    setFeedback("Buscando coincidencias...");
    personSearchResults.innerHTML = "";

    try {
      const data = await apiGet(`/api/search/persons?q=${encodeURIComponent(query)}&limit=10`);
      setFeedback(data.count ? "Abre la ficha completa de la coincidencia que te interese." : "");
      renderSearchResults(data.items || []);
    } catch (error) {
      setFeedback(error.message, true);
    }
  }

  useMyLocationButton?.addEventListener("click", async () => {
    const originalText = useMyLocationButton.textContent;
    useMyLocationButton.disabled = true;
    useMyLocationButton.textContent = "Obteniendo ubicación...";

    try {
      await ensureUserLocation();
      setFeedback("Ubicación activada correctamente.");
    } catch (error) {
      setFeedback(error.message, true);
    } finally {
      useMyLocationButton.disabled = false;
      useMyLocationButton.textContent = originalText;
    }
  });

  pushToggleButton?.addEventListener("click", async () => {
    const originalText = pushToggleButton.textContent;
    pushToggleButton.disabled = true;

    try {
      const current = await getPushSubscription();

      if (current) {
        pushToggleButton.textContent = "Desactivando alertas...";
        await unsubscribePush();
        setFeedback("Alertas de novedades desactivadas.");
      } else {
        pushToggleButton.textContent = "Activando alertas...";
        await subscribePush();
        setFeedback("Alertas de novedades activadas.");
      }
    } catch (error) {
      setFeedback(error.message || "No se pudo cambiar el estado de las alertas.", true);
    } finally {
      pushToggleButton.disabled = false;
      await updatePushToggleLabel();
      if (!pushToggleButton.textContent) {
        pushToggleButton.textContent = originalText;
      }
    }
  });

  updateLocationStatus();
  updatePushToggleLabel();
  loadLatestOffersMeta();
  personSearchForm?.addEventListener("submit", handleSearchSubmit);
})();
