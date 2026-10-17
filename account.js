async function getCurrentAccount() {
  return (await chamarAPI("auth/me")).user;
}

function initLoginPage() {
  const form = document.getElementById("login-form");
  if (!form) return;
  const feedback = document.getElementById("login-feedback");
  try {
    const identifier = localStorage.getItem("click-beleza-login-identifier");
    if (identifier) {
      form.elements.identifier.value = identifier;
      form.elements.rememberIdentifier.checked = true;
    }
  } catch (error) {
    console.error("Não foi possível recuperar o identificador salvo:", error);
  }
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
        body: JSON.stringify({ identifier: form.identifier.value, password: form.password.value })
      });
      try {
        if (form.elements.rememberIdentifier.checked) {
          localStorage.setItem("click-beleza-login-identifier", form.identifier.value.trim());
        } else {
          localStorage.removeItem("click-beleza-login-identifier");
        }
      } catch (error) {
        console.error("Não foi possível salvar o identificador de acesso:", error);
      }
      location.href = user.role === "admin" ? "/admin.html" : "/professional-dashboard.html";
    } catch (error) {
      feedback.textContent = error.message;
      button.disabled = false;
    }
  });
}

function initCustomerOrdersPage() {
  const form = document.getElementById("customer-order-form");
  if (!form) return;
  const resultBox = document.getElementById("customer-order-result");
  const feedback = document.getElementById("customer-order-feedback");
  let booking = null;
  let customerToken = "";

  function render() {
    const date = new Date(`${booking.date}T12:00:00`).toLocaleDateString("pt-BR");
    const canCancel = ["solicitado", "confirmado"].includes(booking.status);
    const canPay = ["demo_pending", "pending", "failed"].includes(booking.paymentStatus) && canCancel;
    const real = booking.paymentMode === "mercadopago";
    const paymentLabels = { paid: "Pago", pending: "Aguardando pagamento", failed: "Pagamento recusado", refunded: "Estornado", refund_failed: "Estorno em análise", demo_completed: "Etapa simulada", demo_pending: "Etapa demonstrativa pendente" };
    resultBox.innerHTML = `<div class="admin-section-heading"><div><p class="eyebrow">PEDIDO ${escapeHtml(booking.id)}</p><h2>${escapeHtml(booking.serviceName)}</h2></div><span class="admin-status ${booking.status === "cancelado" ? "cancelado" : ""}">${escapeHtml(booking.status)}</span></div>
      <div class="customer-order-details"><p><span>Profissional</span><strong>${escapeHtml(booking.professionalName)}</strong></p><p><span>Data e horário</span><strong>${date} às ${escapeHtml(booking.time)}</strong></p><p><span>${real ? "Valor" : "Valor demonstrativo"}</span><strong>${money(booking.price)}</strong></p><p><span>Pagamento</span><strong>${escapeHtml(paymentLabels[booking.paymentStatus] || "Não informado")}</strong></p></div>
      <p class="customer-order-note">${escapeHtml(booking.status === "cancelado" ? (booking.paymentStatus === "refunded" ? "Pedido cancelado e pagamento estornado." : "Este pedido foi cancelado.") : booking.status === "concluido" ? booking.review ? "Avaliação registrada. Obrigado pelo retorno!" : "Atendimento concluído. Você pode deixar uma avaliação." : real ? "O pedido é confirmado após a aprovação do pagamento." : "Nenhuma cobrança real foi realizada; este é um pedido demonstrativo.")}</p>
      <div class="account-actions">${canPay ? `<button class="button button-primary" type="button" data-customer-action="pay">${real ? "Pagar com Mercado Pago" : "Concluir etapa demonstrativa"}</button>` : ""}${canCancel ? '<button class="button button-outline" type="button" data-customer-action="cancel">Cancelar meu pedido</button>' : ""}${booking.status === "concluido" && !booking.review ? `<form class="customer-review-form"><label>Sua nota<select name="rating" required><option value="">Selecione</option><option value="5">5 · Excelente</option><option value="4">4 · Muito bom</option><option value="3">3 · Bom</option><option value="2">2 · Regular</option><option value="1">1 · Precisa melhorar</option></select></label><label>Comentário (opcional)<input name="comment" maxlength="500" placeholder="Conte como foi o atendimento"></label><button class="button button-primary" type="submit">Enviar avaliação</button></form>` : ""}</div>`;
    resultBox.hidden = false;
  }

  async function loadOrder() {
    const bookingId = form.elements.bookingId.value.trim();
    if (!bookingId || !customerToken) return;
    feedback.textContent = "Consultando seu pedido…";
    try {
      ({ booking } = await chamarAPI(`bookings/${encodeURIComponent(bookingId)}/access`, {
        method: "POST",
        body: JSON.stringify({ customerToken })
      }));
      feedback.textContent = "";
      render();
    } catch (error) {
      booking = null;
      resultBox.hidden = true;
      feedback.textContent = error.message;
    }
  }

  form.addEventListener("submit", async event => {
    event.preventDefault();
    customerToken = form.elements.customerToken.value.trim();
    await loadOrder();
  });
  try {
    const saved = JSON.parse(sessionStorage.getItem("cb-order") || "null");
    const wanted = new URLSearchParams(window.location.search).get("pedido");
    if (saved && saved.id === wanted) {
      form.elements.bookingId.value = saved.id;
      form.elements.customerToken.value = saved.token;
      customerToken = saved.token;
      loadOrder();
    }
  } catch (error) {
    sessionStorage.removeItem("cb-order");
  }
  resultBox.addEventListener("click", async event => {
    const button = event.target.closest("button[data-customer-action]");
    if (!button || !booking) return;
    button.disabled = true;
    try {
      if (button.dataset.customerAction === "cancel") {
        ({ booking } = await chamarAPI(`bookings/${encodeURIComponent(booking.id)}/status`, {
          method: "PATCH",
          body: JSON.stringify({ status: "cancelado", customerToken })
        }));
      } else if (booking.paymentMode === "mercadopago") {
        const { checkoutUrl } = await chamarAPI(`bookings/${encodeURIComponent(booking.id)}/checkout`, {
          method: "POST",
          body: JSON.stringify({ customerToken })
        });
        window.location.assign(checkoutUrl);
        return;
      } else {
        ({ booking } = await chamarAPI(`bookings/${encodeURIComponent(booking.id)}/payment`, {
          method: "POST",
          body: JSON.stringify({ paymentMethod: "pix", customerToken })
        }));
      }
      feedback.textContent = "";
      render();
    } catch (error) {
      feedback.textContent = error.message;
      button.disabled = false;
    }
  });
  resultBox.addEventListener("submit", async event => {
    const reviewForm = event.target.closest(".customer-review-form");
    if (!reviewForm || !booking) return;
    event.preventDefault();
    const button = reviewForm.querySelector('[type="submit"]');
    button.disabled = true;
    try {
      await chamarAPI(`bookings/${encodeURIComponent(booking.id)}/review`, {
        method: "POST",
        body: JSON.stringify({
          rating: Number(reviewForm.elements.rating.value),
          comment: reviewForm.elements.comment.value,
          customerToken
        })
      });
      booking.review = true;
      feedback.textContent = "";
      render();
    } catch (error) {
      feedback.textContent = error.message;
      button.disabled = false;
    }
  });
}

function initProfessionalDashboard() {
  const container = document.getElementById("professional-account");
  if (!container) return;
  const work = document.getElementById("professional-work");
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
    const [{ bookings }, { chats }, { transactions }] = await Promise.all([
      chamarAPI("bookings"),
      chamarAPI("chats"),
      chamarAPI("professional/transactions")
    ]);
    const services = professional.services?.length
      ? professional.services.map(service => `${escapeHtml(service.name)} · ${money(service.price)}`).join("<br>")
      : "Adicione serviços e preços para apresentar sua oferta.";
    const paused = professional.status === "paused";
    container.innerHTML = `<div class="account-summary"><div><p class="eyebrow">${paused ? "PERFIL PAUSADO" : "PERFIL PUBLICADO"}</p><h2>${escapeHtml(professional.name)}</h2><p>${escapeHtml(SERVICE_LABELS[professional.service] || professional.service)} · ${escapeHtml(professional.city)}</p><p>Conta: ${escapeHtml(user.email)}</p></div><span class="admin-status ${paused ? "paused" : ""}">${escapeHtml(professional.profileCompletion)}% completo</span></div>
      <p>${escapeHtml(professional.description || "Adicione uma descrição para apresentar seu trabalho.")}</p>
      <p>${services}</p>${paused ? '<p class="form-note">Seu perfil está pausado e não aparece na busca pública. Fale com a administração para reativá-lo.</p>' : ""}
      <p class="form-note">Portfólio: ${professional.portfolio?.length || 0} de 8 fotos/vídeos · até 18 MB no total.</p>
      <div class="account-actions"><a class="button button-primary" href="/register.html?id=${encodeURIComponent(professional.id)}#portfolio-section">Editar perfil e portfólio</a>${paused ? "" : `<a class="button button-outline" href="/professional.html?id=${encodeURIComponent(professional.id)}">Ver perfil público</a>`}<a class="button button-outline" href="/agenda.html#booking-list">Meus pedidos</a><a class="button button-outline" href="/agenda.html#conversations">Minhas conversas</a><a class="button button-outline" href="#professional-finance">Entradas e saídas</a></div>`;
    const clients = new Map();
    for (const booking of bookings) {
      const key = booking.customerName.trim().toLocaleLowerCase("pt-BR");
      const client = clients.get(key) || {
        name: booking.customerName,
        email: booking.customerEmail || "",
        phone: booking.customerPhone || "",
        bookings: 0,
        conversations: 0
      };
      client.bookings += 1;
      clients.set(key, client);
    }
    for (const chat of chats) {
      const key = chat.customerName.trim().toLocaleLowerCase("pt-BR");
      const client = clients.get(key) || {
        name: chat.customerName,
        email: "",
        phone: "",
        bookings: 0,
        conversations: 0
      };
      client.conversations += 1;
      clients.set(key, client);
    }
    work.innerHTML = `<div class="admin-section-heading"><div><p class="eyebrow">SUA OPERAÇÃO</p><h2>Pedidos e clientes</h2></div><a class="button button-outline button-small" href="/agenda.html">Abrir agenda completa</a></div>
      <div class="admin-stat-grid professional-stat-grid"><div class="admin-stat"><strong>${bookings.length}</strong><span>Seus pedidos</span></div><div class="admin-stat"><strong>${chats.length}</strong><span>Suas conversas</span></div><div class="admin-stat"><strong>${clients.size}</strong><span>Seus clientes</span></div></div>
      <div class="professional-client-list">${clients.size ? [...clients.values()].map(client => `<article class="admin-row"><div><h3>${escapeHtml(client.name)}</h3><p>${escapeHtml([client.email, client.phone].filter(Boolean).join(" · ") || "Contato pelo perfil")}</p></div><div class="client-activity"><span>${client.bookings} ${client.bookings === 1 ? "pedido" : "pedidos"}</span><span>${client.conversations} ${client.conversations === 1 ? "conversa" : "conversas"}</span></div></article>`).join("") : '<p class="empty-state">Seus clientes aparecerão aqui quando enviarem um pedido ou iniciarem uma conversa.</p>'}</div>`;
    const paymentsBox = document.getElementById("professional-payments");
    async function renderPayments() {
      if (!paymentsBox) return;
      try {
        const info = await chamarAPI("professional/payments");
        const params = new URLSearchParams(location.search).get("mp");
        const notice = params === "ok" ? "Conta conectada com sucesso." : params === "erro" ? "Não foi possível concluir a conexão. Tente novamente." : "";
        let body;
        if (!info.gatewayEnabled) {
          body = '<p class="form-note">O pagamento online ainda não está ativo na plataforma. Seus pedidos seguem no modo demonstrativo.</p>';
        } else if (info.connected) {
          body = `<p>Conta do Mercado Pago <strong>conectada</strong>${info.connectedAt ? ` em ${new Date(info.connectedAt).toLocaleDateString("pt-BR")}` : ""}. Os pagamentos dos seus clientes caem direto na sua conta; o Click Beleza retém ${info.feePercent}% de comissão.</p>
            <div class="account-actions"><button class="button button-outline" type="button" id="mp-disconnect">Desconectar</button></div>`;
        } else if (info.connectionAvailable) {
          body = `<p>Conecte sua conta do Mercado Pago para receber os pagamentos diretamente, com a comissão de ${info.feePercent}% descontada automaticamente. Você autoriza no site do Mercado Pago; nós nunca vemos sua senha.</p>
            <div class="account-actions"><button class="button button-primary" type="button" id="mp-connect">Conectar Mercado Pago</button></div>`;
        } else {
          body = '<p class="form-note">A conexão de contas ainda não está disponível neste ambiente.</p>';
        }
        paymentsBox.innerHTML = `<div class="admin-section-heading"><div><p class="eyebrow">RECEBIMENTOS</p><h2>Receber pelo Mercado Pago</h2></div></div>${notice ? `<p class="form-feedback" role="status">${escapeHtml(notice)}</p>` : ""}${body}<p class="form-feedback" id="mp-feedback" aria-live="polite"></p>`;
        paymentsBox.querySelector("#mp-connect")?.addEventListener("click", async event => {
          event.currentTarget.disabled = true;
          try {
            const { url } = await chamarAPI("professional/payments/connect", { method: "POST", body: JSON.stringify({}) });
            location.assign(url);
          } catch (error) {
            paymentsBox.querySelector("#mp-feedback").textContent = error.message;
            event.currentTarget.disabled = false;
          }
        });
        paymentsBox.querySelector("#mp-disconnect")?.addEventListener("click", async () => {
          if (!confirm("Desconectar sua conta do Mercado Pago? Novos pedidos passarão a entrar na conta da plataforma.")) return;
          await chamarAPI("professional/payments/disconnect", { method: "POST", body: JSON.stringify({}) });
          history.replaceState(null, "", location.pathname);
          renderPayments();
        });
      } catch (error) {
        paymentsBox.innerHTML = `<p class="form-feedback">${escapeHtml(error.message)}</p>`;
      }
    }
    renderPayments();
    const finance = document.getElementById("professional-finance");
    function renderFinance() {
      const income = transactions.filter(item => item.type === "entrada").reduce((sum, item) => sum + Number(item.amount), 0);
      const expenses = transactions.filter(item => item.type === "saida").reduce((sum, item) => sum + Number(item.amount), 0);
      const dateValue = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
      finance.innerHTML = `<div class="admin-section-heading"><div><p class="eyebrow">CONTROLE MANUAL</p><h2>Entradas e saídas</h2></div></div>
        <p class="finance-disclaimer">Registre seus recebimentos e despesas manualmente. Este livro-caixa não confirma pagamentos, não cobra clientes e não movimenta dinheiro.</p>
        <div class="admin-stat-grid professional-stat-grid finance-stat-grid"><div class="admin-stat"><strong>${money(income)}</strong><span>Entradas registradas</span></div><div class="admin-stat"><strong>${money(expenses)}</strong><span>Saídas registradas</span></div><div class="admin-stat"><strong>${money(income - expenses)}</strong><span>Saldo registrado</span></div></div>
        <form class="finance-entry-form" id="professional-finance-form">
          <label>Tipo<select name="type" required><option value="entrada">Entrada</option><option value="saida">Saída / despesa</option></select></label>
          <label>Valor (R$)<input name="amount" type="number" min="0.01" max="1000000" step="0.01" required></label>
          <label>Data<input name="date" type="date" value="${dateValue}" required></label>
          <label class="finance-description">Descrição<input name="description" maxlength="160" required placeholder="Ex.: atendimento ou compra de materiais"></label>
          <button class="button button-primary" type="submit">Registrar lançamento</button>
          <p class="form-feedback finance-feedback" id="finance-feedback" aria-live="polite"></p>
        </form>
        <div class="finance-ledger"><h3>Histórico manual</h3>${transactions.length ? `<div class="finance-ledger-list">${transactions.map(item => `<article class="finance-row"><span class="finance-type ${item.type === "entrada" ? "finance-income" : "finance-expense"}">${item.type === "entrada" ? "Entrada" : "Saída"}</span><div><strong>${escapeHtml(item.description)}</strong><small>${new Date(`${item.date}T12:00:00`).toLocaleDateString("pt-BR")} · registro manual</small></div><strong>${item.type === "entrada" ? "+" : "−"} ${money(item.amount)}</strong></article>`).join("")}</div>` : '<p class="empty-state">Nenhum lançamento registrado.</p>'}</div>`;
      finance.querySelector("#professional-finance-form").addEventListener("submit", async event => {
        event.preventDefault();
        const form = event.currentTarget;
        const button = form.querySelector('[type="submit"]');
        const feedback = finance.querySelector("#finance-feedback");
        button.disabled = true;
        feedback.textContent = "Salvando lançamento…";
        try {
          const { transaction } = await chamarAPI("professional/transactions", {
            method: "POST",
            body: JSON.stringify({
              type: form.elements.type.value,
              amount: form.elements.amount.value,
              date: form.elements.date.value,
              description: form.elements.description.value
            })
          });
          transactions.unshift(transaction);
          renderFinance();
          finance.querySelector("#finance-feedback").textContent = "Lançamento manual registrado.";
        } catch (error) {
          feedback.textContent = error.message;
          button.disabled = false;
        }
      });
    }
    renderFinance();
  }).catch(error => {
    container.innerHTML = `<p class="error-message">${escapeHtml(error.message)}</p>`;
    work.innerHTML = `<p class="error-message">${escapeHtml(error.message)}</p>`;
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
  const clientList = document.getElementById("admin-client-list");
  const privacyList = document.getElementById("admin-privacy-list");
  const refreshButton = document.getElementById("admin-refresh");
  const financeBody = document.getElementById("admin-finance-body");
  const tabLinks = [...document.querySelectorAll(".admin-tabs a")];
  const tabSections = [...root.querySelectorAll(".admin-section")];
  const accessGrid = root.querySelector(".access-level-grid");
  let overview;

  function showTab() {
    const wanted = location.hash.slice(1);
    const active = tabSections.some(section => section.id === wanted) ? wanted : "admin-overview";
    tabSections.forEach(section => { section.hidden = section.id !== active; });
    if (accessGrid) accessGrid.hidden = active !== "admin-overview";
    tabLinks.forEach(link => {
      const current = link.getAttribute("href") === `#${active}`;
      link.classList.toggle("active", current);
      link.setAttribute("role", "tab");
      link.setAttribute("aria-selected", String(current));
    });
  }
  window.addEventListener("hashchange", showTab);
  showTab();

  function renderFinance() {
    const f = overview.finance;
    const card = (value, label, hint = "") => `<div class="admin-stat"><strong>${value}</strong><span>${escapeHtml(label)}</span>${hint ? `<small>${escapeHtml(hint)}</small>` : ""}</div>`;
    const demoNote = f.onlinePayments ? "" : '<p class="admin-section-note">O pagamento online ainda não está ativo: os valores de "Pagamentos confirmados" ficam zerados e a comissão abaixo é apenas projetada sobre pedidos concluídos.</p>';
    const rows = f.professionals.map(p => `<article class="admin-row"><div><h3>${escapeHtml(p.name)}${p.demo ? " · Demonstração" : ""}</h3><p>${p.orders} pedidos · ${p.completed} concluídos · ${money(p.grossCompleted)} em serviços concluídos</p><small>Pago online: ${money(p.paidGross)} · Comissão retida: ${money(p.platformFee)} · Caixa manual: +${money(p.manualIn)} / −${money(p.manualOut)}</small></div></article>`).join("");
    financeBody.innerHTML = `${demoNote}
      <h3 class="finance-block-title">Receita da plataforma</h3>
      <div class="admin-stat-grid finance-grid">${card(money(f.platformRevenue), "Receita total", "comissões + anúncios mensais")}${card(money(f.payments.platformFee), "Comissões retidas", `${f.feePercent}% sobre pagamentos confirmados`)}${card(money(f.ads.monthlyRevenue), "Anúncios (mensal)", `${f.ads.active} ativos · ${f.ads.clicks} cliques`)}${card(money(f.projectedFee), "Comissão projetada", "sobre pedidos concluídos")}</div>
      <h3 class="finance-block-title">Pedidos e valores</h3>
      <div class="admin-stat-grid finance-grid">${card(money(f.orders.completedValue), "Concluídos", `${f.orders.completed} pedidos`)}${card(money(f.orders.openValue), "Em aberto", `${f.orders.open} pedidos`)}${card(money(f.orders.cancelledValue), "Cancelados", `${f.orders.cancelled} pedidos`)}${card(String(f.orders.total), "Total de pedidos")}</div>
      <h3 class="finance-block-title">Pagamentos online</h3>
      <div class="admin-stat-grid finance-grid">${card(money(f.payments.paidGross), "Recebido (bruto)", `${f.payments.paidCount} pagamentos`)}${card(money(f.payments.professionalNet), "Líquido dos profissionais")}${card(money(f.payments.refundedValue), "Estornado", `${f.payments.refundedCount} estornos`)}${card(String(f.payments.pendingCount), "Aguardando pagamento")}</div>
      <h3 class="finance-block-title">Caixa manual dos profissionais</h3>
      <div class="admin-stat-grid finance-grid">${card(money(f.manual.in), "Entradas lançadas")}${card(money(f.manual.out), "Saídas lançadas")}${card(money(f.manual.balance), "Saldo lançado")}</div>
      <h3 class="finance-block-title">Por profissional</h3>
      <div class="admin-list">${rows || '<p class="empty-state">Sem profissionais.</p>'}</div>`;
  }

  function render() {
    renderFinance();
    stats.innerHTML = [
      ["Profissionais", overview.professionals.length],
      ["Agendamentos", overview.bookings.length],
      ["Conversas", overview.chats.length],
      ["Clientes", overview.clients.length],
      ["Anúncios", overview.partners.length],
      ["Contas", overview.users.length]
    ].map(([label, count]) => `<div class="admin-stat"><strong>${escapeHtml(count)}</strong><span>${escapeHtml(label)}</span></div>`).join("");
    profileList.innerHTML = overview.professionals.length ? overview.professionals.map(item => `<article class="admin-row"><div><h3>${escapeHtml(item.name)}${item.demo ? " · Demonstração" : ""}</h3><p>${escapeHtml(SERVICE_LABELS[item.service] || item.service)} · ${escapeHtml(item.city)} · ${escapeHtml(item.email || "sem e-mail")}</p><small class="admin-status ${item.status === "paused" ? "paused" : ""}">${item.status === "paused" ? "Pausado" : "Ativo"}</small></div><div class="admin-row-actions">${!item.demo ? `<a class="button button-outline button-small" href="/register.html?id=${encodeURIComponent(item.id)}">Editar</a>` : ""}<button class="button button-small" data-profile-id="${escapeHtml(item.id)}" data-profile-status="${item.status === "paused" ? "active" : "paused"}">${item.status === "paused" ? "Ativar" : "Pausar"}</button></div></article>`).join("") : '<p class="empty-state">Nenhum profissional cadastrado.</p>';
    bookingList.innerHTML = overview.bookings.length ? overview.bookings.map(item => `<article class="admin-row"><div><h3>${escapeHtml(item.customerName)} · ${escapeHtml(item.serviceName)}</h3><p>${escapeHtml(item.professionalName)} · ${escapeHtml(item.date)} às ${escapeHtml(item.time)} · ${money(item.price)}</p><small>${escapeHtml(item.customerEmail || "")} ${escapeHtml(item.customerPhone || "")}</small><small>${escapeHtml(bookingPaymentSummary(item))}</small><span class="admin-status ${escapeHtml(item.status)}">${escapeHtml(item.status)}</span></div><div class="admin-row-actions">${item.status === "solicitado" ? `<button class="button button-small" data-booking-id="${escapeHtml(item.id)}" data-booking-status="confirmado">Confirmar</button>` : ""}${item.status === "confirmado" ? `<button class="button button-small" data-booking-id="${escapeHtml(item.id)}" data-booking-status="concluido">Concluir</button>` : ""}${["solicitado", "confirmado"].includes(item.status) ? `<button class="button button-outline button-small" data-booking-id="${escapeHtml(item.id)}" data-booking-status="cancelado">Cancelar</button>` : ""}</div></article>`).join("") : '<p class="empty-state">Nenhum agendamento.</p>';
    chatList.innerHTML = overview.chats.length ? overview.chats.map(chat => `<article class="admin-row admin-chat-row"><div><h3>${escapeHtml(chat.customerName)} · ${escapeHtml(chat.professionalName)}</h3><div class="chat-log">${chatBubbles(chat.messages, "professional")}</div><form class="chat-reply" data-admin-chat="${escapeHtml(chat.id)}"><input name="text" required maxlength="1000" placeholder="Responder conversa"><button class="button button-small" type="submit">Responder</button></form></div></article>`).join("") : '<p class="empty-state">Nenhuma conversa.</p>';
    clientList.innerHTML = overview.clients.length ? overview.clients.map(client => `<article class="admin-row"><div><h3>${escapeHtml(client.name || "Cliente")}</h3><p>${escapeHtml([client.email, client.phone].filter(Boolean).join(" · ") || "Contato registrado em conversa")}</p></div><div class="client-activity"><span>${client.bookings} ${client.bookings === 1 ? "pedido" : "pedidos"}</span><span>${client.conversations} ${client.conversations === 1 ? "conversa" : "conversas"}</span></div></article>`).join("") : '<p class="empty-state">Os clientes aparecerão quando houver pedidos ou conversas.</p>';
    partnerList.innerHTML = overview.partners.length ? overview.partners.map(item => `<article class="admin-row"><div><h3>${escapeHtml(item.name)}</h3><p>${escapeHtml(item.category)} · ${escapeHtml(item.plan)} · ${escapeHtml(item.email || "")} · ${escapeHtml(item.phone || "")}</p><small>${escapeHtml(item.clicks || 0)} cliques · <span class="admin-status ${item.status === "paused" ? "paused" : ""}">${escapeHtml(item.status)}</span></small></div><div class="admin-row-actions"><button class="button button-small" data-partner-id="${escapeHtml(item.id)}" data-partner-action="${item.status === "paused" ? "activate" : "pause"}">${item.status === "paused" ? "Ativar" : "Pausar"}</button></div></article>`).join("") : '<p class="empty-state">Nenhum anúncio.</p>';
    const privacyLabels = { acesso: "Acesso", correcao: "Correção", exclusao: "Exclusão", reclamacao: "Reclamação" };
    const privacyStatus = { aberta: "Aberta", em_andamento: "Em andamento", concluida: "Concluída" };
    privacyList.innerHTML = overview.privacyRequests.length ? overview.privacyRequests.map(item => `<article class="admin-row"><div><h3>${escapeHtml(item.protocol)} · ${escapeHtml(privacyLabels[item.type] || item.type)}</h3><p>${escapeHtml(item.name)} · ${escapeHtml(item.contact)}</p><small>${escapeHtml(item.message)}</small><span class="admin-status ${item.status === "concluida" ? "" : "paused"}">${escapeHtml(privacyStatus[item.status] || item.status)}</span></div><div class="admin-row-actions">${item.status === "aberta" ? `<button class="button button-small" data-privacy-id="${escapeHtml(item.id)}" data-privacy-status="em_andamento">Iniciar</button>` : ""}${item.status !== "concluida" ? `<button class="button button-outline button-small" data-privacy-id="${escapeHtml(item.id)}" data-privacy-status="concluida">Concluir</button>` : ""}</div></article>`).join("") : '<p class="empty-state">Nenhuma solicitação.</p>';
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
      } else if (button.dataset.privacyId) {
        await chamarAPI(`admin/privacy/${encodeURIComponent(button.dataset.privacyId)}/status`, { method: "PATCH", body: JSON.stringify({ status: button.dataset.privacyStatus }) });
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
initCustomerOrdersPage();
initProfessionalDashboard();
initAdminDashboard();
