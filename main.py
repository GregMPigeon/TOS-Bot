"""
Whisper bot for game private-submission channels.

Behavior:
- Each player has a unique role (e.g. a role named after them) and a private
  submission channel. Admins link the two with /linkwhisperchannel.
- Players run /whisper in their own private channel to message another player.
- The target's private channel receives the full message.
- A public channel gets a contentless log: "X whispers to Y".
- One special "blackmailer" channel receives a full copy of every whisper (like mod logs).

Setup:
1. pip install discord.py
2. Fill in the CONFIG section below (or use env vars).
3. Run the bot, then as an admin run:
       /linkwhisperchannel channel:#alice-private role:@Alice
   once per player. Mappings persist in whisper_channels.json.
4. Set the full-copy channel with:
       /setblackmailer channel:#mod-logs
   Persists in whisper_settings.json.
"""

import os
import json
import discord
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()  # reads a .env file in the same directory, if present

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "PUT_YOUR_TOKEN_HERE")

GUILD_ID = int(os.environ.get("GUILD_ID", "0"))                      # your server ID
PUBLIC_LOG_CHANNEL_ID = int(os.environ.get("PUBLIC_LOG_CHANNEL_ID", "0"))

DATA_FILE = "whisper_channels.json"
SETTINGS_FILE = "whisper_settings.json"

# role_id (str) -> channel_id (int). Persisted to DATA_FILE.
role_channels: dict[str, int] = {}

# Channel that receives a full copy of every whisper. Set via /setblackmailer.
# Falls back to SPY_LOG_CHANNEL_ID env var until set, then persisted to SETTINGS_FILE.
blackmailer_channel_id: int = int(os.environ.get("SPY_LOG_CHANNEL_ID", "0")) or None


# User IDs allowed to run /togglenight, regardless of server roles/permissions.
NIGHT_TOGGLE_USER_IDS = {
    597162804864221206,
    433015462062981131,
    659469666141470720,
    1093590789898121367,
}

# Whether it's currently night. When true, whispers are blocked. Persisted to SETTINGS_FILE.
is_night: bool = False


def load_mappings():
    global role_channels
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            role_channels = json.load(f)


def save_mappings():
    with open(DATA_FILE, "w") as f:
        json.dump(role_channels, f, indent=2)


def load_settings():
    global blackmailer_channel_id, is_night
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "r") as f:
            data = json.load(f)
            blackmailer_channel_id = data.get("blackmailer_channel_id", blackmailer_channel_id)
            is_night = data.get("is_night", is_night)


def save_settings():
    with open(SETTINGS_FILE, "w") as f:
        json.dump(
            {"blackmailer_channel_id": blackmailer_channel_id, "is_night": is_night}, f, indent=2
        )


def role_registered_to_channel(channel_id: int):
    """Return the role_id (int) registered to this channel, if any."""
    for role_id, cid in role_channels.items():
        if cid == channel_id:
            return int(role_id)
    return None


# User ID that can run admin-only commands even without Manage Server permission.
ADMIN_BYPASS_USER_ID = 597162804864221206


def is_admin():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.id == ADMIN_BYPASS_USER_ID:
            return True
        if interaction.user.guild_permissions.manage_guild:
            return True
        raise app_commands.MissingPermissions(["manage_guild"])

    return app_commands.check(predicate)


# ---------------------------------------------------------------------------
# BOT
# ---------------------------------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # needed to check roles reliably


class WhisperClient(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        load_mappings()
        load_settings()
        guild = discord.Object(id=GUILD_ID) if GUILD_ID else None
        if guild:
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            print(f"Synced {len(synced)} command(s) to guild {GUILD_ID}.")
        else:
            synced = await self.tree.sync()
            print(f"GUILD_ID not set — synced {len(synced)} command(s) globally (can take up to an hour to appear).")


client = WhisperClient()


@client.tree.command(
    name="linkwhisperchannel",
    description="(Admin) Link a private channel to a player's role for whispering.",
)
@app_commands.describe(channel="The player's private submission channel", role="The player's unique role")
@is_admin()
async def linkwhisperchannel(
    interaction: discord.Interaction, channel: discord.TextChannel, role: discord.Role
):
    role_channels[str(role.id)] = channel.id
    save_mappings()
    await interaction.response.send_message(
        f"Linked {channel.mention} to role **{role.name}**.", ephemeral=True
    )


@linkwhisperchannel.error
async def linkwhisperchannel_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            "You need Manage Server permission to do that.", ephemeral=True
        )
    else:
        raise error


@client.tree.command(
    name="unlinkwhisperchannel",
    description="(Admin) Remove a role's link to its private whisper channel.",
)
@app_commands.describe(role="The player's role to unlink")
@is_admin()
async def unlinkwhisperchannel(interaction: discord.Interaction, role: discord.Role):
    if str(role.id) not in role_channels:
        await interaction.response.send_message(
            f"**{role.name}** isn't linked to any channel.", ephemeral=True
        )
        return

    del role_channels[str(role.id)]
    save_mappings()
    await interaction.response.send_message(
        f"Unlinked **{role.name}** from its whisper channel.", ephemeral=True
    )


@unlinkwhisperchannel.error
async def unlinkwhisperchannel_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            "You need Manage Server permission to do that.", ephemeral=True
        )
    else:
        raise error


@client.tree.command(
    name="setblackmailer",
    description="(Admin) Set the channel that receives a full copy of every whisper.",
)
@app_commands.describe(channel="The channel that will receive full whisper contents")
@is_admin()
async def setblackmailer(interaction: discord.Interaction, channel: discord.TextChannel):
    global blackmailer_channel_id
    blackmailer_channel_id = channel.id
    save_settings()
    await interaction.response.send_message(
        f"{channel.mention} will now receive a full copy of every whisper.", ephemeral=True
    )


@setblackmailer.error
async def setblackmailer_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            "You need Manage Server permission to do that.", ephemeral=True
        )
    else:
        raise error


@client.tree.command(name="togglenight", description="Toggle night/day. Whispers are blocked during the night.")
async def togglenight(interaction: discord.Interaction):
    if interaction.user.id not in NIGHT_TOGGLE_USER_IDS:
        await interaction.response.send_message(
            "You aren't allowed to use this command.", ephemeral=True
        )
        return

    global is_night
    is_night = not is_night
    save_settings()

    public_channel = client.get_channel(PUBLIC_LOG_CHANNEL_ID)
    announcement = "It is now nighttime." if is_night else "It is now daytime."
    if public_channel:
        await public_channel.send(announcement)

    await interaction.response.send_message(announcement, ephemeral=True)


@client.tree.command(name="whisper", description="Privately whisper another player.")
@app_commands.describe(role="The player's role to whisper to", message="Your message")
async def whisper(interaction: discord.Interaction, role: discord.Role, message: str):
    if is_night:
        await interaction.response.send_message(
            "Whispers cannot be sent during the night.", ephemeral=True
        )
        return

    sender = interaction.user
    channel_id = interaction.channel_id

    sender_role_id = role_registered_to_channel(channel_id)
    if sender_role_id is None or sender_role_id not in [r.id for r in sender.roles]:
        await interaction.response.send_message(
            "You can only whisper from your own private submission channel.",
            ephemeral=True,
        )
        return

    target_channel_id = role_channels.get(str(role.id))
    if target_channel_id is None:
        await interaction.response.send_message(
            f"The role **{role.name}** isn't linked to a private channel.",
            ephemeral=True,
        )
        return

    target_channel = client.get_channel(target_channel_id)
    public_channel = client.get_channel(PUBLIC_LOG_CHANNEL_ID)
    spy_channel = client.get_channel(blackmailer_channel_id) if blackmailer_channel_id else None

    if target_channel is None:
        await interaction.response.send_message(
            "Couldn't find the target's channel. Check the linked channel still exists.",
            ephemeral=True,
        )
        return

    if target_channel_id == channel_id:
        await interaction.response.send_message("You can't whisper to yourself.", ephemeral=True)
        return

    # 1. Deliver full message to the target's private channel.
    sender_role = interaction.guild.get_role(sender_role_id)
    sender_label = sender_role.name if sender_role else sender.display_name
    await target_channel.send(f"Whisper from {sender_label}: {message}")

    # 2. Public log — no content, just the fact a whisper happened.
    if public_channel:
        await public_channel.send(f"{sender_label} is whispering {role.name}")

    # 3. Blackmailer channel — full copy, like a moderator log.
    if spy_channel:
        await spy_channel.send(f"{sender_label} Whispers {role.name}: {message}")

    await interaction.channel.send(f"You whispered {role.name}: {message}")
    await interaction.response.send_message("Whisper sent.", ephemeral=True)


if __name__ == "__main__":
    client.run(BOT_TOKEN)