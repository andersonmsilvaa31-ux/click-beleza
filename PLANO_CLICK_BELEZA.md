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

Para executar localmente, abra um terminal nesta pasta e rode:

```powershell
python consulta_cep.py
```

Depois, acesse `http://127.0.0.1:8765`. Os dados criados ficam em
`click_beleza_data.json`; os perfis iniciais são exemplos demonstrativos em
Marília e São Paulo e não representam profissionais reais. O protótipo não tem
autenticação nem proteção de dados suficiente para uso público. Não há
processamento de pagamentos. Após enviar um pedido, o cliente pode avançar por
uma etapa demonstrativa de Pix ou cartão; nenhuma cobrança, transferência,
repasse ou coleta de dados financeiros ocorre. O pedido continua aguardando a
confirmação do profissional. Uma cobrança real exigirá integração com um
provedor de pagamentos e controles de segurança apropriados.
Não o exponha à
internet nem cadastre dados pessoais reais. A consulta de CEP requer internet;
distância exata depende da localização GPS autorizada ou de coordenadas válidas
no perfil profissional.

No VS Code, use **Terminal → Run Task → Click Beleza: iniciar servidor local**
(também é a tarefa padrão de build, acionada por `Ctrl+Shift+B`). Para rodar a
suíte de testes, selecione **Click Beleza: testar API** em **Terminal → Run
Task**. A configuração de depuração está disponível em **Run and Debug**.
O projeto usa somente a biblioteca padrão do Python, sem pacotes adicionais.
As extensões Python e Pylance recomendadas pelo workspace já estão instaladas
neste ambiente.

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

- **Protótipo atual:** HTML, CSS e JavaScript.
- **Próxima etapa:** backend e banco de dados para usuários, profissionais,
  serviços, horários, reservas e avaliações.
- Serviço de mapas/geocodificação para calcular distâncias a partir de
  endereços ou coordenadas.
- Para exibir perfis do Google Maps diretamente no marketplace, avaliar a
  Places API oficial, obter chave e configurar faturamento, restrições de chave
  e regras de exibição. O protótipo oferece apenas um link de busca externa e
  não faz raspagem nem importação de dados do Maps.
- Serviço de autenticação e envio de e-mail ou notificações.
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
