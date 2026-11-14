import os, re, secrets, shutil, tempfile
from urllib.parse import quote

from fastapi import FastAPI, UploadFile, File, Form, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, HTMLResponse, FileResponse
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession
from telethon.tl.functions.messages import ImportChatInviteRequest, CheckChatInviteRequest

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION = os.environ.get("SESSION", "")   # empty at first; fill after /setup
PASSWORD = os.environ["PASSWORD"]         # your site password
_ch = os.environ.get("CHANNEL", "")       # paste a link, @username, or id
CHANNEL = None
CHANNEL_ERR = ""

client = TelegramClient(StringSession(SESSION), API_ID, API_HASH)
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Range", "Accept-Ranges", "Content-Length"],
)

CHUNK = 512 * 1024
pending = {}


async def resolve_channel(raw: str):
    s = raw.strip()
    if not s:
        raise ValueError("CHANNEL is empty")
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    m = re.search(r"t\.me/c/(\d+)", s)
    if m:
        return int("-100" + m.group(1))
    m = re.search(r"(?:t\.me|telegram\.me)/(?:\+|joinchat/)([\w-]+)", s)
    if m:
        h = m.group(1)
        info = await client(CheckChatInviteRequest(h))
        if hasattr(info, "chat"):          # already a member
            return info.chat
        upd = await client(ImportChatInviteRequest(h))
        return upd.chats[0]
    m = re.search(r"(?:t\.me|telegram\.me)/([A-Za-z0-9_]{4,})", s)
    name = m.group(1) if m else s.lstrip("@")
    return await client.get_entity(name)


def ch():
    if CHANNEL is None:
        raise HTTPException(503, "Channel not set: " + (CHANNEL_ERR or "check CHANNEL"))
    return CHANNEL


@app.on_event("startup")
async def start():
    global CHANNEL, CHANNEL_ERR
    await client.connect()
    if await client.is_user_authorized():
        await client.get_dialogs()
        try:
            CHANNEL = await resolve_channel(_ch)
        except Exception as e:
            CHANNEL_ERR = str(e)


def check(k: str):
    if not secrets.compare_digest(k or "", PASSWORD):
        raise HTTPException(401, "Wrong password")


@app.get("/")
async def home():
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cloud.html")
    if os.path.exists(here):
        return FileResponse(here)
    return {"ok": True}


# ---------- one-time login to get a session for THIS backend ----------
SETUP_HTML = """<!DOCTYPE html><html><head><meta name=viewport content="width=device-width,initial-scale=1">
<title>Setup</title><style>body{font-family:sans-serif;padding:16px;max-width:480px;margin:auto}
input,button{width:100%;padding:10px;margin:6px 0;font-size:16px;box-sizing:border-box}
textarea{width:100%;height:140px}</style></head><body>
<h3>One-time Telegram login</h3>
<input id=k placeholder="Site password (PASSWORD)" type=password>
<input id=phone placeholder="Phone with country code, e.g. +977...">
<button onclick="step1()">Send code</button>
<input id=code placeholder="Code from Telegram">
<input id=pw placeholder="2-step password (only if you have one)" type=password>
<button onclick="step2()">Log in</button>
<p id=msg></p><textarea id=out readonly placeholder="Session appears here"></textarea>
<script>
async function post(url, o){const f=new FormData();for(const x in o)f.append(x,o[x]);
const r=await fetch(url,{method:'POST',body:f});return r.json();}
async function step1(){const d=await post('/setup/phone',{k:k.value,phone:phone.value});
msg.textContent=d.ok?'Code sent. Check your Telegram app.':(d.detail||JSON.stringify(d));}
async function step2(){const d=await post('/setup/code',{k:k.value,code:code.value,pw:pw.value});
if(d.session){out.value=d.session;msg.textContent='Copy this into Render as SESSION.';}
else if(d.need_password){msg.textContent='Enter your 2-step password and tap Log in again.';}
else msg.textContent=d.detail||JSON.stringify(d);}
</script></body></html>"""


@app.get("/setup", response_class=HTMLResponse)
async def setup_page():
    return SETUP_HTML


@app.post("/setup/phone")
async def setup_phone(phone: str = Form(...), k: str = Form("")):
    check(k)
    r = await client.send_code_request(phone)
    pending["phone"] = phone
    pending["hash"] = r.phone_code_hash
    return {"ok": True}


@app.post("/setup/code")
async def setup_code(code: str = Form(...), pw: str = Form(""), k: str = Form("")):
    check(k)
    try:
        await client.sign_in(pending["phone"], code, phone_code_hash=pending["hash"])
    except SessionPasswordNeededError:
        if not pw:
            return {"need_password": True}
        await client.sign_in(password=pw)
    return {"session": client.session.save()}


# ---------- files ----------
@app.get("/api/files")
async def files(k: str = ""):
    check(k)
    out = []
    async for m in client.iter_messages(ch(), limit=500):
        f = m.file
        if not f:
            continue
        out.append({
            "id": m.id,
            "name": f.name or f"file_{m.id}",
            "size": f.size or 0,
            "mime": f.mime_type or "",
            "date": m.date.isoformat(),
        })
    return out


@app.post("/api/upload")
async def upload(file: UploadFile = File(...), k: str = Form("")):
    check(k)
    name = re.sub(r"[^\w.\- ]", "_", file.filename or "file")
    d = tempfile.mkdtemp()
    path = os.path.join(d, name)
    try:
        with open(path, "wb") as out:
            while True:
                b = await file.read(1024 * 1024)
                if not b:
                    break
                out.write(b)
        msg = await client.send_file(ch(), path, force_document=True, caption=name)
        return {"id": msg.id}
    finally:
        shutil.rmtree(d, ignore_errors=True)


@app.delete("/api/files/{mid}")
async def delete(mid: int, k: str = ""):
    check(k)
    await client.delete_messages(ch(), [mid])
    return {"ok": True}


@app.get("/api/stream/{mid}")
async def stream(mid: int, request: Request, k: str = "", dl: int = 0):
    check(k)
    msg = await client.get_messages(ch(), ids=mid)
    if not msg or not msg.file:
        raise HTTPException(404, "Not found")

    size = msg.file.size
    mime = msg.file.mime_type or "application/octet-stream"
    name = msg.file.name or f"file_{mid}"

    start, end, status = 0, size - 1, 200
    rng = request.headers.get("range")
    if rng:
        m = re.match(r"bytes=(\d*)-(\d*)", rng)
        if m:
            if m.group(1):
                start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
            elif m.group(2):
                start = max(size - int(m.group(2)), 0)
            status = 206
    if start > end:
        raise HTTPException(416, "Bad range")
    length = end - start + 1

    async def gen():
        aligned = start - (start % CHUNK)
        skip = start - aligned
        left = length
        async for chunk in client.iter_download(msg.media, offset=aligned, request_size=CHUNK):
            if skip:
                chunk = chunk[skip:]
                skip = 0
            if len(chunk) > left:
                chunk = chunk[:left]
            left -= len(chunk)
            yield chunk
            if left <= 0:
                break

    disp = "attachment" if dl else "inline"
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        "Content-Disposition": f"{disp}; filename*=UTF-8''{quote(name)}",
    }
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(gen(), status_code=status, media_type=mime, headers=headers)
