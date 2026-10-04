# Bot Jurídico Discord | versão configurável

Esta versão permite configurar o bot diretamente pelo Discord, sem precisar copiar IDs manualmente para o `config.json`.

## Comando principal

Use:

```text
/configuracoes
```

Na primeira configuração, enquanto nenhum gestor estiver definido, administradores do servidor conseguem abrir o painel. Depois que o dono do servidor definir um gestor, somente o gestor escolhido e o dono do servidor terão acesso ao painel de configurações.

## O que pode ser configurado pelo painel

### Sistema de SET

- Canal em que os pedidos de SET aguardam aprovação
- Cargo Sistema, que pode aprovar ou negar os pedidos
- Cargo OAB
- Cargo Estagiário
- Cargo Advogado
- Cargo Promotor
- Cargo Juiz
- Cargo Oficial de Justiça
- Imagem grande do painel de SET por URL

Quando um SET é aprovado, o usuário recebe:

1. O cargo OAB, se estiver configurado
2. O cargo específico solicitado

### Sistema de Tickets

- Categoria onde os tickets serão criados
- Canal que receberá os transcripts
- Cargo da equipe que poderá acessar e usar as ações dos tickets
- Imagem do painel de tickets por URL

Se nenhum cargo específico de equipe de tickets for configurado, o bot usa o Cargo Sistema.

## Ações dentro do ticket

- Intimar: seleciona uma pessoa, abre um campo para escrever a intimação e envia por DM
- Citar: adiciona a pessoa ao canal e permite definir Testemunha, Procurador ou Autor do Fato
- Audiência: permite escrever livremente data, horário, local e observações
- Finalizar: gera transcript HTML, envia no canal configurado e encerra o ticket

## Publicar os painéis

Dentro de `/configuracoes`, existem os botões:

- Publicar painel SET aqui
- Publicar painel Ticket aqui

Assim você pode ir até o canal desejado, usar `/configuracoes` e publicar o painel diretamente nele.

Os comandos antigos continuam disponíveis:

```text
/painel_set
/painel_ticket
/bot_status
```

## Imagem do painel

O botão de imagem pede uma URL direta. Você pode enviar uma imagem em um canal do Discord, abrir a imagem e copiar o link. Cole esse link no formulário do painel.

Para remover a imagem, abra a opção novamente e envie o campo vazio.

## Backup da configuração

No painel `/configuracoes` existe o botão **Exportar config**. Ele envia o `config.json` atual de forma privada para você salvar como backup.

## Instalação

Instale as dependências:

```bash
pip install -r requirements.txt
```

No Discord Developer Portal, em **Bot > Privileged Gateway Intents**, ative:

- Server Members Intent
- Message Content Intent

O Presence Intent não é necessário.

## Token

Na Discloud, prefira configurar a variável:

```text
DISCORD_TOKEN=SEU_TOKEN
```

Nunca publique o token no GitHub.

Se usar `.env`, ele deve conter:

```env
DISCORD_TOKEN=SEU_TOKEN
```

Adicione `.env` ao `.gitignore`.

## Permissões do bot

O bot precisa de permissões para:

- Gerenciar canais
- Gerenciar cargos
- Ver canais
- Enviar mensagens
- Ler histórico de mensagens
- Anexar arquivos
- Incorporar links
- Usar comandos de aplicativo

O cargo do bot precisa ficar acima dos cargos que ele entregará aos usuários.

## Arquivos

```text
bot.py
config.json
requirements.txt
.env.example
README.md
discloud.config
```

O `config.json` começa vazio e é preenchido automaticamente pelo painel de configurações.
