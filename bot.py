import discord
from discord.ext import commands
from discord import app_commands
import json
import os

# ── Config ────────────────────────────────────────────────────────────────────
LFG_CATEGORY_NAME = "Games"
MENU_CHANNEL_NAME = "game-menu"
DATA_FILE         = "games_data.json"

GAMES = [
    "Grounded", "Phasmophobia", "Minecraft", "Calamity", "Isaac",
    "Valorant", "Liars Bar", "Content Warning", "Among Us", "Satisfactory",
    "Stardew Valley", "Portal", "Sea Of Thieves", "Rust", "Helldivers 2",
    "Marvel Rivals", "GeoGuessr", "Apex Legends", "Rabbit and Steel", "Split Fiction",
    "Brawl Stars", "Goat Simulator 3", "Mario Kart World", "Red Dead Redemption 2",
    "Rocket League", "Fall Guys", "Peak", "Ready Or Not", "SpeedRunners",
    "Ultimate Chicken Horse", "Terraria", "Overwatch", "Euro Truck Simulator",
    "Rematch", "Subnautica 2", "Eldenring / Nightreign",
]

GAMES_PER_MESSAGE = 5

# ── Storage helpers ───────────────────────────────────────────────────────────
def load_data() -> dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE) as f:
            return json.load(f)
    return {"subscriptions": {}}

def save_data(data: dict):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ── Bot setup ─────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.members = True

bot  = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# ── Helpers ───────────────────────────────────────────────────────────────────
def channel_name_for(game: str) -> str:
    # Matches Discord auto-naming: lowercase, spaces→dashes, strip special chars
    name = game.lower().replace("/", "").replace(".", "")
    name = "-".join(name.split())
    while "--" in name:
        name = name.replace("--", "-")
    return name.strip("-")

async def get_or_create_category(guild: discord.Guild) -> discord.CategoryChannel:
    cat = discord.utils.get(guild.categories, name=LFG_CATEGORY_NAME)
    if not cat:
        # Category itself hidden from everyone
        overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
        cat = await guild.create_category(LFG_CATEGORY_NAME, overwrites=overwrites)
    return cat

async def get_game_channel(guild: discord.Guild, game: str) -> discord.TextChannel | None:
    category = discord.utils.get(guild.categories, name=LFG_CATEGORY_NAME)
    if not category:
        return None
    return discord.utils.get(category.text_channels, name=channel_name_for(game))

async def grant_access(guild: discord.Guild, member: discord.Member, game: str):
    channel = await get_game_channel(guild, game)
    if channel:
        await channel.set_permissions(member, view_channel=True, send_messages=True)

async def revoke_access(guild: discord.Guild, member: discord.Member, game: str):
    channel = await get_game_channel(guild, game)
    if channel:
        await channel.set_permissions(member, overwrite=None)  # remove override → falls back to hidden

# ── UI: per-game button rows ──────────────────────────────────────────────────
def make_game_rows(games: list[str]) -> list[discord.ui.View]:
    views = []
    for chunk_start in range(0, len(games), GAMES_PER_MESSAGE):
        chunk = games[chunk_start:chunk_start + GAMES_PER_MESSAGE]
        view  = discord.ui.View(timeout=None)
        for game in chunk:
            safe = game.replace(" ", "_").replace("/", "")[:50]
            row  = chunk.index(game)

            # Game label (disabled button, just for display)
            view.add_item(discord.ui.Button(
                label=game,
                style=discord.ButtonStyle.secondary,
                custom_id=f"label_{safe}",
                disabled=True,
                row=row,
            ))

            # ➕ Add
            add_btn = discord.ui.Button(
                label="➕ Add",
                style=discord.ButtonStyle.success,
                custom_id=f"add_{safe}",
                row=row,
            )
            async def add_cb(interaction: discord.Interaction, g=game):
                data = load_data()
                uid  = str(interaction.user.id)
                if uid not in data["subscriptions"]:
                    data["subscriptions"][uid] = []
                if g in data["subscriptions"][uid]:
                    await interaction.response.send_message(
                        f"You already have **{g}** on your list.", ephemeral=True
                    )
                    return
                data["subscriptions"][uid].append(g)
                save_data(data)
                try:
                    await grant_access(interaction.guild, interaction.user, g)
                    channel = await get_game_channel(interaction.guild, g)
                    if channel:
                        await interaction.response.send_message(
                            f"✅ Added **{g}**! You can now see {channel.mention}.", ephemeral=True
                        )
                    else:
                        await interaction.response.send_message(
                            f"✅ Added **{g}** to your list! (Channel not set up yet — ask an admin to run `/admin setup_channels`)",
                            ephemeral=True
                        )
                except discord.Forbidden:
                    await interaction.response.send_message(
                        f"✅ Added **{g}** to your list! (Bot is missing permissions to grant channel access — ask an admin)",
                        ephemeral=True
                    )
            add_btn.callback = add_cb
            view.add_item(add_btn)

            # ➖ Remove
            rem_btn = discord.ui.Button(
                label="➖ Remove",
                style=discord.ButtonStyle.danger,
                custom_id=f"remove_{safe}",
                row=row,
            )
            async def rem_cb(interaction: discord.Interaction, g=game):
                data = load_data()
                uid  = str(interaction.user.id)
                if uid not in data["subscriptions"] or g not in data["subscriptions"][uid]:
                    await interaction.response.send_message(
                        f"**{g}** is not on your list.", ephemeral=True
                    )
                    return
                data["subscriptions"][uid].remove(g)
                save_data(data)
                try:
                    await revoke_access(interaction.guild, interaction.user, g)
                except discord.Forbidden:
                    pass
                await interaction.response.send_message(
                    f"🗑️ Removed **{g}** from your list.", ephemeral=True
                )
            rem_btn.callback = rem_cb
            view.add_item(rem_btn)

            # 👥 Who plays?
            who_btn = discord.ui.Button(
                label="👥 Who plays?",
                style=discord.ButtonStyle.primary,
                custom_id=f"who_{safe}",
                row=row,
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
                    await interaction.response.send_message(
                        f"Nobody has **{g}** on their list yet.", ephemeral=True
                    )
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

    await channel.purge(limit=200, check=lambda m: m.author == guild.me)

    header = discord.Embed(
        title="🎮 Game Menu",
        description=(
            "**➕ Add** — join a game's private channel\n"
            "**➖ Remove** — leave a game's private channel\n"
            "**👥 Who plays?** — see who has this game on their list\n\n"
            "*All responses are only visible to you.*"
        ),
        color=0x5865F2,
    )
    await channel.send(embed=header)

    for view in make_game_rows(GAMES):
        await channel.send(view=view)

# ── /admin command group ──────────────────────────────────────────────────────
admin_group = app_commands.Group(
    name="admin",
    description="Admin tools (requires Manage Server permission)",
    default_permissions=discord.Permissions(manage_guild=True),
)

@admin_group.command(name="setup_channels", description="Create all game channels under the Looking For Group category")
async def admin_setup_channels(interaction: discord.Interaction):
    """Creates a private channel for every game. Safe to run multiple times — skips existing ones."""
    await interaction.response.defer(ephemeral=True)
    guild    = interaction.guild
    category = await get_or_create_category(guild)
    created  = 0
    skipped  = 0
    for game in GAMES:
        name = channel_name_for(game)
        existing = discord.utils.get(category.text_channels, name=name)
        if existing:
            skipped += 1
            continue
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
        }
        await category.create_text_channel(name=name, overwrites=overwrites)
        created += 1
    await interaction.followup.send(
        f"✅ Done! Created **{created}** channel(s), skipped **{skipped}** that already existed.",
        ephemeral=True
    )

@admin_group.command(name="sync_permissions", description="Re-sync channel access for everyone based on their game list")
async def admin_sync_permissions(interaction: discord.Interaction):
    """
    Useful after setup_channels or if permissions get out of sync.
    Grants access to everyone who has a game, revokes from those who don't.
    """
    await interaction.response.defer(ephemeral=True)
    guild    = interaction.guild
    data     = load_data()
    category = discord.utils.get(guild.categories, name=LFG_CATEGORY_NAME)
    if not category:
        await interaction.followup.send(
            "❌ No 'Looking For Group' category found. Run `/admin setup_channels` first.",
            ephemeral=True
        )
        return

    count = 0
    for game in GAMES:
        channel = discord.utils.get(category.text_channels, name=channel_name_for(game))
        if not channel:
            continue
        for member in guild.members:
            if member.bot:
                continue
            has_game = game in data["subscriptions"].get(str(member.id), [])
            try:
                if has_game:
                    await channel.set_permissions(member, view_channel=True, send_messages=True)
                else:
                    await channel.set_permissions(member, overwrite=None)
                count += 1
            except discord.Forbidden:
                pass

    await interaction.followup.send(
        f"✅ Synced permissions for **{count}** member/channel combinations.", ephemeral=True
    )

@admin_group.command(name="import_roles", description="Import everyone's existing game roles into the bot's database")
async def admin_import_roles(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    guild      = interaction.guild
    data       = load_data()
    subs       = data["subscriptions"]
    game_lookup = {g.lower(): g for g in GAMES}
    imported_users = 0
    imported_subs  = 0
    for member in guild.members:
        if member.bot:
            continue
        matched = [game_lookup[r.name.lower()] for r in member.roles if r.name.lower() in game_lookup]
        if matched:
            uid       = str(member.id)
            existing  = set(subs.get(uid, []))
            new_games = existing | set(matched)
            subs[uid] = list(new_games)
            imported_users += 1
            imported_subs  += len(new_games - existing)
    save_data(data)
    await interaction.followup.send(
        f"✅ Imported game roles for **{imported_users}** members, "
        f"added **{imported_subs}** new subscriptions.\n"
        f"Run `/admin sync_permissions` to grant them channel access.",
        ephemeral=True
    )

@admin_group.command(name="refresh_menu", description="Re-post the game menu in #game-menu")
async def admin_refresh_menu(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await post_menu(interaction.guild)
    await interaction.followup.send("✅ Menu refreshed!", ephemeral=True)

@admin_group.command(name="user_games", description="See all games a specific user has on their list")
@app_commands.describe(user="The user to look up")
async def admin_user_games(interaction: discord.Interaction, user: discord.Member):
    data = load_data()
    subs = data["subscriptions"].get(str(user.id), [])
    if not subs:
        await interaction.response.send_message(
            f"**{user.display_name}** has no games on their list.", ephemeral=True
        )
        return
    await interaction.response.send_message(
        f"**{user.display_name}'s games ({len(subs)}):**\n" + "\n".join(f"• {g}" for g in sorted(subs)),
        ephemeral=True
    )

tree.add_command(admin_group)

# ── /usergames (public) ───────────────────────────────────────────────────────
@tree.command(name="usergames", description="See all games a specific user has on their list")
@app_commands.describe(user="The user to look up")
async def usergames(interaction: discord.Interaction, user: discord.Member):
    data = load_data()
    subs = data["subscriptions"].get(str(user.id), [])
    if not subs:
        await interaction.response.send_message(
            f"**{user.display_name}** has no games on their list.", ephemeral=True
        )
        return
    await interaction.response.send_message(
        f"**{user.display_name}'s games ({len(subs)}):**\n" + "\n".join(f"• {g}" for g in sorted(subs)),
        ephemeral=True
    )

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