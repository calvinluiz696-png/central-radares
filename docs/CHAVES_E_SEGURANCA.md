# Chaves de API e segurança

**Regra:** chave nunca vai para o app, para o código ou para arquivos do repositório. Só em **GitHub Secrets**
(repositório > Settings > Secrets and variables > Actions > New repository secret). Em repositório público os Secrets
continuam privados e não aparecem nos logs.

| Secret | De onde vem (passos aproximados; as telas mudam) |
|---|---|
| `GEMINI_API_KEY` | Google AI Studio (aistudio.google.com) > "Get API key" em projeto **sem faturamento**. Gratuito: o Google usa o conteúdo para melhorar produtos (aqui só vai texto público). |
| `GROQ_API_KEY` | console.groq.com > API Keys. Plano gratuito; ao estourar, responde 429. |
| `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID` | Painel da Cloudflare > Workers AI > "Use REST API": cria token com permissão de Workers AI e mostra o Account ID. Free = 10.000 neurons/dia, depois bloqueia. Alguns modelos exigem plano pago: use só os gratuitos (`config/providers.json`). |
| `MISTRAL_API_KEY` | console.mistral.ai > plano **Experiment** (sem cartão; exige telefone verificado). Pedidos podem ser usados para treinar os modelos deles. |

Não precisa cadastrar as quatro: sem a chave, aquela IA é pulada. Com nenhuma, a Central roda só a parte sem IA.

## Cuidados
- **Não ative cobrança/cartão** em nenhum painel. Se um provedor pedir cartão, não use esse provedor.
- Dados enviados às IAs: apenas texto de páginas públicas. Nenhum dado seu, do celular ou de motoristas.
- Respostas das IAs são tratadas como não confiáveis: validadas (evidência literal, números conferidos) antes de qualquer uso.
- Se uma chave vazar, apague-a no provedor e crie outra; atualize o Secret.
- Repositório público = qualquer pessoa lê `data/`, `state/` e `config/`. Não coloque nada pessoal ali.
