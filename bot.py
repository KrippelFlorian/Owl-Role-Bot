import discord
from discord.ext import commands
from discord import app_commands
import json
import os
import asyncio
from datetime import datetime

# ── Config ────────────────────────────────────────────────────────────────────
LFG_CATEGORY_NAME = "Looking For Group"  # category that holds temp channels
MENU_CHANNEL_NAME = "game-menu"          # channel where the UI panels live
DATA_FILE         = "games_data.json"    # stores who plays what
PING_COOLDOWN     = 300                  # seconds between /lfg calls per game (5 min)
CHANNEL_TIMEOUT   = 18000               # seconds of inactivity before auto-delete (5 hours)

GAMES = [
    "Grounded", "Phasmophobia", "Minecraft", "Calamity", "Isaac",
    "Valorant", "Liars Bar", "Content Warning", "Among Us", "Satisfactory",
    "Stardew Valley", "Portal", "Sea Of Thieves", "Rust", "Helldivers 2",
    "Marvel Rivals", "GeoGuessr", "Apex Legends", "Rabbit and Steel", "Split Fiction",
    "Brawl Stars", "Goat Simulator", "Mario Kart World", "Red Dead Redemption 2",
    "Rocket League", "Fall Guys", "Peak", "Ready Or Not", "SpeedRunners",
    "Ultimate Chicken Horse", "Terraria", "Overwatch", "Euro Truck Simulator",
    "Rematch", "Subnautica 2", "Eldenring / Nightreign",
]

# Discord select menus max 25 options, so split into pages of 25
GAME_PAGES = [GAMES[i:i+25] for i in range(0, len(GAMES), 25)]

# ── Storage helpers ───────────────────────────────────────────────────────────
def load_data() -> dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE) as f:
            return json.load(f)
    return {"subscriptions": {}, "last_ping": {}}

def save_data(data: dict):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ── Bot setup ─────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.members  = True
intents.messages = True
intents.message_content = True

bot  = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

active_channels: dict[int, asyncio.Task] = {}

# ── LFG helpers ───────────────────────────────────────────────────────────────
def channel_name_for(game: str) -> str:
    return "lfg-" + game.lower().replace(" ", "-").replace("/", "").replace("--", "-")

async def get_or_create_category(guild: discord.Guild) -> discord.CategoryChannel:
    cat = discord.utils.get(guild.categories, name=LFG_CATEGORY_NAME)
    if not cat:
        cat = await guild.create_category(LFG_CATEGORY_NAME)
    return cat

async def schedule_deletion(channel: discord.TextChannel, delay: int):
    await asyncio.sleep(delay)
    try:
        await channel.send(
            f"⏰ This channel has been inactive for **{delay // 3600} hours** and will now be deleted."
        )
        await asyncio.sleep(5)
        await channel.delete(reason="LFG session expired")
    except discord.NotFound:
        pass
    active_channels.pop(channel.id, None)

def reset_timer(channel: discord.TextChannel):
    old_task = active_channels.get(channel.id)
    if old_task:
        old_task.cancel()
    task = asyncio.create_task(schedule_deletion(channel, CHANNEL_TIMEOUT))
    active_channels[channel.id] = task

async def run_lfg(interaction: discord.Interaction, game: str, message: str = ""):
    """Core LFG logic shared by slash command and UI button."""
    await interaction.response.defer(ephemeral=True)

    data  = load_data()
    guild = interaction.guild
    subs  = data["subscriptions"]

    cooldown_key = f"{interaction.user.id}:{game}"
    last = data["last_ping"].get(cooldown_key)
    if last:
        elapsed = (datetime.utcnow() - datetime.fromisoformat(last)).total_seconds()
        if elapsed < PING_COOLDOWN:
            remaining = int(PING_COOLDOWN - elapsed)
            await interaction.followup.send(
                f"⏳ Please wait **{remaining}s** before opening another **{game}** session.",
                ephemeral=True
            )
            return

    members_with_game: list[discord.Member] = []
    for uid, games in subs.items():
        if game in games:
            member = guild.get_member(int(uid))
            if member:
                members_with_game.append(member)

    if interaction.user not in members_with_game:
        members_with_game.append(interaction.user)

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
    }
    for member in members_with_game:
        overwrites[member] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

    category  = await get_or_create_category(guild)
    chan_name = channel_name_for(game)
    existing  = discord.utils.get(category.text_channels, name=chan_name)

    if existing:
        await existing.set_permissions(interaction.user, view_channel=True, send_messages=True)
        reset_timer(existing)
        extra = f"\n> {message}" if message else ""
        await existing.send(f"🎮 **{interaction.user.display_name}** also wants to play!{extra}")
        await interaction.followup.send(f"✅ A session is already open: {existing.mention}", ephemeral=True)
        return

    channel  = await category.create_text_channel(name=chan_name, overwrites=overwrites)
    mentions = " ".join(m.mention for m in members_with_game if m != interaction.user)
    extra    = f"\n> {message}" if message else ""

    if mentions:
        intro = (
            f"🎮 **{interaction.user.display_name}** is looking for people to play **{game}**!{extra}\n"
            f"{mentions}\n\n"
            f"🔒 Only people with **{game}** on their list can see this channel.\n"
            f"⏰ It will auto-delete after **{CHANNEL_TIMEOUT // 3600} hours** of inactivity."
        )
    else:
        intro = (
            f"🎮 **{interaction.user.display_name}** is looking for people to play **{game}**!{extra}\n\n"
            f"*Nobody else has this game on their list yet — tell your friends to add it via the game menu!*\n"
            f"⏰ This channel will auto-delete after **{CHANNEL_TIMEOUT // 3600} hours** of inactivity."
        )

    await channel.send(intro)
    reset_timer(channel)

    data["last_ping"][cooldown_key] = datetime.utcnow().isoformat()
    save_data(data)

    await interaction.followup.send(f"✅ Created a private session: {channel.mention}", ephemeral=True)

# ── UI: game select menus ─────────────────────────────────────────────────────
def make_game_options(page: list[str]) -> list[discord.SelectOption]:
    return [discord.SelectOption(label=g, value=g) for g in page]

# ── Panel 1: Manage my games ──────────────────────────────────────────────────
class AddGameSelect(discord.ui.Select):
    def __init__(self, page: list[str]):
        super().__init__(
            placeholder="Select a game to add...",
            options=make_game_options(page),
            custom_id=f"add_game_{page[0]}",
        )

    async def callback(self, interaction: discord.Interaction):
        game = self.values[0]
        data = load_data()
        uid  = str(interaction.user.id)
        subs = data["subscriptions"]
        if uid not in subs:
            subs[uid] = []
        if game in subs[uid]:
            await interaction.response.send_message(f"You already have **{game}** on your list.", ephemeral=True)
            return
        subs[uid].append(game)
        save_data(data)
        await interaction.response.send_message(f"✅ Added **{game}** to your list!", ephemeral=True)

class RemoveGameSelect(discord.ui.Select):
    def __init__(self, page: list[str]):
        super().__init__(
            placeholder="Select a game to remove...",
            options=make_game_options(page),
            custom_id=f"remove_game_{page[0]}",
        )

    async def callback(self, interaction: discord.Interaction):
        game = self.values[0]
        data = load_data()
        uid  = str(interaction.user.id)
        subs = data["subscriptions"]
        if uid not in subs or game not in subs[uid]:
            await interaction.response.send_message(f"**{game}** is not on your list.", ephemeral=True)
            return
        subs[uid].remove(game)
        save_data(data)
        await interaction.response.send_message(f"🗑️ Removed **{game}** from your list.", ephemeral=True)

class MyGamesButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="📋 My Games", style=discord.ButtonStyle.secondary, custom_id="my_games")

    async def callback(self, interaction: discord.Interaction):
        data = load_data()
        uid  = str(interaction.user.id)
        subs = data["subscriptions"].get(uid, [])
        if not subs:
            await interaction.response.send_message(
                "You haven't added any games yet. Use the dropdowns above!", ephemeral=True
            )
            return
        game_list = "\n".join(f"• {g}" for g in sorted(subs))
        await interaction.response.send_message(f"**Your games ({len(subs)}):**\n{game_list}", ephemeral=True)

class ManageGamesView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        # Add a dropdown per page of games
        for page in GAME_PAGES:
            self.add_item(AddGameSelect(page))
        for page in GAME_PAGES:
            self.add_item(RemoveGameSelect(page))
        self.add_item(MyGamesButton())

# ── Panel 2: Looking for group ────────────────────────────────────────────────
class LFGSelect(discord.ui.Select):
    def __init__(self, page: list[str]):
        super().__init__(
            placeholder="Select a game to find players...",
            options=make_game_options(page),
            custom_id=f"lfg_{page[0]}",
        )

    async def callback(self, interaction: discord.Interaction):
        await run_lfg(interaction, self.values[0])

class LFGView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        for page in GAME_PAGES:
            self.add_item(LFGSelect(page))

# ── Panel 3: Browse players ───────────────────────────────────────────────────
class GameUsersSelect(discord.ui.Select):
    def __init__(self, page: list[str]):
        super().__init__(
            placeholder="Who plays...?",
            options=make_game_options(page),
            custom_id=f"game_users_{page[0]}",
        )

    async def callback(self, interaction: discord.Interaction):
        game  = self.values[0]
        data  = load_data()
        guild = interaction.guild
        players = []
        for uid, games in data["subscriptions"].items():
            if game in games:
                member = guild.get_member(int(uid))
                if member:
                    players.append(member.display_name)
        if not players:
            await interaction.response.send_message(f"Nobody has **{game}** on their list yet.", ephemeral=True)
            return
        player_list = "\n".join(f"• {p}" for p in sorted(players))
        await interaction.response.send_message(
            f"**{len(players)} player(s) with {game}:**\n{player_list}", ephemeral=True
        )

class UserGamesButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="🔍 Look up a user's games", style=discord.ButtonStyle.secondary, custom_id="user_games_prompt")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "Use `/games user_games @user` to look up a specific user's games.",
            ephemeral=True
        )

class BrowseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        for page in GAME_PAGES:
            self.add_item(GameUsersSelect(page))
        self.add_item(UserGamesButton())

# ── Post / refresh menu panels ────────────────────────────────────────────────
async def post_menu(guild: discord.Guild):
    """Find or create #game-menu and post/update the three UI panels."""
    channel = discord.utils.get(guild.text_channels, name=MENU_CHANNEL_NAME)
    if not channel:
        channel = await guild.create_text_channel(MENU_CHANNEL_NAME)

    # Wipe old bot messages so we always have fresh panels
    await channel.purge(limit=20, check=lambda m: m.author == guild.me)

    embed1 = discord.Embed(
        title="🎮 Manage Your Games",
        description=(
            "Use the dropdowns to add or remove games from your list.\n"
            "You'll automatically get access to private LFG channels for games on your list."
        ),
        color=0x5865F2,
    )
    await channel.send(embed=embed1, view=ManageGamesView())

    embed2 = discord.Embed(
        title="🔍 Looking For Group",
        description=(
            "Select a game to open a private channel and ping everyone who plays it.\n"
            "The channel auto-deletes after **5 hours** of inactivity."
        ),
        color=0x57F287,
    )
    await channel.send(embed=embed2, view=LFGView())

    embed3 = discord.Embed(
        title="👥 Browse Players",
        description="See who else has a specific game on their list.",
        color=0xFEE75C,
    )
    await channel.send(embed=embed3, view=BrowseView())

# ── /admin command group ──────────────────────────────────────────────────────
admin_group = app_commands.Group(
    name="admin",
    description="Admin tools (requires Manage Server permission)",
    default_permissions=discord.Permissions(manage_guild=True),
)

@admin_group.command(name="import_roles", description="Import everyone's game roles into the bot's database")
async def admin_import_roles(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    data  = load_data()
    subs  = data["subscriptions"]
    game_lookup    = {g.lower(): g for g in GAMES}
    imported_users = 0
    imported_subs  = 0
    for member in guild.members:
        if member.bot:
            continue
        matched = []
        for role in member.roles:
            canonical = game_lookup.get(role.name.lower())
            if canonical:
                matched.append(canonical)
        if matched:
            uid       = str(member.id)
            existing  = set(subs.get(uid, []))
            new_games = existing | set(matched)
            subs[uid] = list(new_games)
            imported_users += 1
            imported_subs  += len(new_games - existing)
    save_data(data)
    await interaction.followup.send(
        f"✅ Import complete! Found game roles for **{imported_users} members**, "
        f"added **{imported_subs} new subscriptions** to the database.\n"
        f"You can now safely delete the game roles from your server.",
        ephemeral=True
    )

@admin_group.command(name="refresh_menu", description="Re-post the game menu panels in #game-menu")
async def admin_refresh_menu(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await post_menu(interaction.guild)
    await interaction.followup.send("✅ Menu refreshed!", ephemeral=True)

@admin_group.command(name="user_games", description="See all games a specific user has on their list")
@app_commands.describe(user="The user to look up")
async def admin_user_games(interaction: discord.Interaction, user: discord.Member):
    data = load_data()
    uid  = str(user.id)
    subs = data["subscriptions"].get(uid, [])
    if not subs:
        await interaction.response.send_message(f"**{user.display_name}** has no games on their list.", ephemeral=True)
        return
    game_list = "\n".join(f"• {g}" for g in sorted(subs))
    await interaction.response.send_message(
        f"**{user.display_name}'s games ({len(subs)}):**\n{game_list}", ephemeral=True
    )

tree.add_command(admin_group)

# ── Keep UI alive after restart ───────────────────────────────────────────────
bot.add_view(ManageGamesView())
bot.add_view(LFGView())
bot.add_view(BrowseView())

# ── Reset inactivity timer on messages ────────────────────────────────────────
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if message.channel.id in active_channels:
        reset_timer(message.channel)
    await bot.process_commands(message)

# ── Startup ───────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    await tree.sync()
    for guild in bot.guilds:
        await post_menu(guild)
    print(f"✅ Logged in as {bot.user} — slash commands synced, menus posted.")

# ── Run ───────────────────────────────────────────────────────────────────────
TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("Set the DISCORD_TOKEN environment variable before running.")
bot.run(TOKEN)