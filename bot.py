"""
Discord -> OBS message overlay.
 
Right-click a message > Apps > "Show on stream"  -> it appears in an OBS Browser Source.
Use /clearstream to remove it early.
 
Run:   DISCORD_TOKEN=xxxx python bot.py
OBS:   Add a Browser Source with URL http://localhost:8765  (1280x720, transparent)
"""
import asyncio
import json
import os
import re
from dotenv import load_dotenv
import discord
from aiohttp import web
from discord import app_commands

load_dotenv()
 
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = [int(g) for g in os.getenv("GUILD_ID", "").replace(" ", "").split(",") if g]  # optional: instant command sync to one server
print(GUILD_ID)
PORT = int(os.getenv("PORT", "8765"))
AUTO_CLEAR_SECONDS = 0  # 0 = stay on screen until /clearstream
 
# ---------------------------------------------------------------- overlay server
OVERLAY_HTML = """<!doctype html>
<html><head><meta charset="utf-8">
<style>
  html, body { margin:0; background:transparent; overflow:hidden;
               font-family: 'Segoe UI', system-ui, sans-serif; }
  #card { position:absolute; left:40px; right:40px; bottom:40px;
          display:flex; gap:16px; align-items:flex-start;
          background:rgba(20,20,28,.88); color:#fff; border-radius:16px;
          padding:18px 22px; box-shadow:0 8px 30px rgba(0,0,0,.45);
          opacity:0; transform:translateY(20px);
          transition:opacity .35s ease, transform .35s ease; }
  #card.show { opacity:1; transform:translateY(0); }
  #avatar { width:56px; height:56px; border-radius:50%; flex:none; background:#444; }
  #name { font-weight:700; font-size:22px; color:#9db4ff; margin-bottom:4px; }
  #text { font-size:20px; line-height:1.3; white-space:pre-wrap; word-break:break-word; }
  #img { max-width:100%; max-height:240px; border-radius:10px; margin-top:10px; display:none; }
</style></head>
<body>
  <div id="card">
    <img id="avatar">
    <div>
      <div id="name"></div>
      <div id="text"></div>
      <img id="img">
    </div>
  </div>
<script>
  const card = document.getElementById('card');
  const es = new EventSource('/events');
  es.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (!m) { card.classList.remove('show'); return; }
    // textContent (not innerHTML) so message text can never inject HTML
    document.getElementById('name').textContent = m.author;
    document.getElementById('text').textContent = m.content;
    document.getElementById('avatar').src = m.avatar || '';
    const img = document.getElementById('img');
    if (m.image) { img.src = m.image; img.style.display = 'block'; }
    else { img.style.display = 'none'; }
    card.classList.add('show');
  };
</script></body></html>"""
 
clients: set[asyncio.Queue] = set()
current: dict | None = None
clear_task: asyncio.Task | None = None
 
 
async def broadcast(payload: dict | None) -> None:
    global current
    current = payload
    data = f"data: {json.dumps(payload)}\n\n"
    for q in list(clients):
        q.put_nowait(data)
 
 
async def index(_request: web.Request) -> web.Response:
    return web.Response(text=OVERLAY_HTML, content_type="text/html")
 
 
async def events(request: web.Request) -> web.StreamResponse:
    resp = web.StreamResponse(
        headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"}
    )
    await resp.prepare(request)
    q: asyncio.Queue = asyncio.Queue()
    clients.add(q)
    try:
        await resp.write(f"data: {json.dumps(current)}\n\n".encode())
        while True:
            await resp.write((await q.get()).encode())
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        clients.discard(q)
    return resp
 
 
async def start_web_server() -> None:
    app = web.Application()
    app.add_routes([web.get("/", index), web.get("/events", events)])
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
 
 
# ---------------------------------------------------------------- discord bot
class StageBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
 
    async def setup_hook(self) -> None:
        await start_web_server()
        if GUILD_ID:
            for gid in GUILD_ID:
                guild = discord.Object(id=gid)
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                await self.tree.sync(guild=guild)
        
        else:
            await self.tree.sync()
        print("Synced commands:", [c.name for c in synced])
 
 
bot = StageBot()
 
 
async def schedule_auto_clear() -> None:
    global clear_task
    if clear_task:
        clear_task.cancel()
    if AUTO_CLEAR_SECONDS > 0:
        async def _clear():
            await asyncio.sleep(AUTO_CLEAR_SECONDS)
            await broadcast(None)
        clear_task = asyncio.create_task(_clear())
 
 
@bot.tree.context_menu(name="Show on stream")
@app_commands.default_permissions(manage_messages=True)  # server admins can change who sees this
async def show_on_stream(interaction: discord.Interaction, message: discord.Message):
    image = next(
        (a.url for a in message.attachments
         if a.content_type and a.content_type.startswith("image/")),
        None,
    )
    msg = re.sub(r"<.*?>", "", message.content)
    color = str(message.author.color)
    await broadcast({
        "author": message.author.display_name,
        "avatar": message.author.display_avatar.url,
        "content": msg,
        "image": image,
        "color": color,
    })
    await schedule_auto_clear()
    await interaction.response.send_message("Showing on stream.", ephemeral=True)
 
 
@bot.tree.command(name="clearstream", description="Remove the message from the stream overlay")
@app_commands.default_permissions(manage_messages=True)
async def clearstream(interaction: discord.Interaction):
    await broadcast(None)
    await interaction.response.send_message("Cleared.", ephemeral=True)
 
 
bot.run(TOKEN)