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
import pymysql

load_dotenv()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

DB_HOST = os.getenv("DB_HOST", "").strip()
DB_PORT = int(os.getenv("DB_PORT", "3306") or 3306)
DB_USER = os.getenv("DB_USER", "").strip()
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "").strip()
DB_CONFIG_ID = 1

DEFAULT_CONFIG = {
    "guild_id": 0,
    "manager_user_id": 0,
    "system_role_id": 0,
    "oab_role_id": 0,
    "set_review_channel_id": 0,
    "ticket_category_id": 0,
    "ticket_log_channel_id": 0,
    "ticket_staff_role_id": 0,
    "ban_channel_id": 0,
    "ban_log_channel_id": 0,
    "set_panel_image_url": "",
    "ticket_panel_image_url": "",
    "set_roles": {
        "estagiario": 0,
        "advogado": 0,
        "promotor": 0,
        "juiz": 0,
        "oficial_justica": 0
    }
}


def _merge_defaults(data, defaults):
    result = dict(defaults)
    for k, v in data.items():
        if isinstance(v, dict) and isinstance(defaults.get(k), dict):
            result[k] = _merge_defaults(v, defaults[k])
        else:
            result[k] = v
    return result


def _db_configured():
    return bool(DB_HOST and DB_USER and DB_NAME)


def _db_connect():
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        charset="utf8mb4",
        autocommit=True,
        connect_timeout=10,
        cursorclass=pymysql.cursors.DictCursor,
    )


def _ensure_config_table():
    if not _db_configured():
        return
    connection = _db_connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_config (
                    id INT NOT NULL PRIMARY KEY,
                    config_json LONGTEXT NOT NULL,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                        ON UPDATE CURRENT_TIMESTAMP
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
                """
            )
    finally:
        connection.close()


def _load_config_from_file():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_local_backup(config_data):
    # Mantém o config.json apenas como backup/exportação.
    # A fonte principal passa a ser o MySQL.
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config_data, f, ensure_ascii=False, indent=2)


def _save_config_to_db(config_data):
    if not _db_configured():
        return False

    _ensure_config_table()
    connection = _db_connect()
    try:
        payload = json.dumps(config_data, ensure_ascii=False)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO bot_config (id, config_json)
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE config_json = VALUES(config_json)
                """,
                (DB_CONFIG_ID, payload),
            )
        return True
    finally:
        connection.close()


def load_config():
    # 1) O MySQL é a fonte principal. Assim os commits/deploys não apagam
    #    as configurações feitas pelo painel /configuracoes.
    if _db_configured():
        try:
            _ensure_config_table()
            connection = _db_connect()
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT config_json FROM bot_config WHERE id = %s LIMIT 1",
                        (DB_CONFIG_ID,),
                    )
                    row = cursor.fetchone()
            finally:
                connection.close()

            if row and row.get("config_json"):
                data = json.loads(row["config_json"])
                if isinstance(data, dict):
                    merged = _merge_defaults(data, DEFAULT_CONFIG)
                    _write_local_backup(merged)
                    print("✅ Configurações carregadas do MySQL.")
                    return merged
        except Exception as error:
            print(f"⚠️ Não foi possível carregar as configurações do MySQL: {error}")

    # 2) Se ainda não existir configuração no banco, usa o config.json atual.
    #    Na primeira inicialização com o banco, ele é migrado automaticamente.
    data = _load_config_from_file()
    merged = _merge_defaults(data, DEFAULT_CONFIG)

    if _db_configured():
        try:
            _save_config_to_db(merged)
            print("✅ Configuração inicial migrada para o MySQL.")
        except Exception as error:
            print(f"⚠️ Não foi possível migrar o config.json para o MySQL: {error}")

    return merged


def save_config():
    # Mantém o arquivo local para o botão 'Exportar config'.
    _write_local_backup(CONFIG)

    # Salva de forma persistente no banco para sobreviver a commits/redeploys.
    if _db_configured():
        try:
            _save_config_to_db(CONFIG)
        except Exception as error:
            print(f"❌ Falha ao salvar configurações no MySQL: {error}")
    else:
        print("⚠️ Banco de dados não configurado. Configuração salva apenas em config.json.")


CONFIG = load_config()


def as_int(v, default=0):
    try:
        return int(v)
    except Exception:
        return default


def has_role(member: discord.Member, role_id: int) -> bool:
    return bool(role_id) and any(r.id == role_id for r in member.roles)


def is_set_staff(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    return has_role(member, as_int(CONFIG.get("system_role_id")))


def is_ticket_staff(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    role_id = as_int(CONFIG.get("ticket_staff_role_id")) or as_int(CONFIG.get("system_role_id"))
    return has_role(member, role_id)


def can_manage_config(member: discord.Member) -> bool:
    if member.guild.owner_id == member.id:
        return True
    manager_id = as_int(CONFIG.get("manager_user_id"))
    if manager_id:
        return member.id == manager_id
    return member.guild_permissions.administrator


async def require_set_staff(interaction: discord.Interaction) -> bool:
    if not isinstance(interaction.user, discord.Member) or not is_set_staff(interaction.user):
        await interaction.response.send_message("❌ Você não tem permissão para aprovar ou negar SET.", ephemeral=True)
        return False
    return True


async def require_ticket_staff(interaction: discord.Interaction) -> bool:
    if not isinstance(interaction.user, discord.Member) or not is_ticket_staff(interaction.user):
        await interaction.response.send_message("❌ Você não tem permissão para usar as ações do ticket.", ephemeral=True)
        return False
    return True


async def require_config_manager(interaction: discord.Interaction) -> bool:
    if not isinstance(interaction.user, discord.Member) or not can_manage_config(interaction.user):
        await interaction.response.send_message("❌ Este painel de configurações é restrito ao gestor do bot.", ephemeral=True)
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
    def __init__(self, key: str, row: int):
        super().__init__(
            label=SET_LABELS[key],
            style=discord.ButtonStyle.danger,
            custom_id=f"set:{key}",
            row=row
        )
        self.key = key

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(SetRequestModal(self.key))


class SetPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        for key, row in [
            ("estagiario", 0), ("advogado", 0), ("promotor", 0),
            ("juiz", 1), ("oficial_justica", 1)
        ]:
            self.add_item(SetRoleButton(key, row))


class SetApprovalView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Aprovar", emoji="✅", style=discord.ButtonStyle.success, custom_id="set:approve")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_set_staff(interaction):
            return
        if not interaction.message or not interaction.message.embeds:
            return await interaction.response.send_message("❌ Solicitação inválida.", ephemeral=True)

        uid, rid = parse_request_footer(interaction.message.embeds[0])
        member = interaction.guild.get_member(uid) if interaction.guild else None
        role = interaction.guild.get_role(rid) if interaction.guild else None
        if not member or not role:
            return await interaction.response.send_message("❌ Usuário ou cargo não encontrado.", ephemeral=True)

        roles_to_add = [role]
        oab_id = as_int(CONFIG.get("oab_role_id"))
        if oab_id:
            oab_role = interaction.guild.get_role(oab_id)
            if oab_role and oab_role not in roles_to_add:
                roles_to_add.append(oab_role)

        try:
            await member.add_roles(*roles_to_add, reason=f"SET aprovado por {interaction.user}")
        except discord.Forbidden:
            return await interaction.response.send_message("❌ O cargo do bot precisa ficar acima dos cargos que serão entregues.", ephemeral=True)

        embed = interaction.message.embeds[0]
        embed.title = "✅ SET aprovado"
        embed.color = discord.Color.green()
        embed.add_field(name="Aprovado por", value=interaction.user.mention, inline=False)
        await interaction.message.edit(embed=embed, view=None)
        await safe_dm(member, embed=discord.Embed(
            title="✅ SET aprovado",
            description=f"Seu SET de **{role.name}** foi aprovado e os cargos configurados foram adicionados.",
            color=discord.Color.green()
        ))
        await interaction.response.send_message("✅ SET aprovado.", ephemeral=True)

    @discord.ui.button(label="Negar", emoji="❌", style=discord.ButtonStyle.danger, custom_id="set:deny")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_set_staff(interaction):
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
    ticket_staff_id = as_int(CONFIG.get("ticket_staff_role_id")) or as_int(CONFIG.get("system_role_id"))
    ticket_staff_role = guild.get_role(ticket_staff_id) if ticket_staff_id else None
    if ticket_staff_role:
        overwrites[ticket_staff_role] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True, attach_files=True
        )

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
        if not await require_ticket_staff(interaction):
            return
        await interaction.response.send_message("Selecione a pessoa que será intimada:", view=IntimationSelectView(), ephemeral=True)

    @discord.ui.button(label="Citar", emoji="📜", style=discord.ButtonStyle.secondary, custom_id="action:citar")
    async def citar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_ticket_staff(interaction):
            return
        await interaction.response.send_message("Selecione a pessoa e a participação no processo:", view=CitationSetupView(), ephemeral=True)

    @discord.ui.button(label="Audiência", emoji="📅", style=discord.ButtonStyle.success, custom_id="action:audiencia")
    async def audiencia(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_ticket_staff(interaction):
            return
        await interaction.response.send_modal(HearingModal())

    @discord.ui.button(label="Finalizar", emoji="🔒", style=discord.ButtonStyle.danger, custom_id="action:finalizar")
    async def finalizar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_ticket_staff(interaction):
            return
        await finalize_ticket(interaction)


def build_set_panel_embed():
    embed = discord.Embed(
        title="SOLICITAÇÃO DE SET",
        description=(
            "**Tribunal de Justiça**\n"
            "Selecione abaixo o cargo que deseja solicitar.\n\n"
            "> Sua solicitação será encaminhada para análise da equipe responsável."
        ),
        color=0xC1121F
    )
    embed.set_footer(text="Tribunal de Justiça • Sistema Integrado")

    image_url = str(CONFIG.get("set_panel_image_url") or "").strip()
    if image_url:
        embed.set_image(url=image_url)
    return embed


def build_ticket_panel_embed():
    embed = discord.Embed(
        title="🎫 CENTRAL DE ATENDIMENTO",
        description="Escolha uma opção abaixo:\n\n🛠️ **Suporte**\n📢 **Reclamações**\n🚨 **Denúncia**",
        color=discord.Color.blurple()
    )
    image_url = str(CONFIG.get("ticket_panel_image_url") or "").strip()
    if image_url:
        embed.set_image(url=image_url)
    return embed


@bot.tree.command(name="painel_set", description="Envia o painel de solicitação de SET neste canal.")
@app_commands.checks.has_permissions(administrator=True)
async def painel_set(interaction: discord.Interaction):
    embed = build_set_panel_embed()
    await interaction.response.send_message(embed=embed, view=SetPanelView())


@bot.tree.command(name="painel_ticket", description="Envia o painel de abertura de tickets neste canal.")
@app_commands.checks.has_permissions(administrator=True)
async def painel_ticket(interaction: discord.Interaction):
    embed = build_ticket_panel_embed()
    await interaction.response.send_message(embed=embed, view=TicketPanelView())



# =========================
# PAINEL DE CONFIGURAÇÕES
# =========================

class ConfigRoleSelect(discord.ui.RoleSelect):
    def __init__(self, key: str, label: str):
        super().__init__(placeholder=f"Selecione: {label}", min_values=1, max_values=1)
        self.key = key
        self.label = label

    async def callback(self, interaction: discord.Interaction):
        if not await require_config_manager(interaction):
            return
        role = self.values[0]
        if self.key.startswith("set_roles."):
            subkey = self.key.split(".", 1)[1]
            CONFIG.setdefault("set_roles", {})[subkey] = role.id
        else:
            CONFIG[self.key] = role.id
        save_config()
        await interaction.response.send_message(f"✅ **{self.label}** definido como {role.mention}.", ephemeral=True)


class ConfigRoleSelectView(discord.ui.View):
    def __init__(self, key: str, label: str):
        super().__init__(timeout=180)
        self.add_item(ConfigRoleSelect(key, label))


class ConfigTextChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, key: str, label: str):
        super().__init__(placeholder=f"Selecione: {label}", channel_types=[discord.ChannelType.text], min_values=1, max_values=1)
        self.key = key
        self.label = label

    async def callback(self, interaction: discord.Interaction):
        if not await require_config_manager(interaction):
            return
        channel = self.values[0]
        CONFIG[self.key] = channel.id
        save_config()
        await interaction.response.send_message(f"✅ **{self.label}** definido como {channel.mention}.", ephemeral=True)


class ConfigTextChannelSelectView(discord.ui.View):
    def __init__(self, key: str, label: str):
        super().__init__(timeout=180)
        self.add_item(ConfigTextChannelSelect(key, label))


class ConfigCategorySelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(placeholder="Selecione a categoria dos tickets", channel_types=[discord.ChannelType.category], min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        if not await require_config_manager(interaction):
            return
        category = self.values[0]
        CONFIG["ticket_category_id"] = category.id
        save_config()
        await interaction.response.send_message(f"✅ Categoria dos tickets definida como **{category.name}**.", ephemeral=True)


class ConfigCategorySelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(ConfigCategorySelect())


class ConfigManagerSelect(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(placeholder="Selecione quem poderá usar o painel de configurações", min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild or interaction.user.id != interaction.guild.owner_id:
            return await interaction.response.send_message("❌ Somente o dono do servidor pode alterar o gestor do bot.", ephemeral=True)
        user = self.values[0]
        CONFIG["manager_user_id"] = user.id
        CONFIG["guild_id"] = interaction.guild.id
        save_config()
        await interaction.response.send_message(f"✅ Gestor do bot definido como {user.mention}.\nO dono do servidor continuará com acesso de segurança.", ephemeral=True)


class ConfigManagerSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(ConfigManagerSelect())


class PanelImageModal(discord.ui.Modal):
    def __init__(self, key: str, title_text: str):
        super().__init__(title=title_text)
        self.key = key
        self.url = discord.ui.TextInput(
            label="URL direta da imagem",
            placeholder="https://.../imagem.png  |  deixe vazio para remover",
            required=False,
            max_length=1000
        )
        self.add_item(self.url)

    async def on_submit(self, interaction: discord.Interaction):
        if not await require_config_manager(interaction):
            return
        value = str(self.url).strip()
        if value and not (value.startswith("https://") or value.startswith("http://")):
            return await interaction.response.send_message("❌ Informe um link http:// ou https:// válido.", ephemeral=True)
        CONFIG[self.key] = value
        save_config()
        await interaction.response.send_message("✅ Imagem atualizada." if value else "✅ Imagem removida.", ephemeral=True)


def config_summary_embed(guild: discord.Guild):
    def role_text(key, nested=False):
        rid = as_int(CONFIG.get("set_roles", {}).get(key)) if nested else as_int(CONFIG.get(key))
        role = guild.get_role(rid) if rid else None
        return role.mention if role else "❌ Não configurado"

    def channel_text(key):
        cid = as_int(CONFIG.get(key))
        ch = guild.get_channel(cid) if cid else None
        return ch.mention if isinstance(ch, discord.TextChannel) else "❌ Não configurado"

    cat = guild.get_channel(as_int(CONFIG.get("ticket_category_id")))
    manager = guild.get_member(as_int(CONFIG.get("manager_user_id")))

    embed = discord.Embed(title="⚙️ CONFIGURAÇÕES DO BOT JURÍDICO", color=discord.Color.dark_magenta())
    embed.description = "Use os botões abaixo para configurar o bot sem editar IDs manualmente."
    embed.add_field(name="👤 Gestor", value=manager.mention if manager else "⚠️ Ainda não definido", inline=False)
    embed.add_field(
        name="⚖️ SET",
        value=(
            f"Canal de aprovação: {channel_text('set_review_channel_id')}\n"
            f"Cargo Sistema: {role_text('system_role_id')}\n"
            f"Cargo OAB: {role_text('oab_role_id')}\n"
            f"Estagiário: {role_text('estagiario', True)}\n"
            f"Advogado: {role_text('advogado', True)}\n"
            f"Promotor: {role_text('promotor', True)}\n"
            f"Juiz: {role_text('juiz', True)}\n"
            f"Oficial de Justiça: {role_text('oficial_justica', True)}"
        ), inline=False
    )
    ticket_staff = role_text("ticket_staff_role_id")
    if ticket_staff == "❌ Não configurado":
        ticket_staff = f"Usará Cargo Sistema ({role_text('system_role_id')})"
    embed.add_field(
        name="🎫 TICKETS",
        value=(
            f"Categoria: **{cat.name}**" if isinstance(cat, discord.CategoryChannel) else "Categoria: ❌ Não configurada"
        ) + f"\nCanal de transcript: {channel_text('ticket_log_channel_id')}\nEquipe de tickets: {ticket_staff}",
        inline=False
    )
    embed.add_field(
        name="🚨 BAN AUTOMÁTICO",
        value=(
            f"Canal de ban: {channel_text('ban_channel_id')}\n"
            f"Canal de log: {channel_text('ban_log_channel_id')}"
        ),
        inline=False
    )
    embed.add_field(
        name="🖼️ Imagens",
        value=(
            f"Painel SET: {'✅ Configurada' if CONFIG.get('set_panel_image_url') else 'Não configurada'}\n"
            f"Painel Ticket: {'✅ Configurada' if CONFIG.get('ticket_panel_image_url') else 'Não configurada'}"
        ), inline=False
    )
    return embed


class SetConfigView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    async def _role(self, interaction, key, label):
        if not await require_config_manager(interaction): return
        await interaction.response.send_message(f"Selecione o cargo para **{label}**:", view=ConfigRoleSelectView(key, label), ephemeral=True)

    @discord.ui.button(label="Canal aprovar SET", emoji="📥", style=discord.ButtonStyle.primary, row=0)
    async def review_channel(self, interaction, button):
        if not await require_config_manager(interaction): return
        await interaction.response.send_message("Selecione o canal onde os pedidos de SET ficarão aguardando aprovação:", view=ConfigTextChannelSelectView("set_review_channel_id", "Canal de aprovação de SET"), ephemeral=True)

    @discord.ui.button(label="Cargo Sistema", emoji="🛡️", style=discord.ButtonStyle.primary, row=0)
    async def system_role(self, interaction, button): await self._role(interaction, "system_role_id", "Cargo Sistema")

    @discord.ui.button(label="Cargo OAB", emoji="⚖️", style=discord.ButtonStyle.primary, row=0)
    async def oab_role(self, interaction, button): await self._role(interaction, "oab_role_id", "Cargo OAB")

    @discord.ui.button(label="Estagiário", emoji="📚", style=discord.ButtonStyle.secondary, row=1)
    async def estagiario(self, interaction, button): await self._role(interaction, "set_roles.estagiario", "Estagiário")

    @discord.ui.button(label="Advogado", emoji="💼", style=discord.ButtonStyle.secondary, row=1)
    async def advogado(self, interaction, button): await self._role(interaction, "set_roles.advogado", "Advogado")

    @discord.ui.button(label="Promotor", emoji="⚖️", style=discord.ButtonStyle.secondary, row=1)
    async def promotor(self, interaction, button): await self._role(interaction, "set_roles.promotor", "Promotor")

    @discord.ui.button(label="Juiz", emoji="👨‍⚖️", style=discord.ButtonStyle.secondary, row=2)
    async def juiz(self, interaction, button): await self._role(interaction, "set_roles.juiz", "Juiz")

    @discord.ui.button(label="Oficial de Justiça", emoji="📜", style=discord.ButtonStyle.secondary, row=2)
    async def oficial(self, interaction, button): await self._role(interaction, "set_roles.oficial_justica", "Oficial de Justiça")

    @discord.ui.button(label="Imagem painel SET", emoji="🖼️", style=discord.ButtonStyle.success, row=2)
    async def imagem(self, interaction, button):
        if not await require_config_manager(interaction): return
        await interaction.response.send_modal(PanelImageModal("set_panel_image_url", "Imagem do painel de SET"))


class TicketConfigView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Categoria dos tickets", emoji="📁", style=discord.ButtonStyle.primary, row=0)
    async def category(self, interaction, button):
        if not await require_config_manager(interaction): return
        await interaction.response.send_message("Selecione a categoria onde os tickets serão criados:", view=ConfigCategorySelectView(), ephemeral=True)

    @discord.ui.button(label="Canal transcript", emoji="🗂️", style=discord.ButtonStyle.primary, row=0)
    async def logs(self, interaction, button):
        if not await require_config_manager(interaction): return
        await interaction.response.send_message("Selecione o canal que receberá os transcripts:", view=ConfigTextChannelSelectView("ticket_log_channel_id", "Canal de transcript"), ephemeral=True)

    @discord.ui.button(label="Equipe de tickets", emoji="👥", style=discord.ButtonStyle.primary, row=0)
    async def staff(self, interaction, button):
        if not await require_config_manager(interaction): return
        await interaction.response.send_message("Selecione o cargo que poderá ver e usar as ações dos tickets. Se não configurar, o bot usa o Cargo Sistema:", view=ConfigRoleSelectView("ticket_staff_role_id", "Equipe de tickets"), ephemeral=True)

    @discord.ui.button(label="Imagem painel Ticket", emoji="🖼️", style=discord.ButtonStyle.success, row=1)
    async def imagem(self, interaction, button):
        if not await require_config_manager(interaction): return
        await interaction.response.send_modal(PanelImageModal("ticket_panel_image_url", "Imagem do painel de Ticket"))


class BanConfigView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Canal de ban", emoji="🚫", style=discord.ButtonStyle.danger, row=0)
    async def ban_channel(self, interaction, button):
        if not await require_config_manager(interaction): return
        await interaction.response.send_message(
            "Selecione o canal onde qualquer usuário que escrever será banido automaticamente:",
            view=ConfigTextChannelSelectView("ban_channel_id", "Canal de ban automático"),
            ephemeral=True
        )

    @discord.ui.button(label="Canal de log do ban", emoji="🗂️", style=discord.ButtonStyle.secondary, row=0)
    async def ban_log_channel(self, interaction, button):
        if not await require_config_manager(interaction): return
        await interaction.response.send_message(
            "Selecione o canal que receberá os registros dos banimentos automáticos:",
            view=ConfigTextChannelSelectView("ban_log_channel_id", "Canal de log dos banimentos"),
            ephemeral=True
        )


class ConfigMainView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)

    @discord.ui.button(label="Configurar SET", emoji="⚖️", style=discord.ButtonStyle.primary, row=0)
    async def set_config(self, interaction, button):
        if not await require_config_manager(interaction): return
        await interaction.response.send_message("⚙️ **Configuração do sistema de SET**", view=SetConfigView(), ephemeral=True)

    @discord.ui.button(label="Configurar Tickets", emoji="🎫", style=discord.ButtonStyle.primary, row=0)
    async def ticket_config(self, interaction, button):
        if not await require_config_manager(interaction): return
        await interaction.response.send_message("⚙️ **Configuração do sistema de tickets**", view=TicketConfigView(), ephemeral=True)

    @discord.ui.button(label="Configurar Ban", emoji="🚨", style=discord.ButtonStyle.danger, row=0)
    async def ban_config(self, interaction, button):
        if not await require_config_manager(interaction): return
        await interaction.response.send_message("⚙️ **Configuração do banimento automático**", view=BanConfigView(), ephemeral=True)

    @discord.ui.button(label="Ver configuração", emoji="🔎", style=discord.ButtonStyle.secondary, row=0)
    async def summary(self, interaction, button):
        if not await require_config_manager(interaction): return
        await interaction.response.send_message(embed=config_summary_embed(interaction.guild), ephemeral=True)

    @discord.ui.button(label="Definir gestor", emoji="👤", style=discord.ButtonStyle.secondary, row=1)
    async def manager(self, interaction, button):
        if not interaction.guild or interaction.user.id != interaction.guild.owner_id:
            return await interaction.response.send_message("❌ Somente o dono do servidor pode definir o gestor do bot.", ephemeral=True)
        await interaction.response.send_message("Selecione quem terá acesso ao painel de configurações:", view=ConfigManagerSelectView(), ephemeral=True)

    @discord.ui.button(label="Publicar painel SET aqui", emoji="📌", style=discord.ButtonStyle.success, row=1)
    async def publish_set(self, interaction, button):
        if not await require_config_manager(interaction): return
        await interaction.channel.send(embed=build_set_panel_embed(), view=SetPanelView())
        await interaction.response.send_message("✅ Painel de SET publicado neste canal.", ephemeral=True)

    @discord.ui.button(label="Publicar painel Ticket aqui", emoji="📌", style=discord.ButtonStyle.success, row=1)
    async def publish_ticket(self, interaction, button):
        if not await require_config_manager(interaction): return
        await interaction.channel.send(embed=build_ticket_panel_embed(), view=TicketPanelView())
        await interaction.response.send_message("✅ Painel de tickets publicado neste canal.", ephemeral=True)

    @discord.ui.button(label="Exportar config", emoji="💾", style=discord.ButtonStyle.secondary, row=2)
    async def export_config(self, interaction, button):
        if not await require_config_manager(interaction): return
        save_config()
        await interaction.response.send_message("💾 Backup atual da configuração:", file=discord.File(CONFIG_PATH, filename="config.json"), ephemeral=True)


@bot.tree.command(name="configuracoes", description="Abre o painel de configurações do Bot Jurídico.")
async def configuracoes(interaction: discord.Interaction):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message("❌ Use este comando dentro do servidor.", ephemeral=True)
    if not await require_config_manager(interaction):
        return
    if not as_int(CONFIG.get("guild_id")):
        CONFIG["guild_id"] = interaction.guild.id
        save_config()
    await interaction.response.send_message(embed=config_summary_embed(interaction.guild), view=ConfigMainView(), ephemeral=True)


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
async def on_message(message: discord.Message):
    # Mantém o restante do bot intacto e atua somente no canal configurado para ban automático.
    if message.author.bot or not message.guild:
        return

    guild_id = as_int(CONFIG.get("guild_id"))
    if guild_id and message.guild.id != guild_id:
        return

    ban_channel_id = as_int(CONFIG.get("ban_channel_id"))
    if not ban_channel_id or message.channel.id != ban_channel_id:
        return

    log_channel_id = as_int(CONFIG.get("ban_log_channel_id"))
    log_channel = message.guild.get_channel(log_channel_id) if log_channel_id else None

    author_name = str(message.author)
    author_id = message.author.id
    content = (message.content or "[sem conteúdo de texto]")[:1000]

    try:
        await message.author.ban(
            reason=f"Mensagem enviada no canal de ban automático #{message.channel.name}"
        )
    except discord.Forbidden:
        if isinstance(log_channel, discord.TextChannel):
            embed = discord.Embed(
                title="⚠️ Falha no banimento automático",
                description="O bot não conseguiu banir o usuário. Verifique a permissão **Banir Membros** e a hierarquia de cargos.",
                color=discord.Color.orange(),
                timestamp=discord.utils.utcnow()
            )
            embed.add_field(name="Usuário", value=f"{author_name} (`{author_id}`)", inline=False)
            embed.add_field(name="Canal", value=message.channel.mention, inline=False)
            await log_channel.send(embed=embed)
        return
    except discord.HTTPException as error:
        if isinstance(log_channel, discord.TextChannel):
            embed = discord.Embed(
                title="⚠️ Erro no banimento automático",
                description=f"O Discord recusou a operação: `{error}`",
                color=discord.Color.orange(),
                timestamp=discord.utils.utcnow()
            )
            embed.add_field(name="Usuário", value=f"{author_name} (`{author_id}`)", inline=False)
            embed.add_field(name="Canal", value=message.channel.mention, inline=False)
            await log_channel.send(embed=embed)
        return

    try:
        await message.delete()
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        pass

    if isinstance(log_channel, discord.TextChannel):
        embed = discord.Embed(
            title="🚨 Banimento automático realizado",
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="Usuário banido", value=f"{author_name} (`{author_id}`)", inline=False)
        embed.add_field(name="Canal", value=message.channel.mention, inline=True)
        embed.add_field(name="Ação", value="Banimento automático", inline=True)
        embed.add_field(name="Mensagem enviada", value=content, inline=False)
        embed.set_footer(text="Bot Jurídico • Sistema de Segurança")
        await log_channel.send(embed=embed)


@bot.event
async def on_ready():
    print(f"Conectado como {bot.user} ({bot.user.id})")


token = os.getenv("DISCORD_TOKEN")
if not token:
    raise RuntimeError("Defina DISCORD_TOKEN no arquivo .env")

bot.run(token)