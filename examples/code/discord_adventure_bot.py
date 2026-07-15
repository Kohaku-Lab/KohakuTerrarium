"""
Discord Adventure Bot — agents as NPC characters in an interactive RPG.

Unlike the agent-first example in ``examples/agent-apps/discord_bot``,
this program owns Discord interactions and invokes agents only as NPCs.

Here, the Discord bot is the main program. It has its own slash
commands, button interactions, thread management, game state machine.
Agents are NPC characters: created on demand, fed game context by
the bot, and destroyed when the adventure ends.

Why programmatic access?
  - discord.py owns the event loop — agents don't run autonomously
  - Agent configs are generated dynamically per NPC (personality,
    knowledge, relationship to player — all computed at runtime)
  - Game state machine in bot code decides WHEN to invoke which NPC
  - Discord interactions (buttons, embeds, threads) are the UI —
    agent output is just text placed into embeds by bot code
  - Multiple agents per adventure, created/destroyed per session

Requirements:
    pip install discord.py kohakuterrarium
"""

import asyncio
from dataclasses import dataclass, field

import discord
from discord import app_commands

from kohakuterrarium import Terrarium
from kohakuterrarium.core.config import load_agent_config
from kohakuterrarium.terrarium.creature_host import Creature

# One engine hosts every NPC creature for the whole bot process.
ENGINE = Terrarium()

@dataclass
class NPC:
    """Associate a live creature with its in-game identity."""

    name: str
    role: str
    creature: Creature


@dataclass
class Adventure:
    """Track one Discord-thread adventure and its live NPCs."""

    thread_id: int
    player_id: int
    npcs: dict[str, NPC] = field(default_factory=dict)
    history: list[str] = field(default_factory=list)
    state: str = "tavern"  # Encodes the active interaction state.


# Thread IDs isolate concurrent adventures and route incoming messages.
adventures: dict[int, Adventure] = {}


async def create_npc(
    name: str,
    role: str,
    personality: str,
    knowledge: str,
    game_context: str,
) -> NPC:
    """Create an NPC agent with a dynamically generated config.

    The system prompt is built from game context — not a static file.
    Each NPC gets a unique personality and knowledge set based on the
    current adventure state.
    """
    config = load_agent_config("@kt-biome/creatures/general")

    # NPCs only converse, so remove orchestration capabilities and use a small model.
    config.name = f"npc-{name}"
    config.model = "openai/gpt-4.1-mini"
    config.tool_format = "native"
    config.tools = []
    config.subagents = []
    config.system_prompt = (
        f"You are {name}, {role} in a fantasy RPG.\n\n"
        f"Personality: {personality}\n\n"
        f"What you know:\n{knowledge}\n\n"
        f"Current situation:\n{game_context}\n\n"
        "Stay in character. Respond in 1-3 sentences. "
        "Never break the fourth wall."
    )

    # Headless I/O keeps NPC speech inside TurnResult for Discord rendering.
    creature = await ENGINE.add_creature(config, io="headless")
    return NPC(name=name, role=role, creature=creature)


async def talk_to_npc(npc: NPC, message: str) -> str:
    """Run one NPC turn and return an in-character fallback on failure."""
    result = await npc.creature.run(message, timeout=120, raise_on_error=False)
    return result.text.strip() if result.ok else f"*{npc.name} is at a loss*"


async def destroy_adventure(adventure: Adventure) -> None:
    """Remove every NPC creature owned by an adventure."""
    for npc in adventure.npcs.values():
        await ENGINE.remove_creature(npc.creature)
    adventure.npcs.clear()


class AdventureBot(discord.Client):
    """Own Discord commands and interactions for all adventures."""

    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        """Synchronize slash commands after Discord client setup."""
        await self.tree.sync()


bot = AdventureBot()


@bot.tree.command(name="adventure", description="Start a new adventure")
async def start_adventure(interaction: discord.Interaction):
    """Create an adventure thread, spawn its NPCs, and send the opening UI."""
    await interaction.response.defer()

    thread = await interaction.channel.create_thread(
        name=f"Adventure - {interaction.user.display_name}",
        type=discord.ChannelType.public_thread,
    )

    adventure = Adventure(
        thread_id=thread.id,
        player_id=interaction.user.id,
    )

    adventure.npcs["barkeep"] = await create_npc(
        name="Greta",
        role="the tavern barkeeper",
        personality="Gruff but kind. Knows everyone's secrets. Speaks plainly.",
        knowledge="A dragon was spotted near the northern mountains last week. "
        "The local lord is offering 500 gold for anyone who investigates.",
        game_context="The player just walked into your tavern on a rainy evening.",
    )

    adventure.npcs["stranger"] = await create_npc(
        name="Kael",
        role="a mysterious hooded stranger in the corner",
        personality="Cryptic, speaks in riddles. Actually a retired dragon hunter.",
        knowledge="You know the dragon's weakness: it sleeps at noon. "
        "You won't share this freely — the player must earn your trust.",
        game_context="You're sitting in the corner of a tavern, watching a new "
        "adventurer walk in. You've been waiting for someone brave enough.",
    )

    adventures[thread.id] = adventure

    embed = discord.Embed(
        title="The Rusty Tankard",
        description=(
            "Rain hammers the cobblestones as you push open the heavy oak door. "
            "The tavern is warm, lit by a crackling fireplace. A barkeeper polishes "
            "glasses behind the counter. In the far corner, a hooded figure sits "
            "alone, nursing a drink.\n\n"
            "**Who do you approach?**"
        ),
        color=0xD4920A,
    )

    # The bot owns interaction UI; agents provide only character dialogue.
    view = NPCSelectView(adventure)
    await thread.send(embed=embed, view=view)
    await interaction.followup.send(f"Adventure started in {thread.mention}!")


class NPCSelectView(discord.ui.View):
    """Present one interaction button per adventure NPC."""

    def __init__(self, adventure: Adventure):
        super().__init__(timeout=300)
        self.adventure = adventure
        for npc_key, npc in adventure.npcs.items():
            self.add_item(NPCButton(npc_key, npc.name, npc.role))


class NPCButton(discord.ui.Button):
    """Route a Discord button interaction to one NPC."""

    def __init__(self, npc_key: str, name: str, role: str):
        super().__init__(label=f"Talk to {name}", style=discord.ButtonStyle.primary)
        self.npc_key = npc_key

    async def callback(self, interaction: discord.Interaction):
        adventure = adventures.get(interaction.channel_id)
        if not adventure:
            return
        npc = adventure.npcs.get(self.npc_key)
        if not npc:
            return

        await interaction.response.defer()

        # Agent invocation remains behind the bot-managed button interaction.
        greeting = await talk_to_npc(
            npc, "A new adventurer approaches you. Greet them in character."
        )

        embed = discord.Embed(
            title=npc.name,
            description=greeting,
            color=0x5A4FCF,
        )
        embed.set_footer(text="Type a message in this thread to continue talking")
        await interaction.followup.send(embed=embed)

        # The state string routes subsequent free-text messages to this NPC.
        adventure.state = f"talking:{self.npc_key}"


@bot.event
async def on_message(message: discord.Message):
    """Route the adventure owner's thread message to the active NPC."""
    if message.author.bot:
        return

    adventure = adventures.get(message.channel.id)
    if not adventure:
        return
    if message.author.id != adventure.player_id:
        return

    if not adventure.state.startswith("talking:"):
        return

    npc_key = adventure.state.split(":", 1)[1]
    npc = adventure.npcs.get(npc_key)
    if not npc:
        return

    adventure.history.append(f"Player: {message.content}")
    response = await talk_to_npc(npc, message.content)
    adventure.history.append(f"{npc.name}: {response}")

    embed = discord.Embed(
        title=npc.name,
        description=response,
        color=0x5A4FCF,
    )
    await message.channel.send(embed=embed)


@bot.tree.command(name="arena", description="Two NPCs debate a topic")
@app_commands.describe(topic="What should they debate?")
async def arena(interaction: discord.Interaction, topic: str):
    """Run a bot-sequenced three-round debate between temporary NPCs."""
    await interaction.response.defer()

    npc_for = await create_npc(
        name="Scholar Aldric",
        role="a scholar arguing FOR the position",
        personality="Eloquent, logical, cites historical examples.",
        knowledge=f"You believe: {topic}. Argue in favor.",
        game_context="You are in a public debate in the town square.",
    )
    npc_against = await create_npc(
        name="Merchant Vera",
        role="a merchant arguing AGAINST the position",
        personality="Practical, sharp-witted, focuses on consequences.",
        knowledge=f"You oppose: {topic}. Argue against.",
        game_context="You are in a public debate in the town square.",
    )

    try:
        thread = await interaction.channel.create_thread(
            name=f"Arena: {topic[:50]}",
            type=discord.ChannelType.public_thread,
        )
        await interaction.followup.send(f"Debate started in {thread.mention}!")

        # Bot-side sequencing guarantees alternating turns for three rounds.
        last_argument = f"The topic is: {topic}"
        for round_num in range(1, 4):
            arg_for = await talk_to_npc(npc_for, last_argument)
            embed = discord.Embed(
                title=f"Round {round_num} — Scholar Aldric (FOR)",
                description=arg_for,
                color=0x4CAF50,
            )
            await thread.send(embed=embed)

            arg_against = await talk_to_npc(npc_against, arg_for)
            embed = discord.Embed(
                title=f"Round {round_num} — Merchant Vera (AGAINST)",
                description=arg_against,
                color=0xF44336,
            )
            await thread.send(embed=embed)

            last_argument = arg_against
            await asyncio.sleep(1)  # Keep successive Discord embeds readable.

        await thread.send("**The debate has concluded! React to vote for the winner.**")

    finally:
        await ENGINE.remove_creature(npc_for.creature)
        await ENGINE.remove_creature(npc_against.creature)


@bot.tree.command(name="end", description="End the current adventure")
async def end_adventure(interaction: discord.Interaction):
    """End the current thread's adventure and remove its NPC creatures."""
    adventure = adventures.pop(interaction.channel_id, None)
    if adventure:
        await destroy_adventure(adventure)
        await interaction.response.send_message("Adventure ended. All NPCs dismissed.")
    else:
        await interaction.response.send_message("No active adventure in this thread.")


if __name__ == "__main__":
    import os

    bot.run(os.environ["DISCORD_TOKEN"])
