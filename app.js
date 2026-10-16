const SERVICE_LABELS = {
  cabeleireiro: "Cabelo", barbeiro: "Barbearia", manicure: "Manicure",
  pedicure: "Pedicure", sobrancelhas: "Sobrancelhas", cilios: "Cílios",
  maquiagem: "Maquiagem", estetica: "Estética", massagem: "Massagem",
  depilacao: "Depilação", tatuagem: "Tatuagem", piercing: "Piercing",
  tatuagem_piercing: "Tatuagem e piercing"
};
const SERVICE_ICONS = {
  cabeleireiro: "💇", barbeiro: "✂️", manicure: "💅", pedicure: "🦶",
  sobrancelhas: "🤨", cilios: "👁️", maquiagem: "💄", estetica: "🧖",
  massagem: "💆", depilacao: "🌸", tatuagem: "🎨", piercing: "💎",
  tatuagem_piercing: "🎨"
};

const money = value => new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" }).format(value);
const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, char => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
})[char]);

function setLocationFromCoordinates(position, input, form) {
  input.value = `${position.coords.latitude.toFixed(6)},${position.coords.longitude.toFixed(6)}`;
  form.requestSubmit();
}

function requestLocation(input, form, message) {
  if (!navigator.geolocation) {
    message.textContent = "Seu navegador não oferece localização. Digite sua cidade ou CEP.";
    return;
  }
  message.textContent = "Aguardando permissão para acessar sua localização…";
  navigator.geolocation.getCurrentPosition(
    position => setLocationFromCoordinates(position, input, form),
    () => { message.textContent = "Não foi possível acessar a localização. Você pode buscar por cidade ou CEP."; },
    { enableHighAccuracy: false, timeout: 10000, maximumAge: 300000 }
  );
}

function initHome() {
  const button = document.getElementById("use-location");
  if (!button) return;
  button.addEventListener("click", () => {
    const form = document.querySelector(".search-panel");
    requestLocation(form.elements.local, form, document.getElementById("location-feedback"));
  });
}

function parseLocation(value) {
  const coordinates = value.match(/^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$/);
  return coordinates ? { lat: coordinates[1], lon: coordinates[2] } : { city: value };
}

function bookingPaymentSummary(booking) {
  const methods = { pix: "Pix", cartao: "cartão" };
  if (booking.paymentStatus === "demo_completed") {
    return `Pagamento demonstrativo por ${methods[booking.paymentMethod] || "método não informado"}; nenhuma cobrança foi feita. Recebedor previsto: Click Beleza.`;
  }
  if (booking.paymentStatus === "demo_pending") {
    return "Pagamento demonstrativo pendente; nenhuma cobrança foi feita.";
  }
  return "Pagamento não informado.";
}

function bookingPaymentCheckout(booking) {
  const dateLabel = new Date(`${booking.date}T12:00:00`).toLocaleDateString("pt-BR");
  return `<div class="checkout-progress" aria-label="Etapas do pedido">
      <span class="checkout-step complete">1 · Pedido enviado</span><span class="checkout-step active">2 · Pagamento</span>
    </div>
    <h3 class="checkout-title" id="checkout-heading" tabindex="-1">Finalize seu pedido</h3>
    <div class="order-summary"><p class="eyebrow">RESUMO DO PEDIDO</p><strong>${escapeHtml(booking.professionalName)}</strong>
      <p>${escapeHtml(booking.serviceName)} · ${dateLabel} às ${escapeHtml(booking.time)}</p>
      <p class="order-total">Total demonstrativo <strong>${money(booking.price)}</strong></p>
      <small>Código do pedido: ${escapeHtml(booking.id)}</small>
    </div>
    <fieldset class="payment-choice"><legend>Escolha uma forma de pagamento</legend>
      <p class="payment-disclaimer">O recebedor previsto seria Click Beleza, mas este checkout é apenas demonstrativo: nenhuma cobrança, transferência ou repasse será realizado. Não informe dados de cartão.</p>
      <div class="payment-methods">
        <label class="payment-method-option"><input type="radio" name="paymentMethod" value="pix" required checked><span><strong>Pix</strong><small>Simulação, sem QR Code ou transferência real</small></span></label>
        <label class="payment-method-option"><input type="radio" name="paymentMethod" value="cartao" required><span><strong>Cartão</strong><small>Simulação, sem inserir dados do cartão</small></span></label>
      </div>
    </fieldset>
    <button class="button button-primary button-wide" type="submit">Concluir etapa demonstrativa</button>
    <button class="button button-outline button-wide" type="button" data-cancel-order>Cancelar pedido</button>
    <p class="form-feedback" id="booking-feedback" aria-live="polite"></p>`;
}

function professionalCard(professional) {
  const servicePrices = (professional.services || []).map(item => Number(item.price)).filter(Number.isFinite);
  const price = servicePrices.length ? Math.min(...servicePrices) : professional.price;
  const hasPrice = price != null && Number.isFinite(Number(price));
  const distanceText = professional.distance == null ? "" : `⌖ ${Number(professional.distance).toFixed(1)} km`;
  const rating = professional.external
    ? '<span class="source-tag">📍 Local encontrado no OpenStreetMap</span>'
    : professional.reviewCount
      ? `<span class="rating">★ ${Number(professional.rating).toFixed(1)} <small>(${professional.reviewCount})</small></span>`
      : '<span class="source-tag">Novo perfil</span>';
  const cardLocation = professional.external
    ? professional.address
    : `${professional.neighborhood || professional.city}${distanceText ? ` · ${distanceText}` : ""}`;
  const actions = professional.external
    ? `<div class="card-bottom"><p>Preço não informado</p><a class="button button-small" href="${escapeHtml(professional.sourceUrl)}" target="_blank" rel="noopener noreferrer">Ver no mapa <span aria-hidden="true">↗</span></a></div><a class="map-link" href="${escapeHtml(professional.googleMapsUrl)}" target="_blank" rel="noopener noreferrer">Abrir no Google Maps ↗</a>`
    : `<div class="card-bottom"><p>${hasPrice ? `A partir de <strong>${money(price)}</strong>` : "Serviços em breve"}</p><a class="button button-small" href="/professional.html?id=${encodeURIComponent(professional.id)}">Ver perfil <span aria-hidden="true">→</span></a></div>`;
  return `<article class="professional-card">
    <div class="professional-photo" style="--card-color:${escapeHtml(professional.color || "#f2d8d0")}"><span>${escapeHtml(professional.initials || professional.name.slice(0, 1))}</span></div>
    <div class="professional-card-body"><div class="card-topline"><span class="service-tag"><span aria-hidden="true">${SERVICE_ICONS[professional.service] || "✨"}</span> ${escapeHtml(SERVICE_LABELS[professional.service] || professional.service)}</span>${rating}</div>
      <h3>${escapeHtml(professional.name)}</h3><p class="card-location">${escapeHtml(cardLocation)}${professional.external && distanceText ? ` · ${escapeHtml(distanceText)}` : ""}</p>
      ${professional.demo ? '<span class="demo-tag">Perfil de demonstração</span>' : ""}
      ${actions}
    </div>
  </article>`;
}

async function initProfessionals() {
  const list = document.getElementById("professional-list");
  if (!list) return;
  const params = new URLSearchParams(location.search);
  const form = document.getElementById("filter-form");
  const service = document.getElementById("filter-service");
  const locationInput = document.getElementById("filter-location");
  const sort = document.getElementById("filter-sort");
  const chips = document.getElementById("service-chips");
  service.value = params.get("servico") || "";
  locationInput.value = params.get("local") || "Marília, SP";
  sort.value = params.get("ordem") || "distance";
  const serviceOptions = [...service.options].map(option => ({
    id: option.value,
    label: option.textContent.replace(/^[^\p{L}\p{N}]+/u, "").trim(),
    icon: option.value ? (SERVICE_ICONS[option.value] || "✨") : "✨"
  }));
  chips.innerHTML = serviceOptions.map(option =>
    `<button class="service-chip" type="button" data-service="${escapeHtml(option.id)}" aria-pressed="${String(service.value === option.id)}"><span aria-hidden="true">${option.icon}</span>${escapeHtml(option.label)}</button>`
  ).join("");

  let latestRequest = 0;
  const submitButton = form.querySelector(".search-submit");
  const submitLabel = submitButton.innerHTML;
  async function load(updateAddress = false) {
    const requestId = ++latestRequest;
    const query = new URLSearchParams();
    let osmLocation = {};
    if (service.value) query.set("servico", service.value);
    let locationNote = "";
    if (locationInput.value.trim()) {
      let rawLocation = locationInput.value.trim();
      if (/^\d{5}-?\d{3}$/.test(rawLocation)) {
        try {
          const cep = await chamarAPI(`cep?cep=${encodeURIComponent(rawLocation)}`);
          const street = [cep.logradouro, cep.bairro].filter(Boolean).join(", ");
          const precise = cep.lat != null && cep.lon != null && cep.precision !== "cidade";
          rawLocation = precise ? `${cep.lat}, ${cep.lon}` : cep.localidade;
          locationNote = `CEP localizado${street ? ` (${street})` : ""} em ${cep.localidade}${cep.uf ? `, ${cep.uf}` : ""}${precise ? ": distâncias calculadas a partir desse endereço" : ""}.`;
        } catch (error) {
          if (requestId !== latestRequest) return;
          list.innerHTML = `<p class="error-message">${escapeHtml(error.message)}</p>`;
          return;
        }
      }
      const place = parseLocation(rawLocation);
      if (place.city) {
        place.city = place.city.replace(/,\s*(sp|são paulo|sao paulo)$/i, "").trim();
      }
      osmLocation = place;
      Object.entries(place).forEach(([key, value]) => query.set(key, value));
    }
    query.set("ordem", sort.value);
    if (updateAddress) {
      const address = new URLSearchParams();
      if (service.value) address.set("servico", service.value);
      if (locationInput.value.trim()) address.set("local", locationInput.value.trim());
      address.set("ordem", sort.value);
      history.replaceState(null, "", `/professionals.html?${address}`);
    }
    const submitButton = form.querySelector(".search-submit");
    submitButton.disabled = true;
    submitButton.setAttribute("aria-busy", "true");
    submitButton.textContent = "Buscando…";
    list.innerHTML = '<p class="loading">Buscando profissionais…</p>';
    try {
      const realSearch = buscarReaisDeMarilia({
        servico: service.value,
        cidade: osmLocation.city || "",
        lat: osmLocation.lat || "",
        lon: osmLocation.lon || ""
      }).then(data => ({ data, error: null })).catch(error => ({ data: null, error }));
      const result = await chamarAPI(`profissionais?${query}`);
      if (requestId !== latestRequest) return;
      function renderResults(realResult, loading = false) {
        const realProfessionals = realResult.data?.professionals || [];
        const registeredProfessionals = result.professionals.filter(professional => !professional.demo);
        const demoProfessionals = realProfessionals.length || registeredProfessionals.length
          ? []
          : result.professionals.filter(professional => professional.demo);
        const professionals = [...registeredProfessionals, ...realProfessionals, ...demoProfessionals];
        const sortValue = item => {
          if (sort.value === "price") {
            const prices = (item.services || []).map(serviceItem => Number(serviceItem.price)).filter(Number.isFinite);
            return item.external ? Number.POSITIVE_INFINITY : (prices.length ? Math.min(...prices) : Number(item.price) || Number.POSITIVE_INFINITY);
          }
          if (sort.value === "rating") {
            return item.external || item.rating == null ? Number.POSITIVE_INFINITY : -Number(item.rating);
          }
          return item.distance == null ? Number.POSITIVE_INFINITY : Number(item.distance);
        };
        professionals.sort((left, right) => sortValue(left) - sortValue(right));
        document.getElementById("results-count").textContent = `${professionals.length} ${professionals.length === 1 ? "opção" : "opções"}`;
        document.getElementById("results-title").textContent = service.value
          ? `${SERVICE_LABELS[service.value] || service.value} na sua região`
          : "Profissionais disponíveis";
        chips.querySelectorAll(".service-chip").forEach(chip => {
          chip.setAttribute("aria-pressed", String(chip.dataset.service === service.value));
        });
        const sourceError = realResult.error
          ? " A busca no OpenStreetMap está temporariamente indisponível; mostrando os cadastros disponíveis no Click Beleza."
          : "";
        const sourceLoading = loading ? " Consultando locais mapeados no OpenStreetMap…" : "";
        document.getElementById("results-location").textContent = [locationNote, result.locationMessage, sourceLoading, sourceError].filter(Boolean).join(" ");
        document.getElementById("maps-discovery").innerHTML = `<a class="button button-outline" href="${escapeHtml(result.googleMapsUrl)}" target="_blank" rel="noopener noreferrer">Ver no Google Maps <span aria-hidden="true">↗</span></a><span>${realProfessionals.length ? "Locais do OpenStreetMap podem não ter preço, avaliações ou agenda disponíveis. " : ""}Os dados do mapa são © OpenStreetMap contributors (ODbL).</span>`;
        list.innerHTML = professionals.length
          ? professionals.map(professionalCard).join("")
          : `<div class="empty-state"><span>♡</span><h3>Nenhum profissional encontrado</h3><p>Tente outra categoria ou cidade. Você também pode buscar no Google Maps.</p><a class="button button-outline" href="${escapeHtml(result.googleMapsUrl)}" target="_blank" rel="noopener noreferrer">Buscar no Google Maps ↗</a></div>`;
      }
      renderResults({ data: null, error: null }, true);
      realSearch.then(realResult => {
        if (requestId === latestRequest) renderResults(realResult);
      }).catch(error => {
        console.error("Não foi possível atualizar os resultados do mapa:", error);
        if (requestId === latestRequest) renderResults({ data: null, error });
      });
    } catch (error) {
      if (requestId !== latestRequest) return;
      list.innerHTML = `<p class="error-message">${escapeHtml(error.message)}</p>`;
    } finally {
      if (requestId === latestRequest) {
        submitButton.disabled = false;
        submitButton.removeAttribute("aria-busy");
        submitButton.innerHTML = submitLabel;
      }
    }
  }
  form.addEventListener("submit", event => { event.preventDefault(); load(true); });
  sort.addEventListener("change", () => load(true));
  service.addEventListener("change", () => load(true));
  chips.addEventListener("click", event => {
    const chip = event.target.closest(".service-chip");
    if (!chip) return;
    service.value = chip.dataset.service;
    load(true);
  });
  document.getElementById("filter-geolocation").addEventListener("click", () => {
    requestLocation(locationInput, form, document.getElementById("results-location"));
  });
  load();
}

function initRegister() {
  const form = document.getElementById("register-form");
  if (!form) return;
  const professionalId = new URLSearchParams(location.search).get("id");
  const submitButton = form.querySelector('[type="submit"]');
  const portfolioInput = document.getElementById("portfolio-files");
  let existingPortfolio = [];
  let selectedPortfolio = [];
  function showPortfolio() {
    const files = [...existingPortfolio, ...selectedPortfolio];
    document.getElementById("portfolio-count").textContent = `${files.length} de 8 itens no portfólio`;
    document.getElementById("portfolio-preview").innerHTML = files.map(item => `<div class="portfolio-thumb">${item.type.startsWith("video/") ? `<video src="${escapeHtml(item.data)}" muted controls></video>` : `<img src="${escapeHtml(item.data)}" alt="${escapeHtml(item.name)}">`}<small>${escapeHtml(item.name)}</small></div>`).join("");
  }
  portfolioInput.addEventListener("change", () => {
    selectedPortfolio = Array.from(portfolioInput.files || []).map(file => ({ file, name: file.name, type: file.type }));
    const total = existingPortfolio.length + selectedPortfolio.length;
    const invalid = selectedPortfolio.find(({ file }) =>
      !["image/jpeg", "image/png", "image/webp", "video/mp4", "video/webm"].includes(file.type) ||
      file.size > (file.type.startsWith("image/") ? 4 : 10) * 1024 * 1024
    );
    if (total > 8 || invalid) {
      document.getElementById("form-feedback").textContent = total > 8
        ? "O portfólio permite até 8 arquivos no total."
        : `O arquivo ${invalid.name} tem formato não suportado ou excede o limite de tamanho.`;
      portfolioInput.value = "";
      selectedPortfolio = [];
      showPortfolio();
      return;
    }
    document.getElementById("form-feedback").textContent = "";
    document.getElementById("portfolio-count").textContent = `${total} de 8 itens no portfólio`;
  });
  if (professionalId) {
    form.querySelector(".profile-extra").open = true;
    document.querySelector(".form-shell h1").textContent = "Atualize seu perfil.";
    document.querySelector(".form-shell>p:not(.eyebrow)").textContent = "Atualize seu perfil quando quiser. Quanto mais informações adicionar, mais os clientes conhecerão seu trabalho.";
    submitButton.innerHTML = 'Salvar alterações <span aria-hidden="true">→</span>';
    chamarAPI(`profissionais/${encodeURIComponent(professionalId)}/editar`).then(({ professional }) => {
      for (const field of ["name", "service", "description", "address", "neighborhood", "city", "email", "phone", "serviceName", "price", "duration", "lat", "lon"]) {
        if (form.elements[field]) form.elements[field].value = professional[field] ?? "";
      }
      existingPortfolio = professional.portfolio || [];
      showPortfolio();
    }).catch(error => {
      document.getElementById("form-feedback").textContent = error.message;
      submitButton.disabled = true;
    });
  }
  const locationButton = document.getElementById("register-location");
  locationButton.addEventListener("click", () => {
    if (!navigator.geolocation) {
      document.getElementById("form-feedback").textContent = "Seu navegador não oferece localização.";
      return;
    }
    document.getElementById("form-feedback").textContent = "Aguardando permissão para acessar sua localização…";
    navigator.geolocation.getCurrentPosition(position => {
      form.elements.lat.value = position.coords.latitude.toFixed(6);
      form.elements.lon.value = position.coords.longitude.toFixed(6);
      document.getElementById("form-feedback").textContent = "Localização preenchida. Confira o endereço antes de salvar.";
    }, () => {
      document.getElementById("form-feedback").textContent = "Não foi possível acessar sua localização. Você pode preencher as coordenadas manualmente.";
    }, { enableHighAccuracy: false, timeout: 10000, maximumAge: 300000 });
  });
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const button = form.querySelector('[type="submit"]');
    const feedback = document.getElementById("form-feedback");
    button.disabled = true;
    feedback.textContent = "Salvando seu perfil…";
    const data = Object.fromEntries(new FormData(form));
    try {
      const uploaded = await Promise.all(selectedPortfolio.map(({ file, name, type }) => new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve({ name, type, data: reader.result });
        reader.onerror = () => reject(new Error(`Não foi possível ler ${name}.`));
        reader.readAsDataURL(file);
      })));
      data.portfolio = [...existingPortfolio, ...uploaded];
      const result = professionalId
        ? await chamarAPI(`profissionais/${encodeURIComponent(professionalId)}`, { method: "PUT", body: JSON.stringify(data) })
        : await chamarAPI("profissionais", { method: "POST", body: JSON.stringify(data) });
      location.href = `/professional.html?id=${encodeURIComponent(result.professional.id)}&novo=1`;
    } catch (error) {
      feedback.textContent = error.message;
      button.disabled = false;
    }
  });
}

async function initProfessional() {
  const container = document.getElementById("professional-detail");
  if (!container) return;
  const id = new URLSearchParams(location.search).get("id");
  if (!id) { container.innerHTML = '<div class="empty-state"><h2>Perfil não encontrado</h2><a href="/professionals.html">Voltar à busca</a></div>'; return; }
  try {
    const { professional } = await chamarAPI(`profissionais/${encodeURIComponent(id)}`);
    const services = professional.services?.length ? professional.services : (professional.price != null ? [{ name: SERVICE_LABELS[professional.service] || professional.service, price: professional.price, duration: 60 }] : []);
    const portfolio = professional.portfolio || [];
    const completion = professional.profileCompletion ?? 50;
    const hasAddress = Boolean(professional.address || professional.neighborhood);
    const hasServices = services.length > 0;
    container.innerHTML = `<div class="profile-layout">
      <section class="profile-main">
        <div class="profile-banner" style="--card-color:${escapeHtml(professional.color || "#f2d8d0")}"><span>${escapeHtml(professional.initials || professional.name.slice(0, 1))}</span><small>${professional.demo ? "PERFIL DE DEMONSTRAÇÃO" : escapeHtml(professional.city)}</small></div>
        ${new URLSearchParams(location.search).has("novo") ? `<div class="success-note">Seu perfil foi publicado! Ele já aparece na busca. Complete as próximas etapas quando quiser para apresentar melhor seu trabalho.</div>` : ""}
        <p class="eyebrow">${escapeHtml(SERVICE_LABELS[professional.service] || professional.service)}</p><h1>${escapeHtml(professional.name)}</h1>
        ${professional.demo ? "" : `<p><a class="inline-link" href="/register.html?id=${encodeURIComponent(professional.id)}">Editar este perfil →</a></p>`}
        ${professional.demo ? `<div class="profile-meta"><span class="rating">★ ${Number(professional.rating).toFixed(1)} (${professional.reviewCount || 0} avaliações)</span><span>⌖ ${escapeHtml(professional.neighborhood || professional.city)}, ${escapeHtml(professional.city)}</span></div>` : `<div class="profile-meta"><span class="source-tag">${professional.reviewCount ? `★ ${Number(professional.rating).toFixed(1)} · ${professional.reviewCount} avaliações` : "Novo perfil · sem avaliações ainda"}</span><span>⌖ ${escapeHtml(professional.city)}</span></div>`}
        ${professional.address ? `<p><a class="map-link" href="https://www.google.com/maps/search/?api=1&amp;query=${encodeURIComponent(`${professional.name} ${professional.address} ${professional.city}`)}" target="_blank" rel="noopener noreferrer">Ver localização no Google Maps ↗</a></p>` : ""}
        ${professional.demo ? `<h2>Sobre</h2><p class="body-copy">${escapeHtml(professional.description || "Profissional de beleza disponível para agendamentos.")}</p>` : professional.description ? `<h2>Sobre</h2><p class="body-copy">${escapeHtml(professional.description)}</p>` : '<p class="muted-copy">Este profissional ainda está preparando a apresentação do seu trabalho.</p>'}
        ${professional.demo ? "" : `<section class="profile-completion" aria-label="Conclusão do perfil"><div class="profile-completion-heading"><div><p class="eyebrow">PERFIL PROFISSIONAL</p><h2>${completion}% concluído</h2></div><a class="inline-link" href="/register.html?id=${encodeURIComponent(professional.id)}">Completar perfil →</a></div><progress max="100" value="${completion}" aria-label="Perfil ${completion}% concluído">${completion}%</progress><p>Adicione uma descrição, serviços e fotos para que clientes conheçam seu trabalho.</p><ul><li class="complete">Nome, categoria e cidade</li><li class="${professional.description ? "complete" : ""}">Descrição do atendimento</li><li class="${hasServices ? "complete" : ""}">Serviços e preços</li><li class="${portfolio.length ? "complete" : ""}">Fotos do trabalho</li></ul></section>`}
        <section class="portfolio-section"><div class="portfolio-heading"><div><p class="eyebrow">TRABALHOS REALIZADOS</p><h2>Portfólio</h2></div>${professional.demo ? "" : `<a class="inline-link" href="/register.html?id=${encodeURIComponent(professional.id)}">Adicionar mídia →</a>`}</div>${portfolio.length ? `<div class="portfolio-gallery">${portfolio.map(item => `<figure>${item.type.startsWith("video/") ? `<video src="${escapeHtml(item.data)}" controls preload="metadata" aria-label="${escapeHtml(item.name)}"></video>` : `<img src="${escapeHtml(item.data)}" alt="${escapeHtml(item.name)}" loading="lazy">`}<figcaption>${escapeHtml(item.name)}</figcaption></figure>`).join("")}</div>` : '<p class="muted-copy">Este profissional ainda não adicionou fotos ou vídeos.</p>'}</section>
        <h2>Serviços</h2>${hasServices ? `<div class="service-list">${services.map((item, index) => `<label class="service-option"><input type="radio" name="booking-service" value="${index}" ${index === 0 ? "checked" : ""}><span><b>${escapeHtml(item.name)}</b><small>${item.duration || 60} min</small></span><strong>${money(item.price)}</strong></label>`).join("")}</div>` : '<p class="muted-copy">Serviços e preços serão adicionados em breve.</p>'}
        ${hasAddress ? `<h2>Localização</h2><p class="body-copy">${escapeHtml([professional.address, professional.neighborhood, professional.city].filter(Boolean).join(", "))}</p>` : ""}
        <section class="pro-chat" id="pro-chat" aria-label="Conversar com o profissional"><p class="eyebrow">FALE DIRETO</p><h2>💬 Conversar com o profissional</h2>
          ${professional.demo ? '<p class="muted-copy">Este perfil é ilustrativo. O chat fica disponível em perfis de profissionais cadastrados.</p>' : '<div id="pro-chat-body"></div>'}
        </section>
        ${professional.demo ? '<p class="demo-warning">Este é um cadastro ilustrativo. Confirme os dados do profissional antes de qualquer atendimento.</p>' : ""}
      </section>
      <aside class="booking-box"><p class="eyebrow">RESERVE SEU HORÁRIO</p><h2>Vamos marcar?</h2>
        ${professional.demo
          ? `<p class="demo-warning">Este perfil e os horários são apenas demonstrativos. O agendamento será liberado quando um profissional real criar o perfil.</p><a class="button button-outline button-wide" href="/register.html">Sou profissional — criar perfil</a>`
          : hasServices ? `<form id="booking-form"><input type="hidden" name="professionalId" value="${escapeHtml(professional.id)}">
          <label>Seu nome<input name="customerName" required maxlength="100" autocomplete="name"></label>
          <label>Telefone / WhatsApp<input type="tel" name="customerPhone" required maxlength="30" autocomplete="tel"></label>
          <div class="booking-when"><label>Data<input type="date" name="date" required></label><label>Horário<select name="time" required><option value="">Selecione</option>${["09:00","10:00","11:00","13:00","14:00","15:00","16:00","17:00"].map(time => `<option>${time}</option>`).join("")}</select></label></div>
          <p class="pay-note"><strong>Como funciona:</strong> envie o pedido e, na próxima etapa, escolha Pix ou cartão para uma simulação. Nenhum pagamento real será feito.</p>
          <button class="button button-primary button-wide" type="submit">Enviar pedido e continuar</button><p id="booking-feedback" class="form-feedback" aria-live="polite"></p>
        </form><small class="booking-disclaimer">Seu horário só fica reservado após confirmação do profissional.</small>`
          : `<p class="muted-copy">Este profissional ainda está cadastrando os serviços. Volte em breve para consultar horários.</p><a class="button button-outline button-wide" href="/professionals.html">Ver outros profissionais</a>`}
      </aside>
    </div>
    <section class="review-section"><p class="eyebrow">QUEM JÁ FOI</p><h2>Avaliações</h2><div id="review-list" class="review-list"></div></section>`;
    renderReviews(professional.reviews || []);
    if (!professional.demo) initProfessionalChat(professional);
    const bookingForm = document.getElementById("booking-form");
    if (bookingForm) {
      const dateInput = bookingForm.querySelector('[name="date"]');
      dateInput.min = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
      let currentBooking = null;
      bookingForm.addEventListener("submit", async event => {
        event.preventDefault();
        const form = event.currentTarget;
        const feedback = document.getElementById("booking-feedback");
        const button = form.querySelector('[type="submit"]');
        button.disabled = true;
        try {
          if (currentBooking) {
            const paymentMethod = form.querySelector('[name="paymentMethod"]:checked')?.value;
            if (!paymentMethod) {
              feedback.textContent = "Escolha Pix ou cartão para continuar.";
              return;
            }
            feedback.textContent = "Registrando a etapa demonstrativa…";
            const result = await chamarAPI(`agendamentos/${encodeURIComponent(currentBooking.id)}/pagamento`, {
              method: "POST", body: JSON.stringify({ paymentMethod })
            });
            currentBooking = result.booking;
            form.innerHTML = `<div class="checkout-progress" aria-label="Etapas do pedido"><span class="checkout-step complete">1 · Pedido enviado</span><span class="checkout-step complete">2 · Demonstração concluída</span></div>
              <div class="payment-success"><p class="eyebrow">ETAPA CONCLUÍDA</p><h3 class="checkout-title" id="checkout-heading" tabindex="-1">Pedido registrado</h3>
                <p>O pagamento por ${escapeHtml(paymentMethod === "pix" ? "Pix" : "cartão")} foi apenas simulado. <strong>Nenhuma cobrança ou transferência foi realizada.</strong></p>
                <p>O pedido <strong>${escapeHtml(currentBooking.id)}</strong> continua aguardando a confirmação do profissional.</p>
                <p>Total do pedido: <strong>${money(currentBooking.price)}</strong></p>
              </div>
              <button class="button button-outline button-wide" type="button" data-cancel-order>Cancelar pedido</button>
              <p class="form-feedback" id="booking-feedback" aria-live="polite"></p>`;
            form.querySelector("#checkout-heading")?.focus();
            return;
          }

          feedback.textContent = "Enviando pedido…";
          const serviceInput = document.querySelector('[name="booking-service"]:checked');
          const service = serviceInput && services[Number(serviceInput.value)];
          if (!service) {
            feedback.textContent = "Escolha um serviço antes de continuar.";
            return;
          }
          const data = Object.fromEntries(new FormData(form));
          data.serviceName = service.name;
          data.price = service.price;
          const result = await chamarAPI("agendamentos", { method: "POST", body: JSON.stringify(data) });
          currentBooking = result.booking;
          form.innerHTML = bookingPaymentCheckout(currentBooking);
          form.querySelector("#checkout-heading")?.focus();
        } catch (error) {
          feedback.textContent = error.message;
        } finally {
          const activeButton = form.querySelector('[type="submit"]');
          if (activeButton) activeButton.disabled = false;
        }
      });
      bookingForm.addEventListener("click", async event => {
        const button = event.target.closest("[data-cancel-order]");
        if (!button || !currentBooking) return;
        button.disabled = true;
        const feedback = document.getElementById("booking-feedback");
        try {
          const result = await chamarAPI(`agendamentos/${encodeURIComponent(currentBooking.id)}/status`, {
            method: "PATCH", body: JSON.stringify({ status: "cancelado" })
          });
          currentBooking = result.booking;
          bookingForm.innerHTML = `<div class="payment-success"><h3 class="checkout-title" id="checkout-heading" tabindex="-1">Pedido cancelado</h3><p>Nenhum valor foi cobrado. ${bookingPaymentSummary(currentBooking)}</p><p>Código do pedido: <strong>${escapeHtml(currentBooking.id)}</strong></p></div>`;
          bookingForm.querySelector("#checkout-heading")?.focus();
        } catch (error) {
          feedback.textContent = error.message;
          button.disabled = false;
        }
      });
    }
  } catch (error) {
    container.innerHTML = `<p class="error-message">${escapeHtml(error.message)}</p>`;
  }
}

const chatTime = iso => new Date(iso).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
const chatBubbles = (messages, mine) => messages.map(message => `<div class="msg ${message.from === mine ? "msg-mine" : "msg-theirs"}"><p>${escapeHtml(message.text)}</p><small>${chatTime(message.at)}</small></div>`).join("");

function initProfessionalChat(professional) {
  const body = document.getElementById("pro-chat-body");
  if (!body) return;
  const storageKey = `cb-chat-${professional.id}`;
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem(storageKey) || "null"); } catch { saved = null; }
  let timer;

  function renderStart() {
    body.innerHTML = `<p class="muted-copy">Tire dúvidas sobre serviços, valores ou horários antes de agendar.</p>
      <form class="chat-start" id="chat-start"><label>Seu nome<input name="customerName" required maxlength="100" autocomplete="name"></label>
      <label>Mensagem<textarea name="text" required maxlength="1000" rows="3" placeholder="Ex.: Olá! Você tem horário na sexta à tarde?"></textarea></label>
      <button class="button button-primary" type="submit">Enviar mensagem</button><p class="form-feedback" id="chat-feedback" aria-live="polite"></p></form>`;
    document.getElementById("chat-start").addEventListener("submit", async event => {
      event.preventDefault();
      const feedback = document.getElementById("chat-feedback");
      const form = event.currentTarget;
      try {
        const payload = { professionalId: professional.id, customerName: form.customerName.value, text: form.text.value };
        const { chat, token } = await chamarAPI("chats", { method: "POST", body: JSON.stringify(payload) });
        saved = { id: chat.id, token };
        localStorage.setItem(storageKey, JSON.stringify(saved));
        renderThread(chat);
      } catch (error) { feedback.textContent = error.message; }
    });
  }

  function renderThread(chat) {
    const stick = !document.getElementById("chat-log") || (() => { const log = document.getElementById("chat-log"); return log.scrollHeight - log.scrollTop - log.clientHeight < 40; })();
    if (!document.getElementById("chat-log")) {
      body.innerHTML = `<div class="chat-log" id="chat-log" role="log" aria-live="polite"></div>
        <form class="chat-reply" id="chat-reply"><input name="text" required maxlength="1000" placeholder="Escreva sua mensagem…" autocomplete="off"><button class="button button-primary" type="submit">Enviar</button></form>
        <p class="form-feedback" id="chat-feedback" aria-live="polite"></p>
        <p class="form-note">Conversa de ${escapeHtml(chat.customerName)}. <button type="button" class="text-button" id="chat-new">Iniciar nova conversa</button></p>`;
      document.getElementById("chat-reply").addEventListener("submit", async event => {
        event.preventDefault();
        const form = event.currentTarget;
        try {
          const { chat: updated } = await chamarAPI(`chats/${saved.id}/messages`, { method: "POST", body: JSON.stringify({ token: saved.token, text: form.text.value }) });
          form.reset();
          renderThread(updated);
        } catch (error) { document.getElementById("chat-feedback").textContent = error.message; }
      });
      document.getElementById("chat-new").addEventListener("click", () => { clearInterval(timer); localStorage.removeItem(storageKey); saved = null; renderStart(); });
    }
    const log = document.getElementById("chat-log");
    log.innerHTML = chatBubbles(chat.messages, "customer");
    if (stick) log.scrollTop = log.scrollHeight;
    clearInterval(timer);
    timer = setInterval(async () => {
      if (!document.getElementById("chat-log")) { clearInterval(timer); return; }
      try { renderThread((await chamarAPI(`chats/${saved.id}?token=${encodeURIComponent(saved.token)}`)).chat); } catch { /* tenta de novo no próximo ciclo */ }
    }, 5000);
  }

  if (saved) {
    chamarAPI(`chats/${saved.id}?token=${encodeURIComponent(saved.token)}`).then(({ chat }) => renderThread(chat)).catch(() => { localStorage.removeItem(storageKey); saved = null; renderStart(); });
  } else {
    renderStart();
  }
}

function renderReviews(reviews) {
  const list = document.getElementById("review-list");
  if (!list) return;
  list.innerHTML = reviews.length ? reviews.map(review => `<article class="review-card"><div class="rating">★ ${Number(review.rating).toFixed(1)}</div><p>${escapeHtml(review.comment)}</p><small>${escapeHtml(review.customerName)}</small></article>`).join("") : '<p class="muted-copy">Ainda não há avaliações para este perfil.</p>';
}

function initPartners() {
  const grid = document.getElementById("partner-grid");
  if (!grid) return;
  chamarAPI("partners").then(({ partners }) => {
    if (!partners.length) {
      grid.innerHTML = '<p class="muted-copy">Nenhuma parceira por aqui ainda. <a class="inline-link" href="/anunciar.html">Seja a primeira →</a></p>';
      return;
    }
    grid.innerHTML = partners.map(partner => {
      const initials = partner.name.split(/\s+/).slice(0, 2).map(part => part[0]).join("").toUpperCase();
      const tag = partner.demo ? "Demonstração" : partner.plan === "premium" ? "★ Premium" : partner.plan === "destaque" ? "Destaque" : "Parceira";
      const inner = `<span class="partner-logo" style="--partner:${escapeHtml(partner.color)}">${escapeHtml(initials)}</span><span class="partner-info"><small>${escapeHtml(tag)} · ${escapeHtml(partner.categoryLabel)}</small><strong>${escapeHtml(partner.name)}</strong><em>${escapeHtml(partner.description)}</em></span>`;
      return partner.url
        ? `<a class="partner-card ${escapeHtml(partner.plan)}" href="${escapeHtml(partner.url)}" target="_blank" rel="noopener sponsored" data-partner="${escapeHtml(partner.id)}">${inner}</a>`
        : `<a class="partner-card ${escapeHtml(partner.plan)}" href="/anunciar.html">${inner}</a>`;
    }).join("");
    grid.addEventListener("click", event => {
      const card = event.target.closest("[data-partner]");
      if (card) chamarAPI(`partners/${card.dataset.partner}/click`, { method: "POST", body: "{}", keepalive: true }).catch(() => {});
    });
  }).catch(error => { grid.innerHTML = `<p class="muted-copy">${escapeHtml(error.message)}</p>`; });
}

function initAdvertise() {
  const form = document.getElementById("advertise-form");
  if (!form) return;
  const feedback = document.getElementById("advertise-feedback");
  document.querySelectorAll('input[name="planPick"]').forEach(radio => radio.addEventListener("change", () => { form.elements.plan.value = radio.value; }));
  form.addEventListener("submit", async event => {
    event.preventDefault();
    feedback.textContent = "Publicando…";
    try {
      const body = JSON.stringify(Object.fromEntries(new FormData(form)));
      const { token } = await chamarAPI("partners", { method: "POST", body });
      document.getElementById("advertise-token").textContent = token;
      document.getElementById("advertise-success").hidden = false;
      document.getElementById("advertise-success").scrollIntoView({ behavior: "smooth" });
      form.reset();
      form.elements.plan.value = "destaque";
      feedback.textContent = "";
    } catch (error) {
      feedback.textContent = error.message;
    }
  });
  const statsForm = document.getElementById("stats-form");
  const result = document.getElementById("stats-result");
  let currentToken = "";
  function renderStats(stats) {
    const paused = stats.status !== "active";
    result.innerHTML = `<p><strong>${escapeHtml(stats.partner.name)}</strong> · plano ${escapeHtml(stats.partner.planLabel)} (${money(stats.planPrice)}/mês)</p><p class="stats-number">${Number(stats.clicks)} <small>${Number(stats.clicks) === 1 ? "clique" : "cliques"} no anúncio</small></p><p>Status: <b>${paused ? "pausado" : "no ar"}</b></p><button class="button button-outline" type="button" id="toggle-ad">${paused ? "Reativar anúncio" : "Pausar anúncio"}</button>`;
    document.getElementById("toggle-ad").addEventListener("click", async () => {
      try {
        await chamarAPI("partners/manage", { method: "POST", body: JSON.stringify({ token: currentToken, action: paused ? "activate" : "pause" }) });
        renderStats(await chamarAPI(`partners/stats?token=${encodeURIComponent(currentToken)}`));
      } catch (error) { result.textContent = error.message; }
    });
  }
  statsForm.addEventListener("submit", async event => {
    event.preventDefault();
    currentToken = statsForm.elements.token.value.trim();
    try {
      renderStats(await chamarAPI(`partners/stats?token=${encodeURIComponent(currentToken)}`));
    } catch (error) { result.textContent = error.message; }
  });
}

function initInspirationCarousel() {
  const carousel = document.getElementById("photo-carousel");
  if (!carousel) return;
  const slides = [...carousel.querySelectorAll(".inspiration-slide")];
  const dots = document.getElementById("carousel-dots");
  const position = document.getElementById("carousel-position");
  let activeIndex = 0;
  function show(index) {
    activeIndex = (index + slides.length) % slides.length;
    slides.forEach((slide, slideIndex) => {
      slide.classList.toggle("active", slideIndex === activeIndex);
      slide.setAttribute("aria-hidden", String(slideIndex !== activeIndex));
    });
    [...dots.children].forEach((dot, dotIndex) => {
      dot.classList.toggle("active", dotIndex === activeIndex);
      dot.setAttribute("aria-current", dotIndex === activeIndex ? "true" : "false");
    });
    position.textContent = `${String(activeIndex + 1).padStart(2, "0")} / ${String(slides.length).padStart(2, "0")}`;
  }
  slides.forEach((_, index) => {
    const dot = document.createElement("button");
    dot.type = "button";
    dot.className = "carousel-dot";
    dot.setAttribute("aria-label", `Mostrar dica ${index + 1}`);
    dot.addEventListener("click", () => show(index));
    dots.append(dot);
  });
  document.getElementById("carousel-prev").addEventListener("click", () => show(activeIndex - 1));
  document.getElementById("carousel-next").addEventListener("click", () => show(activeIndex + 1));
  show(0);
}

function initChatbot() {
  if (document.getElementById("beauty-chat")) return;
  const root = document.createElement("section");
  root.id = "beauty-chat";
  root.className = "chat-widget";
  root.innerHTML = `<button class="chat-launcher" type="button" aria-expanded="false" aria-controls="chat-panel"><span aria-hidden="true">✦</span> Ajuda</button>
    <div class="chat-panel" id="chat-panel" hidden>
      <div class="chat-header"><div><strong>Ajuda Click Beleza</strong><small>Como podemos ajudar?</small></div><button class="chat-close" type="button" aria-label="Fechar chat">×</button></div>
      <div class="chat-messages" id="chat-messages" aria-live="polite"><p class="chat-bot-message">Olá! Posso ajudar com buscas, agendamentos e cadastro profissional.</p></div>
      <div class="chat-suggestions"><button type="button">Quero agendar</button><button type="button">Sou profissional</button><button type="button">Buscar no Maps</button></div>
      <form class="chat-form"><label class="visually-hidden" for="chat-input">Digite sua pergunta</label><input id="chat-input" maxlength="300" placeholder="Como posso ajudar?" autocomplete="off"><button type="submit" aria-label="Enviar mensagem">↑</button></form>
      <small class="chat-disclaimer">Assistente automático de orientação; não confirma horários.</small>
    </div>`;
  document.body.append(root);
  const launcher = root.querySelector(".chat-launcher");
  const panel = root.querySelector(".chat-panel");
  const input = root.querySelector("#chat-input");
  const messages = root.querySelector("#chat-messages");
  function open(openPanel) {
    panel.hidden = !openPanel;
    launcher.setAttribute("aria-expanded", String(openPanel));
    if (openPanel) input.focus();
  }
  function addMessage(text, role, link) {
    const message = document.createElement("p");
    message.className = role === "user" ? "chat-user-message" : "chat-bot-message";
    message.textContent = text;
    if (link) {
      const anchor = document.createElement("a");
      anchor.href = link.href;
      anchor.textContent = link.label;
      anchor.className = "chat-action-link";
      if (link.external) {
        anchor.target = "_blank";
        anchor.rel = "noopener noreferrer";
      }
      message.append(document.createElement("br"), anchor);
    }
    messages.append(message);
    messages.scrollTop = messages.scrollHeight;
  }
  function respond(rawText) {
    const text = rawText.toLocaleLowerCase("pt-BR");
    const serviceEntry = Object.entries(SERVICE_LABELS).find(([key, label]) =>
      text.includes(key) || text.includes(label.toLocaleLowerCase("pt-BR"))
    );
    if (/profissional|cadastro|divulgar/.test(text)) {
      addMessage("Crie seu perfil com nome, categoria e cidade. Adicione serviços e fotos depois, se quiser.", "bot", { href: "/register.html", label: "Criar meu perfil" });
    } else if (/map|google|negócio|negocio/.test(text)) {
      addMessage("Esta busca abre resultados públicos do Google Maps. Eles não são verificados pelo Click Beleza.", "bot", {
        href: `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(`${serviceEntry ? SERVICE_LABELS[serviceEntry[0]] : "beleza"} Marília SP`)}`,
        label: "Abrir busca no Google Maps ↗", external: true
      });
    } else if (/agenda|agendar|horário|horario|marcar|reserva/.test(text)) {
      addMessage("Escolha o profissional, serviço, data e horário e envie o pedido. Em seguida, você verá uma etapa demonstrativa de Pix ou cartão, sem cobrança real. O horário só fica reservado após a confirmação do profissional.", "bot", { href: serviceEntry ? `/professionals.html?servico=${serviceEntry[0]}` : "/professionals.html", label: "Encontrar profissionais" });
    } else if (/foto|vídeo|video|portfólio|portfolio/.test(text)) {
      addMessage("Adicione até 8 fotos ou vídeos ao seu perfil. Você pode fazer isso agora ou depois.", "bot", { href: "/register.html", label: "Completar meu perfil" });
    } else if (/preço|preco|valor|perto|cidade|busca|buscar|encontr/.test(text) || serviceEntry) {
      addMessage("Busque por serviço e cidade. Ordene os resultados por distância, preço ou avaliação. A busca inicial é em Marília, SP.", "bot", { href: serviceEntry ? `/professionals.html?servico=${serviceEntry[0]}` : "/professionals.html", label: "Buscar profissionais" });
    } else {
      addMessage("Posso ajudar com buscas, agendamentos ou cadastro profissional. A busca inicial é em Marília, SP.", "bot");
    }
  }
  launcher.addEventListener("click", () => open(panel.hidden));
  root.querySelector(".chat-close").addEventListener("click", () => open(false));
  root.querySelector(".chat-suggestions").addEventListener("click", event => {
    const button = event.target.closest("button");
    if (!button) return;
    addMessage(button.textContent, "user");
    respond(button.textContent);
  });
  root.querySelector(".chat-form").addEventListener("submit", event => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    addMessage(text, "user");
    input.value = "";
    respond(text);
  });
  if (location.hash === "#beauty-chat") open(true);
}

async function initAgenda() {
  const container = document.getElementById("booking-list");
  if (!container) return;
  async function load() {
    container.innerHTML = '<p class="loading">Carregando pedidos…</p>';
    try {
      const { bookings } = await chamarAPI("agendamentos");
      container.innerHTML = bookings.length ? bookings.map(booking => `<article class="booking-row"><div><span class="status-pill status-${escapeHtml(booking.status)}">${escapeHtml(booking.status)}</span><h3>${escapeHtml(booking.customerName)} · ${escapeHtml(booking.serviceName)}</h3><p>${escapeHtml(booking.professionalName)} · ${escapeHtml(booking.date)} às ${escapeHtml(booking.time)} · ${money(booking.price)}</p><small>${booking.customerEmail ? escapeHtml(booking.customerEmail) + " · " : ""}${escapeHtml(booking.customerPhone)}</small>${bookingPaymentSummary(booking) ? `<small class="booking-payment">${escapeHtml(bookingPaymentSummary(booking))}</small>` : ""}</div><div class="booking-actions">${booking.status === "solicitado" ? `<button class="button button-small" data-status="confirmado" data-id="${escapeHtml(booking.id)}">Confirmar</button>` : ""}${booking.status === "confirmado" ? `<button class="button button-small" data-status="concluido" data-id="${escapeHtml(booking.id)}">Concluir</button>` : ""}${["solicitado", "confirmado"].includes(booking.status) ? `<button class="button button-outline button-small" data-status="cancelado" data-id="${escapeHtml(booking.id)}">Cancelar</button>` : ""}${booking.status === "concluido" && !booking.review ? `<button class="button button-small" data-review="${escapeHtml(booking.id)}">Avaliar</button>` : ""}</div></article>`).join("") : '<div class="empty-state"><h3>Nenhum pedido ainda</h3><p>Quando alguém solicitar um horário, ele aparecerá aqui.</p></div>';
    } catch (error) { container.innerHTML = `<p class="error-message">${escapeHtml(error.message)}</p>`; }
  }
  container.addEventListener("click", async event => {
    const button = event.target.closest("button");
    if (!button) return;
    try {
      if (button.dataset.status) {
        await chamarAPI(`agendamentos/${encodeURIComponent(button.dataset.id)}/status`, { method: "PATCH", body: JSON.stringify({ status: button.dataset.status }) });
      } else if (button.dataset.review) {
        const ratingValue = prompt("Sua nota de 1 a 5:");
        if (ratingValue === null) return;
        const rating = Number(ratingValue);
        if (!Number.isInteger(rating) || rating < 1 || rating > 5) { alert("Informe uma nota inteira entre 1 e 5."); return; }
        const comment = prompt("Conte um pouco sobre o atendimento:") || "";
        await chamarAPI(`agendamentos/${encodeURIComponent(button.dataset.review)}/review`, { method: "POST", body: JSON.stringify({ rating, comment }) });
      }
      await load();
    } catch (error) { alert(error.message); }
  });
  load();
}

function initChatInbox() {
  const box = document.getElementById("chat-inbox");
  if (!box) return;
  const drafts = {};
  let signature = "";
  async function load(force = false) {
    try {
      const { chats } = await chamarAPI("chats");
      const next = chats.map(chat => `${chat.id}:${chat.messages.length}`).join("|");
      if (!force && next === signature) return;
      signature = next;
      box.querySelectorAll("input[data-chat]").forEach(input => { drafts[input.dataset.chat] = input.value; });
      box.innerHTML = chats.length ? chats.map(chat => `<article class="chat-thread"><header><h3>${escapeHtml(chat.customerName)}</h3><small>para ${escapeHtml(chat.professionalName)} · ${chatTime(chat.updatedAt)}</small></header>
        <div class="chat-log">${chatBubbles(chat.messages, "professional")}</div>
        <form class="chat-reply" data-form="${escapeHtml(chat.id)}"><input data-chat="${escapeHtml(chat.id)}" name="text" required maxlength="1000" placeholder="Responder ${escapeHtml(chat.customerName)}…" autocomplete="off" value="${escapeHtml(drafts[chat.id] || "")}"><button class="button button-small" type="submit">Responder</button></form></article>`).join("")
        : '<div class="empty-state"><h3>Nenhuma conversa ainda</h3><p>Quando um cliente enviar uma mensagem pelo seu perfil, ela aparecerá aqui.</p></div>';
      box.querySelectorAll(".chat-log").forEach(log => { log.scrollTop = log.scrollHeight; });
    } catch (error) { box.innerHTML = `<p class="error-message">${escapeHtml(error.message)}</p>`; }
  }
  box.addEventListener("submit", async event => {
    event.preventDefault();
    const form = event.target.closest("form[data-form]");
    if (!form) return;
    try {
      await chamarAPI(`chats/${encodeURIComponent(form.dataset.form)}/reply`, { method: "POST", body: JSON.stringify({ text: form.text.value }) });
      delete drafts[form.dataset.form];
      await load(true);
    } catch (error) { alert(error.message); }
  });
  load(true);
  setInterval(() => load(), 5000);
}

initHome();
initProfessionals();
initRegister();
initProfessional();
initAgenda();
initChatInbox();
initInspirationCarousel();
initPartners();
initAdvertise();
initChatbot();
