# Central Inteligente de Radares (custo zero)

Repositório **público e separado** do app Motorista Nav. Todo dia o GitHub Actions:

1. baixa a base do **Pardal** e os radares do **OpenStreetMap** (fontes estruturadas, com coordenada);
2. baixa as **páginas públicas** listadas em `config/sources.json` (prefeitura, DER-SP, Artesp, DNIT…);
3. pede a uma **IA gratuita** que *leia o texto* e liste os radares citados. A IA **nunca fornece coordenadas**;
4. **confere** cada achado: a frase de evidência precisa existir literalmente na página; limite e km que não aparecem
   na frase são descartados;
5. geocodifica endereços (Nominatim) e **classifica**:
   - `data/confirmados.csv` — **oficial confirmado**, formato idêntico ao Pardal (o app já importa);
   - `data/nao_oficiais.csv` — **não oficial** com coordenada estruturada (mesmo formato). O app deriva o nível pelas
     etiquetas de fonte da coluna `source`: 2+ fontes diferentes = *corroborado*; 1 fonte = *não confirmado*;
   - `data/pendentes.csv` — **não vai para o mapa**: sem coordenada estruturada (só endereço ou rodovia/km);
6. grava `data/manifest.json` (data, contagens, origem, uso de cada IA, erros).

## Níveis de confiança
| Nível | Regra |
|---|---|
| Oficial confirmado | coordenada estruturada + fonte oficial concordando |
| Não oficial corroborado | 2+ fontes diferentes concordam no local (ex.: OpenStreetMap + imprensa/fórum), nenhuma oficial |
| Não confirmado | 1 fonte só (ex.: OpenStreetMap sozinho) |

A regra existe igual no app (`Confidence.kt`) e aqui (`classify.level_of`), com os mesmos casos de teste nos dois lados.

## Regra de confirmação (conservadora)
Só vira **confirmado** quando há **coordenada estruturada (OpenStreetMap)** + **fonte oficial independente**
(prefeitura, DER-SP, Artesp, DNIT, PRF, ANTT) citando o mesmo local (≤150 m, sentido compatível).
Imprensa nunca confirma. Endereço geocodificado nunca confirma sozinho (erro de centenas de metros).
DER-SP/Artesp informam rodovia+km sem latitude/longitude: ficam **pendentes** até existir fonte com localização.
Radares já presentes no Pardal (≤30 m, mesmo sentido) são tratados como duplicata; sentidos opostos não.
Confirmados **nunca são apagados** automaticamente.

## Revezamento de IAs
Ordem em `config/providers.json`: Gemini → Groq → Cloudflare Workers AI → Mistral. Cada pedido começa pela principal;
429/cota → pausa até renovar (Retry-After, ou virada do dia UTC); chave/plano negado → pausa 24 h; erro de servidor →
pausa curta. Teto diário **local** abaixo do limite gratuito. Se todas falharem: a etapa de IA pausa, o resto segue, e a
página volta a ser processada na próxima execução. Páginas sem mudança não gastam cota.

**Custo zero:** só planos gratuitos, sem cartão. Estourou a cota = erro/bloqueio, não cobrança (Cloudflare Free e Groq Free
bloqueiam; Gemini e Mistral gratuitos limitam). **Não ative faturamento em nenhum provedor.**

## Rodar / testar
```
python -m unittest discover -s tests -t .      # 43 testes, sem rede e sem chaves
python -m central.run --no-ai                  # coleta sem IA (precisa de internet)
python -m central.run                          # completo (usa as chaves do ambiente)
```
No GitHub: aba **Actions → Central de Radares → Run workflow**.

Veja `docs/CHAVES_E_SEGURANCA.md`, `docs/FONTES.md` e `docs/MODO_MOTORISTA_AUTOMATICO.md`.

## Limitações conhecidas
- Chamadas às APIs das 4 IAs e ao Overpass/Nominatim/Pardal **não foram testadas ao vivo** neste desenvolvimento (rede
  bloqueada); testes usam respostas falsas. O primeiro "Run workflow" é a validação real. Se algum modelo mudou de nome,
  o erro aparece em `data/manifest.json` e basta ajustar `config/providers.json`.
- Sem fonte oficial com latitude/longitude para Jundiaí/Várzea/Campo Limpo/Jarinu, a maioria dos achados de texto
  fica **pendente** — por desenho, para não inventar.
- Agendamentos do GitHub podem atrasar e são pausados após ~60 dias sem atividade no repositório.
