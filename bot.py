import discord
from discord.ext import commands
from discord import app_commands
import json
import os
import asyncio
from datetime import datetime

# ── Config ──────────────────────────────────────────────────────────────────
LFG_CATEGORY_NAME = "Looking For Group"  # category that holds temp channels
DATA_FILE         = "games_data.json"    # stores who plays what
PING_COOLDOWN     = 300                  # seconds between /lfg calls per game (5 min)
CHANNEL_TIMEOUT   = 18000                # seconds of inactivity before auto-delete (30 min)

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

# ── Storage helpers ──────────────────────────────────────────────────────────
def load_data() -> dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE) as f:
            return json.load(f)
    return {"subscriptions": {}, "last_ping": {}}

def save_data(data: dict):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ── Bot setup ────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.members  = True
intents.messages = True
intents.message_content = True

bot  = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# Tracks active temp channels: channel_id → asyncio.Task (the timeout task)
active_channels: dict[int, asyncio.Task] = {}

# ── Helpers ───────────────────────────────────────────────────────────────────
def channel_name_for(game: str) -> str:
    """Convert a game name to a valid Discord channel name."""
    return "lfg-" + game.lower().replace(" ", "-").replace("/", "").replace("--", "-")

async def get_or_create_category(guild: discord.Guild) -> discord.CategoryChannel:
    cat = discord.utils.get(guild.categories, name=LFG_CATEGORY_NAME)
    if not cat:
        cat = await guild.create_category(LFG_CATEGORY_NAME)
    return cat

async def schedule_deletion(channel: discord.TextChannel, delay: int):
    """Wait `delay` seconds then delete the channel."""
    await asyncio.sleep(delay)
    try:
        await channel.send(
            f"⏰ This channel has been inactive for **{delay // 60} minutes** and will now be deleted."
        )
        await asyncio.sleep(5)
        await channel.delete(reason="LFG session expired")
    except discord.NotFound:
        pass  # already deleted
    active_channels.pop(channel.id, None)

def reset_timer(channel: discord.TextChannel):
    """Cancel any existing deletion timer and start a fresh one."""
    old_task = active_channels.get(channel.id)
    if old_task:
        old_task.cancel()
    task = asyncio.create_task(schedule_deletion(channel, CHANNEL_TIMEOUT))
    active_channels[channel.id] = task

# ── Autocomplete ──────────────────────────────────────────────────────────────
async def game_autocomplete(interaction: discord.Interaction, current: str):
    return [
        app_commands.Choice(name=g, value=g)
        for g in GAMES if current.lower() in g.lower()
    ][:25]

# ── /games command group ──────────────────────────────────────────────────────
games_group = app_commands.Group(name="games", description="Manage your game list")

@games_group.command(name="add", description="Add a game to your list")
@app_commands.autocomplete(game=game_autocomplete)
async def games_add(interaction: discord.Interaction, game: str):
    if game not in GAMES:
        await interaction.response.send_message(
            f"❌ **{game}** is not in the game list. Use the autocomplete to pick a valid game.",
            ephemeral=True
        )
        return
    data = load_data()
    uid  = str(interaction.user.id)
    subs = data["subscriptions"]
    if uid not in subs:
        subs[uid] = []
    if game in subs[uid]:
        await interaction.response.send_message(
            f"You already have **{game}** on your list.", ephemeral=True
        )
        return
    subs[uid].append(game)
    save_data(data)
    await interaction.response.send_message(
        f"✅ Added **{game}** to your list! You'll get access to the private channel when someone uses `/lfg`.",
        ephemeral=True
    )

@games_group.command(name="remove", description="Remove a game from your list")
@app_commands.autocomplete(game=game_autocomplete)
async def games_remove(interaction: discord.Interaction, game: str):
    data = load_data()
    uid  = str(interaction.user.id)
    subs = data["subscriptions"]
    if uid not in subs or game not in subs[uid]:
        await interaction.response.send_message(
            f"**{game}** is not on your list.", ephemeral=True
        )
        return
    subs[uid].remove(game)
    save_data(data)
    await interaction.response.send_message(
        f"🗑️ Removed **{game}** from your list.", ephemeral=True
    )

@games_group.command(name="list", description="See all games you're signed up for")
async def games_list(interaction: discord.Interaction):
    data = load_data()
    uid  = str(interaction.user.id)
    subs = data["subscriptions"].get(uid, [])
    if not subs:
        await interaction.response.send_message(
            "You haven't added any games yet. Use `/games add` to get started!",
            ephemeral=True
        )
        return
    game_list = "\n".join(f"• {g}" for g in sorted(subs))
    await interaction.response.send_message(
        f"**Your games:**\n{game_list}", ephemeral=True
    )

@games_group.command(name="players", description="See who else plays a specific game")
@app_commands.autocomplete(game=game_autocomplete)
async def games_players(interaction: discord.Interaction, game: str):
    data    = load_data()
    guild   = interaction.guild
    players = []
    for uid, games in data["subscriptions"].items():
        if game in games:
            member = guild.get_member(int(uid))
            if member:
                players.append(member.display_name)
    if not players:
        await interaction.response.send_message(
            f"Nobody has **{game}** on their list yet.", ephemeral=True
        )
        return
    await interaction.response.send_message(
        f"**{len(players)} player(s) have {game}:**\n" + "\n".join(f"• {p}" for p in players),
        ephemeral=True
    )

@games_group.command(name="user_games", description="See all games a specific user has on their list")
@app_commands.describe(user="The user to look up")
async def games_user_games(interaction: discord.Interaction, user: discord.Member):
    data = load_data()
    uid  = str(user.id)
    subs = data["subscriptions"].get(uid, [])
    if not subs:
        await interaction.response.send_message(
            f"**{user.display_name}** has no games on their list.", ephemeral=True
        )
        return
    game_list = "\n".join(f"• {g}" for g in sorted(subs))
    await interaction.response.send_message(
        f"**{user.display_name}'s games ({len(subs)}):**\n{game_list}", ephemeral=True
    )

@games_group.command(name="game_users", description="See all users who have a specific game on their list")
@app_commands.autocomplete(game=game_autocomplete)
@app_commands.describe(game="The game to look up")
async def games_game_users(interaction: discord.Interaction, game: str):
    data    = load_data()
    guild   = interaction.guild
    players = []
    for uid, games in data["subscriptions"].items():
        if game in games:
            member = guild.get_member(int(uid))
            if member:
                players.append(member.display_name)
    if not players:
        await interaction.response.send_message(
            f"Nobody has **{game}** on their list.", ephemeral=True
        )
        return
    player_list = "\n".join(f"• {p}" for p in sorted(players))
    await interaction.response.send_message(
        f"**{len(players)} player(s) with {game}:**\n{player_list}", ephemeral=True
    )

tree.add_command(games_group)

# ── /admin command group ──────────────────────────────────────────────────────
admin_group = app_commands.Group(
    name="admin",
    description="Admin tools (requires Manage Server permission)",
    default_permissions=discord.Permissions(manage_guild=True),
)

@admin_group.command(name="import_roles", description="Import everyone's game roles into the bot's database")
async def admin_import_roles(interaction: discord.Interaction):
    """
    Scans every member's roles, matches them against the GAMES list,
    and saves them as subscriptions — replacing the old role-based system.
    """
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    data  = load_data()
    subs  = data["subscriptions"]

    # Build a lookup: lowercase role name → canonical game name
    game_lookup = {g.lower(): g for g in GAMES}

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
            uid = str(member.id)
            existing = set(subs.get(uid, []))
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

tree.add_command(admin_group)

# ── /lfg command ──────────────────────────────────────────────────────────────
@tree.command(name="lfg", description="Looking for group — opens a private channel for your game")
@app_commands.autocomplete(game=game_autocomplete)
@app_commands.describe(
    game="Which game?",
    message="Optional message (e.g. 'starting in 10 min, need 2 more')"
)
async def lfg(interaction: discord.Interaction, game: str, message: str = ""):
    if game not in GAMES:
        await interaction.response.send_message(
            f"❌ **{game}** is not in the game list.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    data  = load_data()
    guild = interaction.guild
    subs  = data["subscriptions"]

    # Cooldown check
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

    # Collect all members who have this game
    members_with_game: list[discord.Member] = []
    for uid, games in subs.items():
        if game in games:
            member = guild.get_member(int(uid))
            if member:
                members_with_game.append(member)

    # Always include the caller even if they forgot to add the game
    if interaction.user not in members_with_game:
        members_with_game.append(interaction.user)

    # Build permission overwrites:
    # - @everyone: cannot see the channel
    # - each player with the game: can see & send
    # - the bot itself: can see & manage
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
    }
    for member in members_with_game:
        overwrites[member] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

    # Get or create the LFG category
    category = await get_or_create_category(guild)
    chan_name = channel_name_for(game)

    # Reuse an existing open session for this game if one exists
    existing = discord.utils.get(category.text_channels, name=chan_name)
    if existing:
        # Grant access to the caller in case they weren't already in
        await existing.set_permissions(interaction.user, view_channel=True, send_messages=True)
        reset_timer(existing)
        extra = f"\n> {message}" if message else ""
        await existing.send(
            f"🎮 **{interaction.user.display_name}** also wants to play!{extra}"
        )
        await interaction.followup.send(
            f"✅ A session is already open: {existing.mention}", ephemeral=True
        )
        return

    # Create the private channel
    channel = await category.create_text_channel(name=chan_name, overwrites=overwrites)

    # Post the intro message
    mentions = " ".join(m.mention for m in members_with_game if m != interaction.user)
    extra    = f"\n> {message}" if message else ""
    if mentions:
        intro = (
            f"🎮 **{interaction.user.display_name}** is looking for people to play **{game}**!{extra}\n"
            f"{mentions}\n\n"
            f"🔒 Only people with **{game}** on their list can see this channel.\n"
            f"⏰ It will auto-delete after **{CHANNEL_TIMEOUT // 60} minutes** of inactivity."
        )
    else:
        intro = (
            f"🎮 **{interaction.user.display_name}** is looking for people to play **{game}**!{extra}\n\n"
            f"*Nobody else has this game on their list yet — tell your friends to use `/games add {game}`!*\n"
            f"⏰ This channel will auto-delete after **{CHANNEL_TIMEOUT // 60} minutes** of inactivity."
        )

    await channel.send(intro)

    # Start the inactivity timer
    reset_timer(channel)

    # Save cooldown
    data["last_ping"][cooldown_key] = datetime.utcnow().isoformat()
    save_data(data)

    await interaction.followup.send(
        f"✅ Created a private session: {channel.mention}", ephemeral=True
    )

# ── Reset timer on any message in a managed channel ───────────────────────────
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if message.channel.id in active_channels:
        reset_timer(message.channel)
    await bot.process_commands(message)

# ── /gamelist ─────────────────────────────────────────────────────────────────
@tree.command(name="gamelist", description="Show all available games")
async def gamelist(interaction: discord.Interaction):
    text = "\n".join(f"• {g}" for g in GAMES)
    await interaction.response.send_message(
        f"**Available games ({len(GAMES)} total):**\n{text}", ephemeral=True
    )

# ── Startup ───────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    await tree.sync()
    print(f"✅ Logged in as {bot.user} — slash commands synced.")

# ── Run ───────────────────────────────────────────────────────────────────────
TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("Set the DISCORD_TOKEN environment variable before running.")
bot.run(TOKEN)