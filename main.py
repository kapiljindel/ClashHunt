import asyncio
import discord
from discord.ext import tasks, commands
from discord import app_commands
import aiohttp
import os
import urllib.parse
from datetime import datetime, timezone
from dotenv import load_dotenv

# Import the asynchronous MongoDB driver
from motor.motor_asyncio import AsyncIOMotorClient

# Import our secondary scraper utility module
from scraper import scrape_fwa_details

# Load credentials from .env
load_dotenv()
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
COC_API_TOKEN = os.getenv("COC_API_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

intents = discord.Intents.default()
intents.message_content = True  
bot = commands.Bot(command_prefix="!", intents=intents)

# MongoDB Global Database Client References
mongo_client = None
db = None
clans_collection = None

# Multi-server anti-spam loop cache -> {"guild_id-clan_tag": "opponentTag-state"}
active_wars = {}

# --- DATABASE MANAGEMENT FUNCTIONS ---
def init_mongodb():
    """Initializes the MongoDB connection client."""
    global mongo_client, db, clans_collection
    if not MONGO_URI:
        print("[Critical Error] MONGO_URI is missing from your .env file!")
        return False
    
    mongo_client = AsyncIOMotorClient(MONGO_URI)
    db = mongo_client["fwa_war_bot"]
    clans_collection = db["tracked_clans"]
    print("[Database] Successfully connected to MongoDB Cluster.")
    return True

async def db_add_clan(tag, name, channel_id, guild_id):
    """Saves or updates a clan tracking record scoped to a specific guild server."""
    await clans_collection.update_one(
        {"clan_tag": tag, "guild_id": guild_id},
        {"$set": {"clan_name": name, "channel_id": channel_id}},
        upsert=True
    )

async def db_remove_clan(tag, guild_id):
    """Deletes a clan tracking record scoped to a specific guild server."""
    await clans_collection.delete_one({"clan_tag": tag, "guild_id": guild_id})

async def db_get_guild_clans(guild_id):
    """Fetches all tracked clans for a specific server guild only."""
    cursor = clans_collection.find({"guild_id": guild_id})
    clans = await cursor.to_list(length=100)
    
    clans_data = {}
    for c in clans:
        clans_data[c["clan_tag"]] = {"clan_name": c["clan_name"], "channel_id": c["channel_id"]}
    return clans_data

async def db_get_all_global_clans():
    """Fetches every single tracked entry across all servers for the background loop."""
    cursor = clans_collection.find({})
    return await cursor.to_list(length=1000)


# --- AUTOCOMPLETE DROPDOWN FILTER (SERVER SCOPED) ---
async def clan_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """Generates dropdown options showing ONLY clans registered in the current server."""
    if not interaction.guild_id:
        return []
        
    guild_clans = await db_get_guild_clans(interaction.guild_id)
    choices = []
    
    for tag, details in guild_clans.items():
        display_name = f"{details['clan_name']} ({tag})"
        if current.lower() in display_name.lower():
            choices.append(app_commands.Choice(name=display_name, value=tag))
            
    return choices[:25]


# --- COC DATA PARSERS ---
def parse_coc_date(date_str):
    if not date_str: return None
    try:
        clean_str = date_str.replace(".000Z", "")
        dt = datetime.strptime(clean_str, "%Y%m%dT%H%M%S")
        return dt.replace(tzinfo=timezone.utc)
    except Exception: return None

def get_th_composition(members):
    counts = {}
    for member in members:
        th = member.get('townhallLevel') or member.get('townHallLevel')
        if th: counts[th] = counts.get(th, 0) + 1
    
    sorted_th = sorted(counts.keys(), reverse=True)
    comp_strings = []
    for th in sorted_th:
        if th >= 12: comp_strings.append(f":th{th}: `{counts[th]}`")
        else: comp_strings.append(f"TH{th} `{counts[th]}`")
    return " ".join(comp_strings) if comp_strings else "No data"

async def generate_war_embed(clan_tag):
    """Queries CoC API and running scraper pipelines to compile an output layout."""
    clean_tag = clan_tag.upper().replace("#", "").strip()
    encoded_tag = urllib.parse.quote(f"#{clean_tag}")
    url = f"https://api.clashofclans.com/v1/clans/{encoded_tag}/currentwar"
    headers = {"Authorization": f"Bearer {COC_API_TOKEN}", "Accept": "application/json"}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            if response.status != 200:
                return None, None, f"CoC API Error (Status: {response.status})"
            war_data = await response.json()

    if war_data.get('state') == 'notInWar':
        return None, "notInWar", None

    clan = war_data.get('clan', {})
    opponent = war_data.get('opponent', {})
    state = war_data.get('state')
    match_id = f"{opponent.get('tag')}-{state}"

    print(f"[Main Bot] Scraping FWA metrics for #{clean_tag}...")
    fwa_metrics = await asyncio.to_thread(scrape_fwa_details, f"#{clean_tag}")

    end_time = parse_coc_date(war_data.get('endTime'))
    time_left_text = "Unknown"
    if end_time:
        now = datetime.now(timezone.utc)
        delta = end_time - now
        total_hours = int(delta.total_seconds() // 3600)
        days = total_hours // 24
        hours = total_hours % 24
        time_left_text = f"{days}d {hours}h" if days > 0 else f"{hours}h"

    our_comp = get_th_composition(clan.get('members', []))
    enemy_comp = get_th_composition(opponent.get('members', []))
    clean_our_tag = clan.get('tag', '').replace('#', '')
    clean_enemy_tag = opponent.get('tag', '').replace('#', '')

    embed = discord.Embed(description="<@&1500908965196730480>", color=3368601)
    badge_url = clan.get('badgeUrls', {}).get('medium', "https://api-assets.clashofclans.com/badges/200/GZm0ep4Lp9-5woM7I6P2DD61PIzuMuT2Jk3EeZbpKVc.png")
    embed.set_thumbnail(url=badge_url)

    field_title = f"{clan.get('name')} vs {opponent.get('name')}"
    field_value = (
        f"**[{clan.get('name')}](https://link.clashofclans.com/en?action=OpenClanProfile&tag={clean_our_tag})** (`{clan.get('tag')}`) **VS** "
        f"**[{opponent.get('name')}](https://link.clashofclans.com/en?action=OpenClanProfile&tag={clean_enemy_tag})** (`{opponent.get('tag')}`)\n\n"
        f"**Match Type:** {fwa_metrics['match_type']}\n"
        f"**Sync Number:** #{fwa_metrics['sync_num']}\n"
        f"**War ID:** #{fwa_metrics['war_id']}\n"
        f"**Team Size:** {war_data.get('teamSize')} vs {war_data.get('teamSize')}\n"
        f"**Ends in:** {time_left_text}\n\n"
        f"**Points Balance:** {fwa_metrics['point_balance']}\n\n"
        f"**{clan.get('name')} Composition**\n{our_comp}\n\n"
        f"**{opponent.get('name')} Composition**\n{enemy_comp}"
    )

    embed.add_field(name=field_title, value=field_value, inline=False)
    return embed, match_id, None


# --- SERVER-AWARE BACKGROUND TASK LOOP ---
@tasks.loop(minutes=15)
async def check_clan_war_loop():
    await bot.wait_until_ready()
    all_tracked_entries = await db_get_all_global_clans()
    if not all_tracked_entries: return

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Loop handling {len(all_tracked_entries)} total database tracks...")

    for document in all_tracked_entries:
        tag = document["clan_tag"]
        guild_id = document["guild_id"]
        channel_id = document["channel_id"]
        
        channel = bot.get_channel(channel_id)
        if not channel: continue

        # Scoping key unique per server + per clan tag
        cache_key = f"{guild_id}-{tag}"

        try:
            embed, match_id, error = await generate_war_embed(tag)
            if error or match_id == "notInWar":
                active_wars[cache_key] = None
                continue

            if active_wars.get(cache_key) == match_id:
                continue

            await channel.send(embed=embed)
            print(f"[Loop Success] Update posted for {tag} on Guild: {guild_id}")
            active_wars[cache_key] = match_id

        except Exception as e:
            print(f"[Loop Exception] Tracking error on {tag} for guild {guild_id}: {e}")
        
        await asyncio.sleep(2)


# --- APPLICATION TREE SLASH COMMANDS ---

@bot.tree.command(name="addclan", description="Register a new clan to be tracked automatically in this server channel.")
@app_commands.describe(clan_tag="The unique in-game tag of your clan (e.g., #2RLGQ2L9L)")
async def addclan(interaction: discord.Interaction, clan_tag: str):
    if not interaction.guild_id:
        await interaction.response.send_message("❌ This command must be executed within a server guild.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    formatted_tag = f"#{clan_tag.upper().replace('#', '').strip()}"
    
    # Check if this server already tracks this specific tag
    guild_clans = await db_get_guild_clans(interaction.guild_id)
    if formatted_tag in guild_clans:
        await interaction.followup.send(f"⚠️ `{formatted_tag}` is already tracked in <#{guild_clans[formatted_tag]['channel_id']}> on this server.")
        return

    encoded_tag = urllib.parse.quote(formatted_tag)
    url = f"https://api.clashofclans.com/v1/clans/{encoded_tag}"
    headers = {"Authorization": f"Bearer {COC_API_TOKEN}", "Accept": "application/json"}
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            if response.status != 200:
                await interaction.followup.send("❌ Registration rejected. Please check the clan tag.")
                return
            data = await response.json()
            clan_name = data.get("name", "Unknown Clan")

    # Save to MongoDB with server boundary validation
    await db_add_clan(formatted_tag, clan_name, interaction.channel_id, interaction.guild_id)
    await interaction.followup.send(f"✅ MongoDB Entry Saved! **{clan_name}** (`{formatted_tag}`) is now tracked for this server.")


@bot.tree.command(name="removeclan", description="Stop auto-tracking a specific clan tag in this server.")
@app_commands.autocomplete(clan_tag=clan_autocomplete)
@app_commands.describe(clan_tag="Choose which clan to clear from this server's records.")
async def removeclan(interaction: discord.Interaction, clan_tag: str):
    if not interaction.guild_id: return
    
    guild_clans = await db_get_guild_clans(interaction.guild_id)
    formatted_tag = clan_tag.upper().strip()

    if formatted_tag not in guild_clans:
        await interaction.response.send_message("❌ That clan is not tracked on this server layout.", ephemeral=True)
        return

    name = guild_clans[formatted_tag]["clan_name"]
    
    # Delete from MongoDB matching this specific server only
    await db_remove_clan(formatted_tag, interaction.guild_id)
    cache_key = f"{interaction.guild_id}-{formatted_tag}"
    if cache_key in active_wars: del active_wars[cache_key]

    await interaction.response.send_message(f"🗑️ Wiped server logging records for **{name}** (`{formatted_tag}`).", ephemeral=True)


@bot.tree.command(name="listclans", description="Show clans currently tracked inside this server.")
async def listclans(interaction: discord.Interaction):
    if not interaction.guild_id: return
    
    guild_clans = await db_get_guild_clans(interaction.guild_id)
    if not guild_clans:
        await interaction.response.send_message("📭 No clans are configured for tracking in this server.", ephemeral=True)
        return

    embed = discord.Embed(title="📋 Server Tracked Clans Overview", color=0x336869)
    for tag, details in guild_clans.items():
        embed.add_field(
            name=f"{details['clan_name']} ({tag})",
            value=f"Logging destination: <#{details['channel_id']}>",
            inline=False
        )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="checkwar", description="Instantly check live status for any server-tracked clan.")
@app_commands.autocomplete(clan_tag=clan_autocomplete)
@app_commands.describe(clan_tag="Select a clan from your server's registered dashboard list.")
async def checkwar_command(interaction: discord.Interaction, clan_tag: str):
    await interaction.response.defer(thinking=True)
    try:
        embed, _, error = await generate_war_embed(clan_tag)
        if error:
            await interaction.followup.send(f"❌ Error compiling log layout: `{error}`")
            return
        if _ == "notInWar":
            await interaction.followup.send(f"🛡️ The clan `{clan_tag.upper()}` is not in an active war.")
            return

        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send("❌ Internal database or parsing pipeline crash.")
        print(f"[Command Exception] {e}")


# --- BOT EVENT HANDLERS ---
@bot.event
async def on_ready():
    # Setup our asynchronous MongoDB cluster endpoints
    if not init_mongodb():
        print("[Shutdown] Terminating execution: MongoDB initialization failed.")
        await bot.close()
        return
        
    print(f"Logged into Discord API as: {bot.user.name}")
    print("Syncing slash commands with Discord global trees...")
    try:
        synced = await bot.tree.sync()
        print(f"Successfully synchronized {len(synced)} application slash commands.")
    except Exception as e:
        print(f"Failed to sync application tree layouts: {e}")
        
    print("-----------------------------------------------------")
    if not check_clan_war_loop.is_running():
        check_clan_war_loop.start()

if __name__ == "__main__":
    if not DISCORD_BOT_TOKEN or not COC_API_TOKEN:
        print("[Critical Error] Tokens are missing! Check your local .env configuration script.")
    else:
        bot.run(DISCORD_BOT_TOKEN)