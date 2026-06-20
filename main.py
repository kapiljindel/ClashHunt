import asyncio
import discord
from discord.ext import tasks, commands
from discord import app_commands
import aiohttp
import json
import os
from datetime import datetime, timezone
import urllib.parse

# Import our secondary utility module
from scraper import scrape_fwa_details

# ==================== CONFIGURATION ====================
DISCORD_BOT_TOKEN = "MTUxMDQ3MjY0MjYzNjAyMTg1MQ.G5iTuJ.99O-Q6b6wZCrIA-znT604O31ihTiLoHd7VujmU"
COC_API_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiIsImtpZCI6IjI4YTMxOGY3LTAwMDAtYTFlYi03ZmExLTJjNzQzM2M2Y2NhNSJ9.eyJpc3MiOiJzdXBlcmNlbGwiLCJhdWQiOiJzdXBlcmNlbGw6Z2FtZWFwaSIsImp0aSI6Ijg4ZDQzM2I5LWZlMTQtNGQwYS05NjE2LTczZTlkMDYxZmVkNCIsImlhdCI6MTc4MTg3NjkxOSwic3ViIjoiZGV2ZWxvcGVyL2M4ZmEzOGQxLWRiNDMtM2M4Yi05MjljLWI0YWJlMWE3NzlhNCIsInNjb3BlcyI6WyJjbGFzaCJdLCJsaW1pdHMiOlt7InRpZXIiOiJkZXZlbG9wZXIvc2lsdmVyIiwidHlwZSI6InRocm90dGxpbmcifSx7ImNpZHJzIjpbIjExNC4xMzQuMjQuMjAwIl0sInR5cGUiOiJjbGllbnQifV19.ULTc6TxD7yI3K_y4ODch8xx2VlMtSm86gBW_blqPTliZqywAPJ6n6z4l6YeW9Ti2F15bT3GNG9Vn3UGrczVaCQ"
CLANS_FILE = "clans.json"
# =======================================================

intents = discord.Intents.default()
intents.message_content = True  
bot = commands.Bot(command_prefix="!", intents=intents)

# Cache dictionary to track multiple wars simultaneously
active_wars = {}

# --- DATABASE HELPER FUNCTIONS ---
def load_clans():
    """Loads tracked clans from the local JSON file."""
    if not os.path.exists(CLANS_FILE):
        return {}
    try:
        with open(CLANS_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}

def save_clans(data):
    """Saves tracked clans to the local JSON file."""
    try:
        with open(CLANS_FILE, 'w') as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"[Storage Error] Could not save clans layout: {e}")

# --- AUTOCOMPLETE DROPDOWN FILTER ---
async def clan_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """Generates a dynamic dropdown menu of registered clans inside Discord."""
    clans_data = load_clans()
    choices = []
    
    for tag, details in clans_data.items():
        display_name = f"{details['clan_name']} ({tag})"
        # Filter choices based on what the user is typing, or show all if empty
        if current.lower() in display_name.lower():
            choices.append(app_commands.Choice(name=display_name, value=tag))
            
    return choices[:25]  # Discord maximum application option limit is 25 items


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
    """Fetches API & Scraper data for any given clan tag and builds the embed payload."""
    clean_tag = clan_tag.upper().replace("#", "").strip()
    encoded_tag = urllib.parse.quote(f"#{clean_tag}")
    url = f"https://api.clashofclans.com/v1/clans/{encoded_tag}/currentwar"
    headers = {
        "Authorization": f"Bearer {COC_API_TOKEN}",
        "Accept": "application/json"
    }

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

    embed = discord.Embed(
        description="<@&1500908965196730480>", 
        color=3368601                          
    )
    
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


# --- MULTI-CLAN BACKGROUND LOOP ---
@tasks.loop(minutes=15)
async def check_clan_war_loop():
    await bot.wait_until_ready()
    clans_data = load_clans()
    if not clans_data: return

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Automated loop verifying {len(clans_data)} registered clans...")

    for tag, config in clans_data.items():
        channel_id = config.get("channel_id")
        channel = bot.get_channel(channel_id)
        if not channel: continue

        try:
            embed, match_id, error = await generate_war_embed(tag)
            if error or match_id == "notInWar":
                active_wars[tag] = None
                continue

            if active_wars.get(tag) == match_id:
                continue

            await channel.send(embed=embed)
            print(f"[Loop Success] Sent live war logging update for clan: {tag}")
            active_wars[tag] = match_id

        except Exception as e:
            print(f"[Loop Exception] Failed processing metrics for {tag}: {e}")
        
        await asyncio.sleep(2)


# --- DROPDOWN POWERED SLASH COMMANDS ---

@bot.tree.command(name="checkwar", description="Instantly check live status for any tracked clan.")
@app_commands.autocomplete(clan_tag=clan_autocomplete)
@app_commands.describe(clan_tag="Choose a clan from your tracking dashboard list.")
async def checkwar_command(interaction: discord.Interaction, clan_tag: str):
    """Allows manual check on a specific tag chosen from the dynamic dropdown selection."""
    await interaction.response.defer(thinking=True)
    
    try:
        embed, _, error = await generate_war_embed(clan_tag)
        if error:
            await interaction.followup.send(f"❌ Error fetching layout profiles: `{error}`")
            return
        if _ == "notInWar":
            await interaction.followup.send(f"🛡️ The clan `{clan_tag.upper()}` is currently not engaged in an active war.")
            return

        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send("❌ Critical system failure during manual trace processing pipelines.")
        print(f"[Command Exception] {e}")


@bot.tree.command(name="addclan", description="Register a new clan to be tracked automatically in this channel.")
@app_commands.describe(clan_tag="The unique in-game tag of your clan (e.g., #2LRGQ2L9L)")
async def addclan(interaction: discord.Interaction, clan_tag: str):
    await interaction.response.defer(ephemeral=True)
    formatted_tag = f"#{clan_tag.upper().replace('#', '').strip()}"
    
    clans_data = load_clans()
    if formatted_tag in clans_data:
        await interaction.followup.send(f"⚠️ `{formatted_tag}` is already being tracked in <#{clans_data[formatted_tag]['channel_id']}>.")
        return

    encoded_tag = urllib.parse.quote(formatted_tag)
    url = f"https://api.clashofclans.com/v1/clans/{encoded_tag}"
    headers = {"Authorization": f"Bearer {COC_API_TOKEN}", "Accept": "application/json"}
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            if response.status != 200:
                await interaction.followup.send("❌ Could not register clan. Please verify that the tag is valid.")
                return
            data = await response.json()
            clan_name = data.get("name", "Unknown Clan")

    clans_data[formatted_tag] = {
        "clan_name": clan_name,
        "channel_id": interaction.channel_id
    }
    save_clans(clans_data)
    await interaction.followup.send(f"✅ Success! **{clan_name}** (`{formatted_tag}`) is now being tracked in this channel.")


@bot.tree.command(name="removeclan", description="Stop auto-tracking a specific clan tag.")
@app_commands.autocomplete(clan_tag=clan_autocomplete)
@app_commands.describe(clan_tag="Choose which clan to remove from tracking.")
async def removeclan(interaction: discord.Interaction, clan_tag: str):
    clans_data = load_clans()
    formatted_tag = clan_tag.upper().strip()

    if formatted_tag not in clans_data:
        await interaction.response.send_message(f"❌ `{formatted_tag}` is not currently being tracked.", ephemeral=True)
        return

    name = clans_data[formatted_tag]["clan_name"]
    del clans_data[formatted_tag]
    if formatted_tag in active_wars: del active_wars[formatted_tag]
    save_clans(clans_data)

    await interaction.response.send_message(f"🗑️ Stopped tracking **{name}** (`{formatted_tag}`).", ephemeral=True)


@bot.tree.command(name="listclans", description="Show all clans currently tracked by the bot system.")
async def listclans(interaction: discord.Interaction):
    clans_data = load_clans()
    if not clans_data:
        await interaction.response.send_message("📭 No clans are currently configured for tracking.", ephemeral=True)
        return

    embed = discord.Embed(title="📋 Tracked Clans Overview", color=0x336869)
    for tag, details in clans_data.items():
        embed.add_field(
            name=f"{details['clan_name']} ({tag})",
            value=f"Logging destination: <#{details['channel_id']}>",
            inline=False
        )
    await interaction.response.send_message(embed=embed)


# --- INITIALIZATION ---
@bot.event
async def on_ready():
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
    bot.run(DISCORD_BOT_TOKEN)