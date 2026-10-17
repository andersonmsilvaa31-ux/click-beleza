# Click Beleza — plano do projeto

## Protótipo executável

As etapas de protótipo e uma primeira fatia do MVP estão implementadas no
projeto: busca por serviço e cidade/CEP/GPS, ordenação por distância/preço/nota,
perfis, cadastro e edição de profissional, solicitação e gestão de
agendamentos com etapa de checkout demonstrativo, avaliações após conclusão,
portfólio de fotos/vídeos, assistente de orientação e vitrine rotativa de
inspiração. O piloto está configurado para Marília (SP). O atalho para o Google
Maps abre as listagens reais do Google em outra aba; ele não importa nem
verifica estabelecimentos dentro do Click Beleza.

As quatro imagens de inspiração são materiais ilustrativos do Unsplash,
armazenados localmente em `assets/` para o carrossel funcionar sem carregar
fotos de terceiros a cada visita.

Para executar localmente com PostgreSQL, instale as dependências nesta pasta e
configure a senha local no arquivo `%APPDATA%\postgresql\pgpass.conf` (formato
`host:porta:banco:usuário:senha`). Depois rode:

```powershell
python -m pip install -r requirements.txt
python consulta_cep.py
```

No VS Code, a tarefa e a configuração de depuração selecionam o banco
`click_beleza` no PostgreSQL local. A primeira conexão cria as tabelas relacionais
(`professionals`, `users`, `sessions`, `bookings`, `chats`, `partners`,
`financial_transactions`, `privacy_requests`, `booking_slots`) com chaves
estrangeiras e e-mail único, e migra uma única vez o estado antigo
(`click_beleza_state`, mantida como `click_beleza_state_legacy`) ou o arquivo
`click_beleza_data.json`. As próximas alterações são salvas só nas linhas que
mudaram, em transação. A senha não fica no código: o usuário da aplicação e
o banco estão na URL sem senha, e o cliente PostgreSQL lê a senha do arquivo
local `pgpass.conf`. Para iniciar manualmente fora do VS Code, defina
`CLICK_BELEZA_DATABASE_URL` como
`postgresql://click_beleza_app@127.0.0.1:5432/click_beleza`.
Para abrir o mesmo banco no pgAdmin, registre um servidor com host
`127.0.0.1`, porta `5432`, banco de manutenção `postgres` e usuário
`postgres`; informe a senha diretamente na janela do pgAdmin e expanda
`Databases > click_beleza`.

Sem `CLICK_BELEZA_DATABASE_URL`, o backend mantém o armazenamento JSON para
testes e execução independente. Acesse `http://127.0.0.1:8765`. Os perfis
iniciais são exemplos demonstrativos em Marília e São Paulo e não representam
profissionais reais. O protótipo tem contas de profissionais e administrador, com senhas armazenadas
como hash e sessões em cookie HttpOnly. O painel administrativo fica em
`/admin.html`; a área profissional fica em `/professional-dashboard.html`.
Cadastros de profissionais criam uma conta e uma sessão automaticamente.
O login aceita e-mail ou CPF validado quando este foi informado no cadastro.
O e-mail continua obrigatório para novas contas profissionais; o CPF é
opcional e armazenado somente como hash salgado. O formulário pode lembrar
apenas o CPF/e-mail neste dispositivo, nunca a senha. Senhas novas exigem pelo
menos 8 caracteres e são armazenadas com hash.
Para vincular um CPF opcional à primeira conta administrativa, configure
`CLICK_BELEZA_ADMIN_CPF` ao executar a tarefa de criação do administrador.
Para criar a primeira conta administrativa no VS Code, execute
**Click Beleza: criar primeiro administrador** em **Terminal → Run Task**. A
senha aleatória é exibida uma única vez no terminal; guarde-a em local seguro.
O login inicial é `admin@clickbeleza.local`, configurável pela tarefa.
Administradores podem visualizar e gerenciar todos os perfis, contas,
agendamentos, clientes, conversas e anúncios. Cada profissional acessa somente
o próprio perfil e os pedidos e conversas ligados a ele. Clientes usam as
funções públicas básicas; cada pedido recebe um código aleatório privado para
autorizar somente o pagamento demonstrativo, cancelamento e avaliação daquele
pedido. O banco guarda somente o hash do código, nunca o código em si.
Apesar da autenticação, este MVP ainda não tem controles suficientes para uso
público em produção. Não há
processamento de pagamentos. Após enviar um pedido, o cliente pode avançar por
uma etapa demonstrativa de Pix ou cartão; nenhuma cobrança, transferência,
repasse ou coleta de dados financeiros ocorre. O pedido continua aguardando a
confirmação do profissional. Uma cobrança real exigirá integração com um
provedor de pagamentos e controles de segurança apropriados.
O painel profissional também oferece um livro-caixa manual de entradas e
despesas, isolado por profissional. Esses lançamentos são anotações e não
confirmam recebimentos, pagamentos, cobranças ou transferências reais.
Não o exponha à
internet nem cadastre dados pessoais reais. A consulta de CEP requer internet;
distância exata depende da localização GPS autorizada ou de coordenadas válidas
no perfil profissional.

No VS Code, use **Terminal → Run Task → Click Beleza: iniciar servidor local**
(também é a tarefa padrão de build, acionada por `Ctrl+Shift+B`). Para rodar a
suíte de testes, selecione **Click Beleza: testar API** em **Terminal → Run
Task**. A configuração de depuração está disponível em **Run and Debug**.
O backend usa a biblioteca padrão e o driver `psycopg` listado em
`requirements.txt`. As extensões Python e Pylance estão instaladas neste
ambiente; a extensão PostgreSQL também é recomendada pelo workspace.

### API HTTP

As páginas carregam `api.js`, que define `API_URL` com a origem atual e a rota
`/api`, e disponibiliza `chamarAPI("profissionais?servico=barbeiro")`. Também
aceita um endereço alternativo configurado antes do script com
`window.CLICK_BELEZA_API_URL`. As rotas em português são `/api/profissionais`,
`/api/profissionais/{id}`, `/api/profissionais/{id}/editar` e
`/api/agendamentos`; as rotas anteriores em inglês continuam compatíveis.
`api.js` também disponibiliza `buscarReaisDeMarilia()`, que consulta a rota
`/api/profissionais/reais` no backend. Ela busca locais mapeados no
OpenStreetMap/Overpass, incluindo estabelecimentos cadastrados como nós, vias
ou relações, na cidade informada ou em um raio de 12 km da localização GPS.
Esses locais são exibidos em cards externos com links para o mapa; preço,
avaliações, fotos e agenda não são inventados nem importados. Os resultados
dependem da disponibilidade do serviço público Overpass e podem estar
incompletos ou desatualizados. A interface informa indisponibilidade e mantém
os cadastros Click Beleza acessíveis como alternativa. Atribuição: ©
OpenStreetMap contributors, sob licença ODbL.

As rotas de conta são `/api/auth/login`, `/api/auth/logout` e `/api/auth/me`.
O painel administrativo usa `/api/admin/overview` e as rotas protegidas de
gestão; profissionais autenticados recebem somente os próprios pedidos e
conversas. A sessão expira após oito horas. Em uma hospedagem pública, use
HTTPS e revise controles de segurança, privacidade e recuperação de acesso
antes de liberar as contas.
Clientes acompanham um pedido individual em `/customer-orders.html` usando o
código do pedido e o código privado exibido no checkout; esse código permite
consultar e cancelar apenas aquele pedido e registrar uma avaliação depois do
atendimento.

**Consulta de CEP** (`/api/cep?cep=00000-000`): consulta o ViaCEP e, se ele
falhar, o BrasilAPI; depois tenta obter coordenadas pelo Nominatim
(OpenStreetMap). Retorna endereço, `lat`, `lon` e `precision` (`cep`,
`endereço` ou `cidade`). Quando há coordenadas precisas, a busca do site usa
esse ponto para calcular distâncias e o raio de 12 km. Respostas ficam em
cache em memória (CEP por 24 h) e a busca Overpass tenta três servidores
públicos em sequência, com cache de 10 minutos.
Exemplo:

```js
chamarAPI("profissionais?servico=barbeiro&city=Marília")
  .then(resultado => console.log(resultado.professionals))
  .catch(erro => console.error(erro.message));
```

A busca inicial da API retorna perfis fictícios identificados como
"Perfil de demonstração", para facilitar testes locais; eles não aceitam
agendamentos e não devem ser apresentados como profissionais reais.

## 1. Ideia

Criar um marketplace de serviços de beleza e bem-estar. A pessoa informa onde
está, escolhe o serviço e compara profissionais e estabelecimentos próximos por
distância, preço, avaliações e horários disponíveis. A experiência deve ser
simples como pedir em um aplicativo de delivery, mas voltada a encontrar e
agendar serviços.

## 2. Público

- **Clientes:** pessoas que querem encontrar um serviço confiável perto de casa
  ou do trabalho, comparar opções e agendar sem precisar procurar em vários
  perfis e redes sociais.
- **Profissionais:** autônomos e estabelecimentos que querem divulgar serviços,
  preencher horários disponíveis e receber novos clientes.

## 3. Jornada principal

1. A pessoa informa um CEP ou permite o uso da localização.
2. Escolhe uma categoria, como cabelo, manicure, barbearia ou maquiagem.
3. Vê opções na região e pode ordenar por distância, menor preço ou melhor
   avaliação.
4. Abre o perfil para conferir serviços, preços, fotos, endereço, avaliações e
   horários.
5. Escolhe um horário e solicita ou confirma o agendamento.
6. Avança para a etapa demonstrativa de Pix ou cartão; nenhuma cobrança real
   é feita, e o pedido continua aguardando a confirmação do profissional.
7. Recebe a confirmação e, depois do atendimento, pode avaliar o profissional.

## 4. MVP — primeira versão

Priorizar o essencial antes de construir um aplicativo completo:

- Página inicial com busca por serviço e localização.
- Categorias de serviços e filtros por distância, preço e nota.
- Lista de profissionais com preço, distância, avaliação e disponibilidade.
- Perfil público do profissional com serviços, fotos, endereço e horários.
- Cadastro e edição básica do perfil profissional.
- Solicitação de agendamento, checkout demonstrativo e confirmação de status.
- Avaliações após um atendimento concluído.
- Galeria de fotos e vídeos no perfil do profissional.
- Assistente automático para orientar a busca e o agendamento.
- Vitrine rotativa de inspiração com imagens ilustrativas, substituível por
  anúncios identificados quando houver uma solução comercial.
- Layout adaptado para celular.

Para validar a ideia, começar em uma única cidade ou região e com poucas
categorias. A ordenação por proximidade só deve ser apresentada como real quando
os profissionais tiverem endereço ou coordenadas cadastradas e a localização do
cliente puder ser comparada com esses dados.

## 5. Etapas sugeridas

1. **Descoberta:** conversar com clientes e profissionais; escolher cidade,
   categorias iniciais e regras de agendamento.
2. **Protótipo:** desenhar as telas principais e testar a busca e a comparação
   com usuários.
3. **MVP:** implementar cadastro, perfis, busca, horários e solicitações de
   agendamento.
4. **Piloto local:** cadastrar profissionais reais, acompanhar agendamentos e
   recolher feedback.
5. **Evolução:** adicionar pagamentos, cancelamentos, notificações, promoções e
   expansão para outras regiões conforme a validação.

## 6. Materiais e recursos necessários

### Para planejar e produzir

- Computador com acesso à internet e celulares para testar as telas.
- Identidade visual: nome, logotipo, paleta de cores e tipografia.
- Protótipo navegável das telas de busca, resultados, perfil e agendamento.
- Textos e fotos autorizadas dos profissionais e dos serviços.
- Formulário para cadastrar profissionais, serviços, preços, endereço e
  disponibilidade.

### Para desenvolver

- **Protótipo atual:** HTML, CSS e JavaScript com backend local, PostgreSQL e
  autenticação básica para profissionais e administração.
- **Próxima etapa:** robustecer o backend e o banco de dados para operação
  pública, com horários, reservas, auditoria e recuperação de acesso.
- Serviço de mapas/geocodificação para calcular distâncias a partir de
  endereços ou coordenadas.
- Para exibir perfis do Google Maps diretamente no marketplace, avaliar a
  Places API oficial, obter chave e configurar faturamento, restrições de chave
  e regras de exibição. O protótipo oferece apenas um link de busca externa e
  não faz raspagem nem importação de dados do Maps.
- Envio de e-mail ou notificações para contas e agendamentos.
- Provedor de pagamentos, caso a cobrança seja feita dentro da plataforma.
- Hospedagem, domínio e ambiente de testes.

### Para operar com segurança

- Termos de uso e política de privacidade adequados ao serviço.
- Consentimento claro antes de acessar a localização; oferecer busca por CEP
  como alternativa.
- Autorização para publicar fotos, preços e dados de contato.
- Processo de moderação de avaliações e de atendimento a reclamações.
- Verificação dos requisitos legais e de proteção de dados aplicáveis antes do
  lançamento.

## 7. Modelo de receita a testar

- Comissão por agendamento concluído; ou
- Plano mensal opcional para profissionais, com ferramentas extras de
  divulgação e gestão.

No piloto, validar a disposição de pagamento antes de fixar taxas. Exibir com
clareza qualquer comissão ou cobrança ao cliente e ao profissional.

## 8. Indicadores para o piloto

- Profissionais ativos e serviços cadastrados na região.
- Buscas que resultam em visualização de perfil e em pedido de agendamento.
- Tempo até encontrar um horário adequado.
- Agendamentos confirmados, concluídos e cancelados.
- Avaliação do atendimento e retorno de clientes.
- Custo para atrair clientes e profissionais.

## Infraestrutura de produção
- Variáveis: CLICK_BELEZA_DATABASE_URL, CLICK_BELEZA_HOST/PORT, CLICK_BELEZA_SECURE (cookie Secure + HSTS), CLICK_BELEZA_TRUST_PROXY, CLICK_BELEZA_RATE_LIMIT.
- Cabeçalhos: CSP, HSTS, Permissions-Policy, COOP/CORP. Limite de requisições por IP. Endpoints /healthz e /readyz. Logs em JSON. Encerramento limpo por SIGTERM.
- Deploy: Dockerfile, docker-compose.yml (PostgreSQL, app, Caddy com HTTPS automático, backup diário com retenção de 14 dias), Caddyfile e .env.example. CI em .github/workflows/ci.yml.
- Subir: copie .env.example para .env, preencha SITE_DOMAIN e POSTGRES_PASSWORD e rode docker compose up -d --build.

- Estáticos com ETag, gzip e revalidação (304); suporte a HEAD e OPTIONS; cabeçalho Server sem versão. Verificação do banco: python scripts/check_db.py (IDs duplicados, referências órfãs, faixas x agendamentos).

## Pagamentos reais (Mercado Pago)

O modulo payments.py cria a cobranca (Checkout Pro), valida o webhook por assinatura HMAC, confirma consultando a API (valor e referencia conferidos) e estorna ao cancelar pedido pago. Habilite com CLICK_BELEZA_MP_ACCESS_TOKEN, CLICK_BELEZA_MP_WEBHOOK_SECRET e CLICK_BELEZA_PUBLIC_URL (https). Sem elas o checkout segue demonstrativo. Para repasse automatico (split) cada profissional precisa autorizar a conta via OAuth do Mercado Pago (token em mpSellerToken no usuario, ainda sem tela de onboarding); sem isso o valor entra na conta da plataforma e a comissao fica em platformFee. Configure no painel do Mercado Pago a notificacao em /api/payments/webhook. Prazo e regra de cancelamento/estorno dependem de definicao comercial.

Repasse automatico: o profissional conecta a conta em Meu espaco > Receber pelo Mercado Pago (OAuth). Requer CLICK_BELEZA_MP_CLIENT_ID, CLICK_BELEZA_MP_CLIENT_SECRET e CLICK_BELEZA_ENCRYPTION_KEY (chave Fernet; os tokens ficam cifrados no banco). Cadastre em Mercado Pago Developers a URL de redirecionamento: PUBLIC_URL/api/payments/oauth/callback. Tokens sao renovados automaticamente.
