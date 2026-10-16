const API_URL = (window.CLICK_BELEZA_API_URL || `${window.location.origin}/api`).replace(/\/+$/, "");

async function chamarAPI(rota, options = {}) {
  const endpoint = String(rota).replace(/^\/+/, "");
  const headers = new Headers(options.headers || {});
  headers.set("Accept", "application/json");
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  let resposta;
  try {
    resposta = await fetch(`${API_URL}/${endpoint}`, { ...options, headers });
  } catch (erro) {
    console.error("Erro ao conectar com a API do Click Beleza:", erro);
    throw new Error("Não foi possível conectar à API. Verifique se o servidor está ativo.");
  }

  let dados;
  try {
    dados = await resposta.json();
  } catch (erro) {
    console.error("A API do Click Beleza retornou uma resposta inválida:", erro);
    throw new Error("A API retornou uma resposta inválida.");
  }

  if (!resposta.ok) {
    throw new Error(dados.error || `A API respondeu com erro HTTP ${resposta.status}.`);
  }
  return dados;
}

async function buscarReaisDeMarilia({ servico = "", cidade = "", lat = "", lon = "" } = {}) {
  const query = new URLSearchParams();
  if (cidade) query.set("city", cidade);
  if (lat && lon) {
    query.set("lat", lat);
    query.set("lon", lon);
  }
  if (servico) query.set("servico", servico);
  return chamarAPI(`profissionais/reais?${query}`);
}
