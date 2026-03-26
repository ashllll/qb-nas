"""MiniMax TTS — 下载/爬取完成语音通知"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import shutil
import sys
import tempfile
from typing import Optional

import httpx

from config import settings

log = logging.getLogger(__name__)

TTS_URL   = "https://api.minimaxi.com/v1/t2a_v2"
TTS_MODEL = "speech-02-turbo"
VOICE_ID  = "female-shaonv"

NOTIFY_TEMPLATES = {
    "crawl_done":    "爬取完成，共发现 {total} 个磁力链接",
    "download_done": "已将 {count} 个资源添加到下载队列",
    "classify_done": "分类完成，{category} 类资源 {count} 个",
    "error":         "操作失败，{msg}",
}


class MinimaxTTS:
    def __init__(self):
        self._enabled = settings.TTS_ENABLED
        self._api_key = settings.MINIMAX_API_KEY

    async def speak(self, text: str) -> bool:
        if not self._enabled:
            return False
        asyncio.create_task(self._speak_async(text))
        return True

    async def notify(self, event: str, **kwargs) -> bool:
        template = NOTIFY_TEMPLATES.get(event, "")
        if not template:
            return False
        return await self.speak(template.format(**kwargs))

    async def _speak_async(self, text: str):
        try:
            audio_data = await self._synthesize(text)
            if audio_data:
                await self._play(audio_data)
        except Exception as e:
            log.warning(f"TTS 播放失败: {e}")

    async def _synthesize(self, text: str) -> Optional[bytes]:
        payload = {
            "model": TTS_MODEL,
            "text":  text,
            "stream": True,
            "voice_setting": {"voice_id": VOICE_ID, "speed": 1.0, "vol": 1.0, "pitch": 0},
            "audio_setting": {"sample_rate": 32000, "bitrate": 128000, "format": "mp3", "channel": 1},
        }
        chunks: list[bytes] = []
        async with httpx.AsyncClient(timeout=30) as client:
            async with client.stream(
                "POST", TTS_URL,
                headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
                content=json.dumps(payload),
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str in ("", "[DONE]"):
                        continue
                    try:
                        data = json.loads(data_str)
                        b64  = data.get("data", {}).get("audio", "")
                        if b64:
                            chunks.append(base64.b64decode(b64))
                    except Exception:
                        pass
        return b"".join(chunks) if chunks else None

    async def _play(self, audio_data: bytes):
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(audio_data)
            tmp = f.name
        try:
            if sys.platform == "darwin":
                proc = await asyncio.create_subprocess_exec(
                    "afplay", tmp,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.wait()
            elif sys.platform == "win32":
                proc = await asyncio.create_subprocess_exec(
                    "powershell", "-Command", f"Start-Process '{tmp}' -Wait",
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.wait()
            else:
                # shutil.which 是纯 Python，不阻塞事件循环
                for player in ("mpg123", "ffplay", "paplay"):
                    path = shutil.which(player)
                    if path:
                        proc = await asyncio.create_subprocess_exec(
                            path, "-q", tmp,
                            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                        )
                        await proc.wait()
                        break
                else:
                    log.warning("未找到音频播放器 (mpg123/ffplay/paplay)")
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass


tts = MinimaxTTS()
