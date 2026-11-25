# Fontes públicas: o que foi verificado (24/11/2026)

| Fonte | O que oferece | Uso na Central |
|---|---|---|
| **Pardal** (CSV SP, ODbL) | lat/lng, tipo, limite, origem (DNIT, OSM, ANTT, DER-SP, Inmetro, PRF) | base de comparação (não duplicar) |
| **OpenStreetMap** (Overpass) | nós `highway=speed_camera` com coordenada, às vezes `maxspeed`/`direction` | candidatos estruturados; sozinhos viram *não confirmado* (aparecem no app com pontinho cinza) |
| **ANTT** dados abertos (conjunto "Radar") | dados de radares de rodovias concedidas | já chega pelo Pardal; integração direta não feita |
| **DER-SP** (Diário Oficial/notícias) | rodovia + km + sentido de radares homologados; **sem lat/lng; sem limite** | só texto: pendente |
| **Artesp** dados abertos | especificação de cadastro (planilhas por concessionária); **sem lat/lng** | só referência; pendente |
| **Prefeitura de Jundiaí** | notícias com nomes de rua; mapa no site não baixável | texto: pendente / confirma junto com OSM |
| **DNIT** dados abertos | portal existe; arquivo de radares não localizado | via Pardal |
| **PRF** | nenhum arquivo aberto de radares localizado | via Pardal |
| **MapaRadar** | — | **não usado** |

Páginas novas: acrescente em `config/sources.json` (apenas endereços que existem). `family` define a confiança.

## Fontes não oficiais (o que dá e o que não dá)
| Fonte | Situação | Como entra |
|---|---|---|
| OpenStreetMap | colaborativo, com coordenada | nível *não confirmado*; *corroborado* se outra fonte concordar |
| Notícias/imprensa | páginas públicas | texto lido pela IA com evidência; nunca confirma; corrobora se o endereço cair a ≤150 m de um ponto do OSM |
| Fóruns/blogs públicos | só se a página permitir leitura automática (robots.txt) e você listar o endereço em `sources.json` com `"family": "forum"` | idem imprensa |
| Waze | **sem API pública de leitura** (o feed de dados é para parceiros que enviam dados) | não usado |
| Redes sociais | bloqueiam leitura automática e os termos proíbem | não usadas |
| Relatos no próprio app | motorista marca o ponto | origem "Comunidade", nível *não confirmado* |

Todo ponto não oficial é **possível radar falso ou antigo**; por isso aparece com pontinho e pode ter alerta desligado no app.
