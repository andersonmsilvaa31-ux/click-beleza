async function getCurrentAccount() {
  return (await chamarAPI("auth/me")).user;
}

function initLoginPage() {
  const form = document.getElementById("login-form");
  if (!form) return;
  const feedback = document.getElementById("login-feedback");
  getCurrentAccount().then(user => {
    if (user) location.replace(user.role === "admin" ? "/admin.html" : "/professional-dashboard.html");
  }).catch(error => {
    feedback.textContent = error.message;
  });
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const button = form.querySelector('[type="submit"]');
    button.disabled = true;
    feedback.textContent = "Verificando acesso…";
    try {
      const { user } = await chamarAPI("auth/login", {
        method: "POST",
        body: JSON.stringify({ email: form.email.value, password: form.password.value })
      });
      location.href = user.role === "admin" ? "/admin.html" : "/professional-dashboard.html";
    } catch (error) {
      feedback.textContent = error.message;
      button.disabled = false;
    }
  });
}

function initProfessionalDashboard() {
  const container = document.getElementById("professional-account");
  if (!container) return;
  getCurrentAccount().then(async user => {
    if (!user) {
      location.replace("/login.html");
      return;
    }
    if (user.role !== "professional" || !user.professionalId) {
      location.replace("/admin.html");
      return;
    }
    const { professional } = await chamarAPI(`profissionais/${encodeURIComponent(user.professionalId)}/editar`);
    const services = professional.services?.length
      ? professional.services.map(service => `${escapeHtml(service.name)} · ${money(service.price)}`).join("<br>")
      : "Adicione serviços e preços para apresentar sua oferta.";
    const paused = professional.status === "paused";
    container.innerHTML = `<div class="account-summary"><div><p class="eyebrow">${paused ? "PERFIL PAUSADO" : "PERFIL PUBLICADO"}</p><h2>${escapeHtml(professional.name)}</h2><p>${escapeHtml(SERVICE_LABELS[professional.service] || professional.service)} · ${escapeHtml(professional.city)}</p><p>Conta: ${escapeHtml(user.email)}</p></div><span class="admin-status ${paused ? "paused" : ""}">${escapeHtml(professional.profileCompletion)}% completo</span></div>
      <p>${escapeHtml(professional.description || "Adicione uma descrição para apresentar seu trabalho.")}</p>
      <p>${services}</p>${paused ? '<p class="form-note">Seu perfil está pausado e não aparece na busca pública. Fale com a administração para reativá-lo.</p>' : ""}
      <div class="account-actions"><a class="button button-primary" href="/register.html?id=${encodeURIComponent(professional.id)}">Editar meu perfil</a>${paused ? "" : `<a class="button button-outline" href="/professional.html?id=${encodeURIComponent(professional.id)}">Ver perfil público</a>`}<a class="button button-outline" href="/agenda.html">Meus pedidos e conversas</a></div>`;
  }).catch(error => {
    container.innerHTML = `<p class="error-message">${escapeHtml(error.message)}</p>`;
  });
}

function initAdminDashboard() {
  const root = document.querySelector(".admin-shell");
  if (!root) return;
  const feedback = document.getElementById("admin-feedback");
  const stats = document.getElementById("admin-stats");
  const profileList = document.getElementById("admin-professional-list");
  const bookingList = document.getElementById("admin-booking-list");
  const chatList = document.getElementById("admin-chat-list");
  const partnerList = document.getElementById("admin-partner-list");
  const userList = document.getElementById("admin-user-list");
  const refreshButton = document.getElementById("admin-refresh");
  let overview;

  function render() {
    stats.innerHTML = [
      ["Profissionais", overview.professionals.length],
      ["Agendamentos", overview.bookings.length],
      ["Conversas", overview.chats.length],
      ["Anúncios", overview.partners.length],
      ["Contas", overview.users.length]
    ].map(([label, count]) => `<div class="admin-stat"><strong>${escapeHtml(count)}</strong><span>${escapeHtml(label)}</span></div>`).join("");
    profileList.innerHTML = overview.professionals.length ? overview.professionals.map(item => `<article class="admin-row"><div><h3>${escapeHtml(item.name)}${item.demo ? " · Demonstração" : ""}</h3><p>${escapeHtml(SERVICE_LABELS[item.service] || item.service)} · ${escapeHtml(item.city)} · ${escapeHtml(item.email || "sem e-mail")}</p><small class="admin-status ${item.status === "paused" ? "paused" : ""}">${item.status === "paused" ? "Pausado" : "Ativo"}</small></div><div class="admin-row-actions">${!item.demo ? `<a class="button button-outline button-small" href="/register.html?id=${encodeURIComponent(item.id)}">Editar</a>` : ""}<button class="button button-small" data-profile-id="${escapeHtml(item.id)}" data-profile-status="${item.status === "paused" ? "active" : "paused"}">${item.status === "paused" ? "Ativar" : "Pausar"}</button></div></article>`).join("") : '<p class="empty-state">Nenhum profissional cadastrado.</p>';
    bookingList.innerHTML = overview.bookings.length ? overview.bookings.map(item => `<article class="admin-row"><div><h3>${escapeHtml(item.customerName)} · ${escapeHtml(item.serviceName)}</h3><p>${escapeHtml(item.professionalName)} · ${escapeHtml(item.date)} às ${escapeHtml(item.time)} · ${money(item.price)}</p><small>${escapeHtml(item.customerEmail || "")} ${escapeHtml(item.customerPhone || "")}</small><small>${escapeHtml(bookingPaymentSummary(item))}</small><span class="admin-status ${escapeHtml(item.status)}">${escapeHtml(item.status)}</span></div><div class="admin-row-actions">${item.status === "solicitado" ? `<button class="button button-small" data-booking-id="${escapeHtml(item.id)}" data-booking-status="confirmado">Confirmar</button>` : ""}${item.status === "confirmado" ? `<button class="button button-small" data-booking-id="${escapeHtml(item.id)}" data-booking-status="concluido">Concluir</button>` : ""}${["solicitado", "confirmado"].includes(item.status) ? `<button class="button button-outline button-small" data-booking-id="${escapeHtml(item.id)}" data-booking-status="cancelado">Cancelar</button>` : ""}</div></article>`).join("") : '<p class="empty-state">Nenhum agendamento.</p>';
    chatList.innerHTML = overview.chats.length ? overview.chats.map(chat => `<article class="admin-row admin-chat-row"><div><h3>${escapeHtml(chat.customerName)} · ${escapeHtml(chat.professionalName)}</h3><div class="chat-log">${chatBubbles(chat.messages, "professional")}</div><form class="chat-reply" data-admin-chat="${escapeHtml(chat.id)}"><input name="text" required maxlength="1000" placeholder="Responder conversa"><button class="button button-small" type="submit">Responder</button></form></div></article>`).join("") : '<p class="empty-state">Nenhuma conversa.</p>';
    partnerList.innerHTML = overview.partners.length ? overview.partners.map(item => `<article class="admin-row"><div><h3>${escapeHtml(item.name)}</h3><p>${escapeHtml(item.category)} · ${escapeHtml(item.plan)} · ${escapeHtml(item.email || "")} · ${escapeHtml(item.phone || "")}</p><small>${escapeHtml(item.clicks || 0)} cliques · <span class="admin-status ${item.status === "paused" ? "paused" : ""}">${escapeHtml(item.status)}</span></small></div><div class="admin-row-actions"><button class="button button-small" data-partner-id="${escapeHtml(item.id)}" data-partner-action="${item.status === "paused" ? "activate" : "pause"}">${item.status === "paused" ? "Ativar" : "Pausar"}</button></div></article>`).join("") : '<p class="empty-state">Nenhum anúncio.</p>';
    userList.innerHTML = overview.users.length ? overview.users.map(item => `<article class="admin-row"><div><h3>${escapeHtml(item.email)}</h3><p>${item.role === "admin" ? "Administrador" : "Profissional"} · ${item.active ? "Ativa" : "Desativada"}</p></div>${item.role === "professional" ? `<div class="admin-row-actions"><button class="button button-small" data-user-id="${escapeHtml(item.id)}" data-user-active="${String(!item.active)}">${item.active ? "Desativar conta" : "Reativar conta"}</button></div>` : ""}</article>`).join("") : '<p class="empty-state">Nenhuma conta.</p>';
  }

  async function load() {
    refreshButton.disabled = true;
    try {
      overview = await chamarAPI("admin/overview");
      render();
    } catch (error) {
      if (/Entre na sua conta|permissão/i.test(error.message)) {
        location.replace("/login.html");
        return;
      }
      feedback.textContent = error.message;
    } finally {
      refreshButton.disabled = false;
    }
  }

  getCurrentAccount().then(user => {
    if (!user) {
      location.replace("/login.html");
      return;
    }
    if (user.role !== "admin") {
      location.replace("/professional-dashboard.html");
      return;
    }
    load();
  }).catch(error => {
    feedback.textContent = error.message;
  });
  refreshButton.addEventListener("click", load);

  root.addEventListener("click", async event => {
    const button = event.target.closest("button");
    if (!button) return;
    try {
      if (button.dataset.bookingId) {
        await chamarAPI(`agendamentos/${encodeURIComponent(button.dataset.bookingId)}/status`, { method: "PATCH", body: JSON.stringify({ status: button.dataset.bookingStatus }) });
      } else if (button.dataset.profileId) {
        await chamarAPI(`admin/professionals/${encodeURIComponent(button.dataset.profileId)}/status`, { method: "PATCH", body: JSON.stringify({ status: button.dataset.profileStatus }) });
      } else if (button.dataset.partnerId) {
        await chamarAPI("partners/manage", { method: "POST", body: JSON.stringify({ partnerId: button.dataset.partnerId, action: button.dataset.partnerAction }) });
      } else if (button.dataset.userId) {
        await chamarAPI(`admin/users/${encodeURIComponent(button.dataset.userId)}/status`, { method: "PATCH", body: JSON.stringify({ active: button.dataset.userActive === "true" }) });
      } else {
        return;
      }
      feedback.textContent = "Alteração salva.";
      await load();
    } catch (error) {
      feedback.textContent = error.message;
    }
  });
  root.addEventListener("submit", async event => {
    const form = event.target.closest("form[data-admin-chat]");
    if (!form) return;
    event.preventDefault();
    try {
      await chamarAPI(`chats/${encodeURIComponent(form.dataset.adminChat)}/reply`, { method: "POST", body: JSON.stringify({ text: form.text.value }) });
      feedback.textContent = "Resposta enviada.";
      await load();
    } catch (error) {
      feedback.textContent = error.message;
    }
  });
}

initLoginPage();
initProfessionalDashboard();
initAdminDashboard();
