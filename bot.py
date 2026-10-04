import os
import io
import re
import html
import json
import asyncio
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG = json.load(f)


def as_int(v, default=0):
    try:
        return int(v)
    except Exception:
        return default


def is_staff(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    staff_ids = {as_int(x) for x in CONFIG.get("staff_role_ids", [])}
    return any(r.id in staff_ids for r in member.roles)


async def require_staff(interaction: discord.Interaction) -> bool:
    if not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
        await interaction.response.send_message("❌ Você não tem permissão para usar esta função.", ephemeral=True)
        return False
    return True


def clean_name(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r"[^a-z0-9-]+", "-", name)
    return re.sub(r"-+", "-", name).strip("-")[:60] or "usuario"


async def safe_dm(user, *, content=None, embed=None):
    try:
        await user.send(content=content, embed=embed)
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False


SET_LABELS = {
    "estagiario": "Estagiário",
    "advogado": "Advogado",
    "promotor": "Promotor",
    "juiz": "Juiz",
    "oficial_justica": "Oficial de Justiça",
}

CITATION_TYPES = {
    "testemunha": "Testemunha",
    "procurador": "Procurador",
    "autor_fato": "Autor do Fato",
}


class JuridicoBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        self.add_view(SetPanelView())
        self.add_view(SetApprovalView())
        self.add_view(TicketPanelView())
        self.add_view(TicketActionsView())

        guild_id = as_int(CONFIG.get("guild_id"))
        if guild_id:
            guild = discord.Object(id=guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()


bot = JuridicoBot()


def get_set_role(guild: discord.Guild, key: str):
    rid = as_int(CONFIG.get("set_roles", {}).get(key))
    return guild.get_role(rid) if rid else None


def parse_request_footer(embed: discord.Embed):
    footer = embed.footer.text or ""
    result = {}
    for part in footer.split("|"):
        if "=" in part:
            k, v = part.split("=", 1)
            result[k] = v
    return as_int(result.get("user")), as_int(result.get("role"))


class SetRequestModal(discord.ui.Modal):
    def __init__(self, role_key: str):
        super().__init__(title=f"Solicitar SET: {SET_LABELS[role_key]}")
        self.role_key = role_key
        self.nome = discord.ui.TextInput(label="Nome no RP", max_length=80)
        self.passaporte = discord.ui.TextInput(label="ID / Passaporte", max_length=30)
        self.obs = discord.ui.TextInput(
            label="Observação",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=500,
        )
        self.add_item(self.nome)
        self.add_item(self.passaporte)
        self.add_item(self.obs)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            return await interaction.response.send_message("❌ Use no servidor.", ephemeral=True)

        role = get_set_role(guild, self.role_key)
        review = guild.get_channel(as_int(CONFIG.get("set_review_channel_id")))
        if not role or not isinstance(review, discord.TextChannel):
            return await interaction.response.send_message("❌ Configuração de SET incompleta.", ephemeral=True)

        embed = discord.Embed(title="📥 Nova solicitação de SET", color=discord.Color.magenta())
        embed.add_field(name="Solicitante", value=interaction.user.mention, inline=False)
        embed.add_field(name="Nome RP", value=str(self.nome), inline=True)
        embed.add_field(name="ID", value=str(self.passaporte), inline=True)
        embed.add_field(name="Cargo solicitado", value=role.mention, inline=False)
        embed.add_field(name="Observação", value=str(self.obs) or "Nenhuma", inline=False)
        embed.set_footer(text=f"user={interaction.user.id}|role={role.id}")

        await review.send(embed=embed, view=SetApprovalView())
        await interaction.response.send_message("✅ Seu pedido de SET foi enviado para análise.", ephemeral=True)


class SetRoleButton(discord.ui.Button):
    def __init__(self, key: str, emoji: str, row: int):
        super().__init__(label=SET_LABELS[key], emoji=emoji, style=discord.ButtonStyle.primary,
                         custom_id=f"set:{key}", row=row)
        self.key = key

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(SetRequestModal(self.key))


class SetPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        for key, emoji, row in [
            ("estagiario", "📚", 0), ("advogado", "💼", 0), ("promotor", "⚖️", 0),
            ("juiz", "👨‍⚖️", 1), ("oficial_justica", "📜", 1)
        ]:
            self.add_item(SetRoleButton(key, emoji, row))


class SetApprovalView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Aprovar", emoji="✅", style=discord.ButtonStyle.success, custom_id="set:approve")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_staff(interaction):
            return
        if not interaction.message or not interaction.message.embeds:
            return await interaction.response.send_message("❌ Solicitação inválida.", ephemeral=True)

        uid, rid = parse_request_footer(interaction.message.embeds[0])
        member = interaction.guild.get_member(uid) if interaction.guild else None
        role = interaction.guild.get_role(rid) if interaction.guild else None
        if not member or not role:
            return await interaction.response.send_message("❌ Usuário ou cargo não encontrado.", ephemeral=True)

        try:
            await member.add_roles(role, reason=f"SET aprovado por {interaction.user}")
        except discord.Forbidden:
            return await interaction.response.send_message("❌ O cargo do bot precisa ficar acima do cargo que será entregue.", ephemeral=True)

        embed = interaction.message.embeds[0]
        embed.title = "✅ SET aprovado"
        embed.color = discord.Color.green()
        embed.add_field(name="Aprovado por", value=interaction.user.mention, inline=False)
        await interaction.message.edit(embed=embed, view=None)
        await safe_dm(member, embed=discord.Embed(
            title="✅ SET aprovado",
            description=f"Seu cargo **{role.name}** foi aprovado e adicionado.",
            color=discord.Color.green()
        ))
        await interaction.response.send_message("✅ SET aprovado.", ephemeral=True)

    @discord.ui.button(label="Negar", emoji="❌", style=discord.ButtonStyle.danger, custom_id="set:deny")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_staff(interaction):
            return
        if not interaction.message or not interaction.message.embeds:
            return await interaction.response.send_message("❌ Solicitação inválida.", ephemeral=True)
        uid, _ = parse_request_footer(interaction.message.embeds[0])
        member = interaction.guild.get_member(uid) if interaction.guild else None
        embed = interaction.message.embeds[0]
        embed.title = "❌ SET negado"
        embed.color = discord.Color.red()
        embed.add_field(name="Negado por", value=interaction.user.mention, inline=False)
        await interaction.message.edit(embed=embed, view=None)
        if member:
            await safe_dm(member, embed=discord.Embed(
                title="❌ Solicitação de SET negada",
                description="Sua solicitação de SET não foi aprovada.",
                color=discord.Color.red()
            ))
        await interaction.response.send_message("✅ Solicitação negada.", ephemeral=True)


TICKET_TYPES = {
    "suporte": ("Suporte", "🛠️"),
    "reclamacoes": ("Reclamações", "📢"),
    "denuncia": ("Denúncia", "🚨"),
}


async def create_ticket(interaction: discord.Interaction, kind: str):
    guild = interaction.guild
    member = interaction.user
    if not guild or not isinstance(member, discord.Member):
        return await interaction.response.send_message("❌ Use no servidor.", ephemeral=True)

    category = guild.get_channel(as_int(CONFIG.get("ticket_category_id")))
    if not isinstance(category, discord.CategoryChannel):
        return await interaction.response.send_message("❌ Categoria de tickets não configurada.", ephemeral=True)

    marker = f"ticket_owner:{member.id};ticket_type:{kind}"
    for ch in category.text_channels:
        if ch.topic and marker in ch.topic:
            return await interaction.response.send_message(f"⚠️ Você já possui este ticket aberto: {ch.mention}", ephemeral=True)

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True,
                                               manage_messages=True, read_message_history=True, attach_files=True),
    }
    for rid in CONFIG.get("staff_role_ids", []):
        role = guild.get_role(as_int(rid))
        if role:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                                           read_message_history=True, attach_files=True)

    label, emoji = TICKET_TYPES[kind]
    channel = await guild.create_text_channel(
        f"{kind}-{clean_name(member.display_name)}-{str(member.id)[-4:]}",
        category=category,
        overwrites=overwrites,
        topic=f"{marker};opened:{int(datetime.now().timestamp())}"
    )
    embed = discord.Embed(
        title=f"{emoji} Ticket de {label}",
        description=(f"Olá {member.mention}. Descreva sua solicitação neste canal.\n\n"
                     "A equipe poderá usar os botões abaixo para **intimar, citar, marcar audiência ou finalizar**."),
        color=discord.Color.blurple()
    )
    await channel.send(content=member.mention, embed=embed, view=TicketActionsView())
    await interaction.response.send_message(f"✅ Ticket criado: {channel.mention}", ephemeral=True)


class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Suporte", emoji="🛠️", style=discord.ButtonStyle.primary, custom_id="ticket:suporte")
    async def suporte(self, interaction: discord.Interaction, button: discord.ui.Button):
        await create_ticket(interaction, "suporte")

    @discord.ui.button(label="Reclamações", emoji="📢", style=discord.ButtonStyle.secondary, custom_id="ticket:reclamacoes")
    async def reclamacoes(self, interaction: discord.Interaction, button: discord.ui.Button):
        await create_ticket(interaction, "reclamacoes")

    @discord.ui.button(label="Denúncia", emoji="🚨", style=discord.ButtonStyle.danger, custom_id="ticket:denuncia")
    async def denuncia(self, interaction: discord.Interaction, button: discord.ui.Button):
        await create_ticket(interaction, "denuncia")


class IntimationModal(discord.ui.Modal):
    def __init__(self, target_id: int):
        super().__init__(title="Enviar intimação")
        self.target_id = target_id
        self.texto = discord.ui.TextInput(label="Texto da intimação", style=discord.TextStyle.paragraph,
                                          min_length=5, max_length=3500)
        self.add_item(self.texto)

    async def on_submit(self, interaction: discord.Interaction):
        member = interaction.guild.get_member(self.target_id) if interaction.guild else None
        if not member:
            return await interaction.response.send_message("❌ Pessoa não encontrada.", ephemeral=True)

        embed = discord.Embed(title="📨 INTIMAÇÃO", description=str(self.texto), color=discord.Color.gold())
        embed.add_field(name="Servidor", value=interaction.guild.name, inline=False)
        if interaction.channel:
            embed.add_field(name="Processo", value=f"#{interaction.channel.name}", inline=False)
        sent = await safe_dm(member, embed=embed)

        if isinstance(interaction.channel, discord.TextChannel):
            copy = discord.Embed(title="📨 Intimação registrada", description=str(self.texto), color=discord.Color.gold())
            copy.add_field(name="Intimado", value=member.mention)
            copy.add_field(name="Enviada por", value=interaction.user.mention)
            copy.add_field(name="DM", value="✅ Enviada" if sent else "⚠️ DM fechada")
            await interaction.channel.send(embed=copy)

        await interaction.response.send_message("✅ Intimação processada." if sent else "⚠️ Registrada, mas a DM está fechada.", ephemeral=True)


class IntimationUserSelect(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(placeholder="Selecione a pessoa que será intimada", min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(IntimationModal(self.values[0].id))


class IntimationSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(IntimationUserSelect())


class CitationSetupView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.target_id = None
        self.kind = None
        self.add_item(CitationUserSelect(self))
        self.add_item(CitationRoleSelect(self))
        self.add_item(CitationConfirm(self))


class CitationUserSelect(discord.ui.UserSelect):
    def __init__(self, parent):
        super().__init__(placeholder="Selecione a pessoa", min_values=1, max_values=1)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        self.parent_view.target_id = self.values[0].id
        await interaction.response.defer(ephemeral=True)


class CitationRoleSelect(discord.ui.Select):
    def __init__(self, parent):
        super().__init__(
            placeholder="Selecione a participação no processo",
            min_values=1, max_values=1,
            options=[
                discord.SelectOption(label="Testemunha", value="testemunha", emoji="👁️"),
                discord.SelectOption(label="Procurador", value="procurador", emoji="💼"),
                discord.SelectOption(label="Autor do Fato", value="autor_fato", emoji="⚖️"),
            ]
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        self.parent_view.kind = self.values[0]
        await interaction.response.defer(ephemeral=True)


class CitationConfirm(discord.ui.Button):
    def __init__(self, parent):
        super().__init__(label="Confirmar citação", emoji="✅", style=discord.ButtonStyle.success)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        if not self.parent_view.target_id or not self.parent_view.kind:
            return await interaction.response.send_message("⚠️ Selecione a pessoa e a participação.", ephemeral=True)
        if not isinstance(interaction.channel, discord.TextChannel) or not interaction.guild:
            return await interaction.response.send_message("❌ Ticket inválido.", ephemeral=True)

        member = interaction.guild.get_member(self.parent_view.target_id)
        if not member:
            return await interaction.response.send_message("❌ Pessoa não encontrada.", ephemeral=True)

        await interaction.channel.set_permissions(member, view_channel=True, send_messages=True,
                                                  read_message_history=True, attach_files=True)
        label = CITATION_TYPES[self.parent_view.kind]
        dm = discord.Embed(
            title="📜 Você foi citado(a) em um processo",
            description=(f"Você foi citado(a) no processo **#{interaction.channel.name}** como **{label}**.\n\n"
                         "O canal do processo já foi liberado para você."),
            color=discord.Color.orange()
        )
        sent = await safe_dm(member, embed=dm)
        embed = discord.Embed(title="📜 Pessoa citada no processo", color=discord.Color.orange())
        embed.add_field(name="Pessoa", value=member.mention)
        embed.add_field(name="Como", value=label)
        embed.add_field(name="Citado por", value=interaction.user.mention)
        embed.add_field(name="Aviso no privado", value="✅ Enviado" if sent else "⚠️ DM fechada", inline=False)
        await interaction.channel.send(embed=embed)
        await interaction.response.send_message("✅ Citação concluída.", ephemeral=True)
        self.parent_view.stop()


class HearingModal(discord.ui.Modal, title="Marcar audiência"):
    detalhes = discord.ui.TextInput(
        label="Informações da audiência",
        placeholder="Escreva data, horário, local, observações e tudo que desejar...",
        style=discord.TextStyle.paragraph,
        min_length=3,
        max_length=3500
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("❌ Use dentro de um ticket.", ephemeral=True)

        embed = discord.Embed(title="📅 AUDIÊNCIA MARCADA", description=str(self.detalhes), color=discord.Color.purple())
        embed.add_field(name="Marcada por", value=interaction.user.mention, inline=False)
        await interaction.channel.send(embed=embed)

        for target, perms in interaction.channel.overwrites.items():
            if isinstance(target, discord.Member) and not target.bot and perms.view_channel is True:
                await safe_dm(target, embed=discord.Embed(
                    title="📅 Audiência marcada",
                    description=f"Processo: **#{interaction.channel.name}**\n\n{self.detalhes}",
                    color=discord.Color.purple()
                ))

        await interaction.response.send_message("✅ Audiência registrada e participantes avisados quando possível.", ephemeral=True)


def transcript_html(channel: discord.TextChannel, messages):
    blocks = []
    for m in messages:
        dt = m.created_at.astimezone(timezone.utc).strftime("%d/%m/%Y %H:%M:%S UTC")
        content = html.escape(m.content or "").replace("\n", "<br>") or "<i>[sem texto]</i>"
        att = ""
        if m.attachments:
            links = [f'<a href="{html.escape(a.url, quote=True)}">{html.escape(a.filename)}</a>' for a in m.attachments]
            att = "<div>Anexos: " + " | ".join(links) + "</div>"
        blocks.append(f'<div class="msg"><div class="meta">{dt} • {html.escape(str(m.author))} ({m.author.id})</div><div>{content}</div>{att}</div>')

    return f'''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><title>Transcript {html.escape(channel.name)}</title>
<style>body{{font-family:Arial;background:#111827;color:#f3f4f6;padding:24px}}h1{{color:#f472b6}}.msg{{background:#1f2937;border:1px solid #374151;border-radius:10px;padding:12px;margin:10px 0}}.meta{{color:#9ca3af;font-size:12px;margin-bottom:8px}}a{{color:#60a5fa}}</style></head>
<body><h1>Transcript #{html.escape(channel.name)}</h1>{''.join(blocks)}</body></html>'''


async def finalize_ticket(interaction: discord.Interaction):
    if not isinstance(interaction.channel, discord.TextChannel) or not interaction.guild:
        return await interaction.response.send_message("❌ Use dentro de um ticket.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)
    log = interaction.guild.get_channel(as_int(CONFIG.get("ticket_log_channel_id")))
    if not isinstance(log, discord.TextChannel):
        return await interaction.followup.send("❌ Canal de transcript não configurado. O ticket não foi apagado.", ephemeral=True)

    messages = [m async for m in interaction.channel.history(limit=None, oldest_first=True)]
    data = io.BytesIO(transcript_html(interaction.channel, messages).encode("utf-8"))
    file = discord.File(data, filename=f"transcript-{interaction.channel.name}.html")

    embed = discord.Embed(title="🗂️ Processo finalizado", color=discord.Color.dark_grey())
    embed.add_field(name="Ticket", value=f"#{interaction.channel.name}")
    embed.add_field(name="Finalizado por", value=interaction.user.mention)
    await log.send(embed=embed, file=file)

    await interaction.followup.send("✅ Processo finalizado. Transcript enviado para o canal de logs.", ephemeral=True)
    await interaction.channel.send("🔒 **Processo finalizado.** Este canal será apagado em 5 segundos.")
    await asyncio.sleep(5)
    await interaction.channel.delete(reason=f"Finalizado por {interaction.user}")


class TicketActionsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Intimar", emoji="📨", style=discord.ButtonStyle.primary, custom_id="action:intimar")
    async def intimar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_staff(interaction):
            return
        await interaction.response.send_message("Selecione a pessoa que será intimada:", view=IntimationSelectView(), ephemeral=True)

    @discord.ui.button(label="Citar", emoji="📜", style=discord.ButtonStyle.secondary, custom_id="action:citar")
    async def citar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_staff(interaction):
            return
        await interaction.response.send_message("Selecione a pessoa e a participação no processo:", view=CitationSetupView(), ephemeral=True)

    @discord.ui.button(label="Audiência", emoji="📅", style=discord.ButtonStyle.success, custom_id="action:audiencia")
    async def audiencia(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_staff(interaction):
            return
        await interaction.response.send_modal(HearingModal())

    @discord.ui.button(label="Finalizar", emoji="🔒", style=discord.ButtonStyle.danger, custom_id="action:finalizar")
    async def finalizar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_staff(interaction):
            return
        await finalize_ticket(interaction)


@bot.tree.command(name="painel_set", description="Envia o painel de solicitação de SET neste canal.")
@app_commands.checks.has_permissions(administrator=True)
async def painel_set(interaction: discord.Interaction):
    embed = discord.Embed(
        title="⚖️ SOLICITAÇÃO DE SET",
        description=("Selecione abaixo o cargo que deseja solicitar.\n\n"
                     "📚 Estagiário\n💼 Advogado\n⚖️ Promotor\n👨‍⚖️ Juiz\n📜 Oficial de Justiça\n\n"
                     "Sua solicitação será enviada para análise."),
        color=discord.Color.magenta()
    )
    await interaction.response.send_message(embed=embed, view=SetPanelView())


@bot.tree.command(name="painel_ticket", description="Envia o painel de abertura de tickets neste canal.")
@app_commands.checks.has_permissions(administrator=True)
async def painel_ticket(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🎫 CENTRAL DE ATENDIMENTO",
        description="Escolha uma opção abaixo:\n\n🛠️ **Suporte**\n📢 **Reclamações**\n🚨 **Denúncia**",
        color=discord.Color.blurple()
    )
    await interaction.response.send_message(embed=embed, view=TicketPanelView())


@bot.tree.command(name="bot_status", description="Confere a configuração básica do bot.")
@app_commands.checks.has_permissions(administrator=True)
async def bot_status(interaction: discord.Interaction):
    guild = interaction.guild
    if not guild:
        return await interaction.response.send_message("❌ Use no servidor.", ephemeral=True)

    checks = [
        ("Categoria de tickets", isinstance(guild.get_channel(as_int(CONFIG.get("ticket_category_id"))), discord.CategoryChannel)),
        ("Canal de transcript", isinstance(guild.get_channel(as_int(CONFIG.get("ticket_log_channel_id"))), discord.TextChannel)),
        ("Canal de análise de SET", isinstance(guild.get_channel(as_int(CONFIG.get("set_review_channel_id"))), discord.TextChannel)),
    ]
    for key, label in SET_LABELS.items():
        checks.append((f"Cargo {label}", get_set_role(guild, key) is not None))

    text = "\n".join(f"{'✅' if ok else '❌'} {name}" for name, ok in checks)
    await interaction.response.send_message(f"**🔧 Status da configuração**\n\n{text}", ephemeral=True)


@bot.tree.error
async def on_tree_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    msg = "❌ Você precisa de permissão de Administrador." if isinstance(error, app_commands.MissingPermissions) else f"❌ Erro: `{error}`"
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


@bot.event
async def on_ready():
    print(f"Conectado como {bot.user} ({bot.user.id})")


token = os.getenv("DISCORD_TOKEN")
if not token:
    raise RuntimeError("Defina DISCORD_TOKEN no arquivo .env")

bot.run(token)
