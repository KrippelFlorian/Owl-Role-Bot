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

    # Only allow LFG if the user has the game on their list
    if game not in subs.get(str(interaction.user.id), []):
        await interaction.followup.send(
            f"❌ You need to add **{game}** to your list first before looking for players!",
            ephemeral=True
        )
        return

    members_with_game: list[discord.Member] = []
    for uid, games in subs.items():
        if game in games:
            member = guild.get_member(int(uid))
            if member:
                members_with_game.append(member)

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

# ── UI: per-game button rows ──────────────────────────────────────────────────
# Each game gets one row: [🎮 Game Name] [➕ Add] [➖ Remove] [🔍 LFG] [👥 Who plays?]
# Discord allows max 5 rows of buttons per message → 5 games per message.
# With 36 games that means 8 messages, which is fine.

GAMES_PER_MESSAGE = 5  # 5 rows × 1 game each

def make_game_rows(games: list[str]) -> list[discord.ui.View]:
    """Return one View per chunk of GAMES_PER_MESSAGE games."""
    views = []
    for chunk_start in range(0, len(games), GAMES_PER_MESSAGE):
        chunk = games[chunk_start:chunk_start + GAMES_PER_MESSAGE]
        view  = discord.ui.View(timeout=None)
        for game in chunk:
            safe = game.replace(" ", "_").replace("/", "")[:50]
            # Game label button (disabled, just for display)
            view.add_item(discord.ui.Button(
                label=game,
                style=discord.ButtonStyle.secondary,
                custom_id=f"label_{safe}",
                disabled=True,
                row=chunk.index(game),
            ))
            # ➕ Add
            add_btn = discord.ui.Button(
                label="➕ Add",
                style=discord.ButtonStyle.success,
                custom_id=f"add_{safe}",
                row=chunk.index(game),
            )
            async def add_cb(interaction: discord.Interaction, g=game):
                data = load_data()
                uid  = str(interaction.user.id)
                subs = data["subscriptions"]
                if uid not in subs:
                    subs[uid] = []
                if g in subs[uid]:
                    await interaction.response.send_message(f"You already have **{g}** on your list.", ephemeral=True)
                    return
                subs[uid].append(g)
                save_data(data)
                # If an LFG channel for this game is already open, grant access immediately
                guild = interaction.guild
                category = discord.utils.get(guild.categories, name=LFG_CATEGORY_NAME)
                if category:
                    existing = discord.utils.get(category.text_channels, name=channel_name_for(g))
                    if existing:
                        await existing.set_permissions(interaction.user, view_channel=True, send_messages=True)
                        await interaction.response.send_message(
                            f"✅ Added **{g}** to your list! There's already an open session: {existing.mention}",
                            ephemeral=True
                        )
                        return
                await interaction.response.send_message(f"✅ Added **{g}** to your list!", ephemeral=True)
            add_btn.callback = add_cb
            view.add_item(add_btn)

            # ➖ Remove
            rem_btn = discord.ui.Button(
                label="➖ Remove",
                style=discord.ButtonStyle.danger,
                custom_id=f"remove_{safe}",
                row=chunk.index(game),
            )
            async def rem_cb(interaction: discord.Interaction, g=game):
                data = load_data()
                uid  = str(interaction.user.id)
                subs = data["subscriptions"]
                if uid not in subs or g not in subs[uid]:
                    await interaction.response.send_message(f"**{g}** is not on your list.", ephemeral=True)
                    return
                subs[uid].remove(g)
                save_data(data)
                await interaction.response.send_message(f"🗑️ Removed **{g}** from your list.", ephemeral=True)
            rem_btn.callback = rem_cb
            view.add_item(rem_btn)

            # 🔍 LFG
            lfg_btn = discord.ui.Button(
                label="🔍 LFG",
                style=discord.ButtonStyle.primary,
                custom_id=f"lfg_{safe}",
                row=chunk.index(game),
            )
            async def lfg_cb(interaction: discord.Interaction, g=game):
                await run_lfg(interaction, g)
            lfg_btn.callback = lfg_cb
            view.add_item(lfg_btn)

            # 👥 Who plays?
            who_btn = discord.ui.Button(
                label="👥 Who plays?",
                style=discord.ButtonStyle.secondary,
                custom_id=f"who_{safe}",
                row=chunk.index(game),
            )
            async def who_cb(interaction: discord.Interaction, g=game):
                data  = load_data()
                guild = interaction.guild
                players = []
                for uid, games in data["subscriptions"].items():
                    if g in games:
                        member = guild.get_member(int(uid))
                        if member:
                            players.append(member.display_name)
                if not players:
                    await interaction.response.send_message(f"Nobody has **{g}** on their list yet.", ephemeral=True)
                    return
                player_list = "\n".join(f"• {p}" for p in sorted(players))
                await interaction.response.send_message(
                    f"**{len(players)} player(s) with {g}:**\n{player_list}", ephemeral=True
                )
            who_btn.callback = who_cb
            view.add_item(who_btn)

        views.append(view)
    return views

# ── Post / refresh menu ───────────────────────────────────────────────────────
async def post_menu(guild: discord.Guild):
    channel = discord.utils.get(guild.text_channels, name=MENU_CHANNEL_NAME)
    if not channel:
        channel = await guild.create_text_channel(MENU_CHANNEL_NAME)

    # Wipe old bot messages
    await channel.purge(limit=100, check=lambda m: m.author == guild.me)

    # Header
    header = discord.Embed(
        title="🎮 Game Menu",
        description=(
            "**➕ Add** — add a game to your list\n"
            "**➖ Remove** — remove a game from your list\n"
            "**🔍 LFG** — open a private channel to find players\n"
            "**👥 Who plays?** — see who has this game on their list\n\n"
            "*All responses are only visible to you.*"
        ),
        color=0x5865F2,
    )
    await channel.send(embed=header)

    # One message per 5 games
    views = make_game_rows(GAMES)
    for view in views:
        await channel.send(view=view)




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

@tree.command(name="usergames", description="See all games a specific user has on their list")
@app_commands.describe(user="The user to look up")
async def usergames(interaction: discord.Interaction, user: discord.Member):
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