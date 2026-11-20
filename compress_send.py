"""
Usage: python compress_send.py <url> <target_mb>
Env: API_ID, API_HASH, BOT_TOKEN, CHAT_ID
Optional: SESSION_STRING (user account, needed for private channels
          or if the bot cannot read a public channel)
"""
import asyncio
import glob
import json
import os
import re
import shutil
import sys
import tempfile

import yt_dlp
from pyrogram import Client

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = int(os.environ["CHAT_ID"])
SESSION_STRING = os.environ.get("SESSION_STRING", "").strip()

TME_RE = re.compile(r"https?://t\.me/(?:c/(\d+)|([A-Za-z0-9_]+))/(?:\d+/)?(\d+)")


async def run(*cmd):
    p = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await p.communicate()
    if p.returncode:
        raise RuntimeError(err.decode(errors="ignore")[-400:])
    return out.decode()


async def duration(path):
    out = await run("ffprobe", "-v", "error", "-show_entries",
                    "format=duration", "-of", "json", path)
    return float(json.loads(out)["format"]["duration"])


def download_web(url, folder):
    opts = {
        "outtmpl": f"{folder}/in.%(ext)s",
        "format": "bv*+ba/b",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
    }
    with yt_dlp.YoutubeDL(opts) as y:
        y.download([url])
    files = glob.glob(f"{folder}/in.*")
    if not files:
        raise RuntimeError("Download nahi hua")
    return files[0]


async def download_tme(url, folder, bot):
    """Download a video from a t.me post link."""
    m = TME_RE.match(url)
    private_id, username, msg_id = m.group(1), m.group(2), int(m.group(3))
    chat = int(f"-100{private_id}") if private_id else username

    clients = []
    if not private_id:  # public channel: try the bot first
        clients.append(bot)
    if SESSION_STRING:
        user = Client("user", api_id=API_ID, api_hash=API_HASH,
                      session_string=SESSION_STRING, in_memory=True)
        clients.append(user)
    if not clients:
        raise RuntimeError("Private channel ke liye SESSION_STRING secret chahiye")

    last_err = None
    for c in clients:
        started = False
        try:
            if c is not bot:
                await c.start()
                started = True
            msg = await c.get_messages(chat, msg_id)
            if not msg or msg.empty or not (msg.video or msg.document):
                raise RuntimeError("Is link me video nahi mila")
            path = await c.download_media(msg, file_name=f"{folder}/in.mp4")
            if path:
                return path
        except Exception as e:  # try next client
            last_err = e
        finally:
            if started:
                await c.stop()
    raise RuntimeError(f"t.me se download fail: {last_err}")


async def compress(src, dst, target_mb):
    dur = await duration(src)
    total_k = target_mb * 8 * 1024 * 0.93 / dur  # kbit/s for video+audio

    # Audio gets smaller on long/low-bitrate videos
    if total_k < 250:
        audio_k, channels = 48, "1"
    elif total_k < 500:
        audio_k, channels = 64, "2"
    else:
        audio_k, channels = 96, "2"

    video_k = int(total_k - audio_k)
    if video_k < 30:
        need = int(dur * (audio_k + 30) / (8 * 1024 * 0.93)) + 1
        raise RuntimeError(
            f"Video {dur / 60:.0f} min ka hai, {target_mb} MB bahut chhota hai. "
            f"Kam se kam {need} MB rakho."
        )

    # Lower bitrate -> lower resolution and fps so quality stays watchable
    if video_k >= 800:
        height, fps = 720, None
    elif video_k >= 400:
        height, fps = 480, None
    elif video_k >= 200:
        height, fps = 360, 24
    else:
        height, fps = 240, 15

    vf = f"scale=-2:'min({height},ih)'"
    if fps:
        vf += f",fps={fps}"

    await run(
        "ffmpeg", "-y", "-i", src,
        "-c:v", "libx264", "-preset", "fast",
        "-b:v", f"{video_k}k", "-maxrate", f"{int(video_k * 1.3)}k",
        "-bufsize", f"{video_k * 2}k",
        "-vf", vf,
        "-c:a", "aac", "-b:a", f"{audio_k}k", "-ac", channels,
        "-movflags", "+faststart", dst,
    )


async def main(url, target_mb):
    work = tempfile.mkdtemp()
    app = Client("job", api_id=API_ID, api_hash=API_HASH,
                 bot_token=BOT_TOKEN, in_memory=True)
    await app.start()
    try:
        if TME_RE.match(url):
            src = await download_tme(url, work, app)
        else:
            src = await asyncio.to_thread(download_web, url, work)
        dst = f"{work}/out.mp4"
        await compress(src, dst, target_mb)
        size = os.path.getsize(dst) / 1024 / 1024
        await app.send_video(CHAT_ID, dst, supports_streaming=True,
                             caption=f"✅ {size:.0f} MB\n{url}")
    except Exception as e:
        await app.send_message(CHAT_ID, f"❌ Fail: {url}\n{e}")
        raise
    finally:
        await app.stop()
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], int(sys.argv[2])))
