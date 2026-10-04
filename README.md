# Bot Jurídico para Discord

## O que o bot faz

### Painel de SET
Cargos disponíveis:
- Estagiário
- Advogado
- Promotor
- Juiz
- Oficial de Justiça

O usuário preenche Nome RP e ID. O pedido vai para um canal de análise com botões **Aprovar** e **Negar**. Ao aprovar, o cargo é entregue automaticamente.

### Painel de Ticket
Botões:
- Suporte
- Reclamações
- Denúncia

Ao abrir, o bot cria um canal privado.

Dentro do ticket aparecem:
- **Intimar**: seleciona um usuário, abre um formulário para escrever a intimação e envia por DM.
- **Citar**: seleciona um usuário e define se ele entra como Testemunha, Procurador ou Autor do Fato. O usuário é adicionado ao canal e recebe aviso por DM.
- **Audiência**: abre um formulário livre para escrever data, horário, local e observações. O aviso é publicado no ticket e enviado por DM aos participantes adicionados individualmente.
- **Finalizar**: gera transcript HTML, envia no canal de logs e apaga o ticket após 5 segundos.

## Instalação

1. Instale Python 3.10 ou superior.
2. Rode:

```bash
pip install -r requirements.txt
```

3. No Discord Developer Portal, ative no bot:
- SERVER MEMBERS INTENT
- MESSAGE CONTENT INTENT

4. Copie `.env.example` para `.env` e coloque o token:

```env
DISCORD_TOKEN=SEU_TOKEN_AQUI
```

5. Ative o Modo Desenvolvedor do Discord e copie os IDs necessários.

6. Preencha `config.json`:
- `guild_id`: ID do servidor
- `staff_role_ids`: IDs dos cargos que podem intimar, citar, marcar audiência e finalizar
- `set_review_channel_id`: canal onde chegam os pedidos de SET
- `ticket_category_id`: categoria onde serão criados os tickets
- `ticket_log_channel_id`: canal de transcript
- `set_roles`: IDs dos cargos que serão entregues

7. Inicie:

```bash
python bot.py
```

## Criar os painéis

No canal de SET, use:

```text
/painel_set
```

No canal de tickets, use:

```text
/painel_ticket
```

Para verificar a configuração:

```text
/bot_status
```

## Permissões importantes

O bot deve poder:
- Gerenciar canais
- Gerenciar cargos
- Ver canais
- Enviar mensagens
- Ler histórico
- Anexar arquivos
- Usar comandos de aplicativo

O cargo do bot precisa ficar acima dos cargos Estagiário, Advogado, Promotor, Juiz e Oficial de Justiça.

## Hospedagem 24 horas

O bot precisa ficar hospedado em VPS, hospedagem de bot ou serviço compatível com Python para funcionar 24 horas por dia.
