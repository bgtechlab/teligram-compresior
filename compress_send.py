"""
Usage: python compress_send.py <url> <target_mb>
Env: API_ID, API_HASH, BOT_TOKEN, CHAT_ID
"""
import asyncio
import glob
import json
import os
import shutil
import sys
import tempfile

import yt_dlp
from pyrogram import Client

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = int(os.environ["CHAT_ID"])


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


def download_url(url, folder):
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


async def compress(src, dst, target_mb):
    dur = await duration(src)
    audio_k = 96
    video_k = int(target_mb * 8 * 1024 * 0.93 / dur - audio_k)
    if video_k < 100:
        raise RuntimeError("Target size is video ke liye bahut chhota hai")
    await run(
        "ffmpeg", "-y", "-i", src,
        "-c:v", "libx264", "-preset", "veryfast",
        "-b:v", f"{video_k}k", "-maxrate", f"{int(video_k * 1.3)}k",
        "-bufsize", f"{video_k * 2}k",
        "-vf", "scale='min(1280,iw)':-2",
        "-c:a", "aac", "-b:a", f"{audio_k}k",
        "-movflags", "+faststart", dst,
    )


async def main(url, target_mb):
    work = tempfile.mkdtemp()
    app = Client("job", api_id=API_ID, api_hash=API_HASH,
                 bot_token=BOT_TOKEN, in_memory=True)
    await app.start()
    try:
        src = await asyncio.to_thread(download_url, url, work)
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
