# Modo Motorista Automático — arquitetura (ainda não implementado)

**Já existe no app (v0.2):** serviço em primeiro plano com GPS, alertas por voz/som a 100/50/20 m, painel flutuante,
funcionamento com outro app aberto e com a tela apagada (enquanto o serviço e a notificação estiverem ativos).

**Falta construir (futuro):**
1. *Detecção de movimento:* usar Activity Recognition (`IN_VEHICLE`) e/ou velocidade GPS > ~15 km/h por ~20 s
   para iniciar o serviço sozinho. Exige permissão `ACTIVITY_RECOGNITION` e localização em segundo plano
   (`ACCESS_BACKGROUND_LOCATION`, concedida pelo usuário nas configurações).
2. *Economia parado:* ao ficar < 3 km/h por ~2 min, trocar para `PRIORITY_BALANCED_POWER_ACCURACY` com intervalo longo
   e voltar à alta precisão quando o movimento retomar (o filtro de movimento do app já descarta parado).
3. *Trajeto:* o app usa o GPS do próprio aparelho; o rastro dinâmico já existe.
4. *Uber/99 aberta:* o app **não acessa** a rota interna desses apps; apenas vê a posição GPS do aparelho e alerta de
   radares à frente por proximidade/sentido. O Android pode encerrar serviços em segundo plano em alguns fabricantes:
   orientar "Sem restrição de bateria".
5. *Limites do Android:* iniciar serviço de localização em segundo plano sem o app visível é restrito a partir do
   Android 12/14; o gatilho por reconhecimento de atividade precisa de teste real em aparelho antes de prometer.
