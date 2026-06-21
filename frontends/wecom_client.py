"""Minimal WeCom AI Bot WebSocket client used by ``frontends/wecomapp.py``.

This module intentionally implements only the SDK-compatible surface that
GenericAgent uses.  Protocol names and frame shapes are aligned with the
WeCom long-connection documentation and Hermes' WeCom adapter behavior, but
this is a local implementation so GenericAgent no longer imports an
external WeCom AI Bot SDK package.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
import json
import mimetypes
import os
import time
import uuid
from email.message import Message
from typing import Any, Awaitable, Callable
from urllib.parse import unquote, urlparse

DEFAULT_WS_URL = "wss://openws.work.weixin.qq.com"

APP_CMD_SUBSCRIBE = "aibot_subscribe"
APP_CMD_CALLBACK = "aibot_msg_callback"
APP_CMD_LEGACY_CALLBACK = "aibot_callback"
APP_CMD_EVENT_CALLBACK = "aibot_event_callback"
APP_CMD_RESPONSE = "aibot_respond_msg"
APP_CMD_RESPONSE_WELCOME = "aibot_respond_welcome_msg"
APP_CMD_SEND = "aibot_send_msg"
APP_CMD_PING = "ping"
APP_CMD_UPLOAD_MEDIA_INIT = "aibot_upload_media_init"
APP_CMD_UPLOAD_MEDIA_CHUNK = "aibot_upload_media_chunk"
APP_CMD_UPLOAD_MEDIA_FINISH = "aibot_upload_media_finish"

CALLBACK_COMMANDS = {APP_CMD_CALLBACK, APP_CMD_LEGACY_CALLBACK}
NON_RESPONSE_COMMANDS = CALLBACK_COMMANDS | {APP_CMD_EVENT_CALLBACK, APP_CMD_PING}
UPLOAD_CHUNK_SIZE = 512 * 1024
MAX_UPLOAD_CHUNKS = 100
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024

Handler = Callable[..., Any]
Downloader = Callable[[str], Awaitable[Any] | Any]


def generate_req_id(prefix: str) -> str:
    """Return a WeCom-compatible unique request id."""
    return f"{prefix}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"


class WSClient:
    """Small SDK-compatible WeCom WebSocket client.

    The constructor keeps the third-party SDK's millisecond interval options so
    ``frontends/wecomapp.py`` can keep its existing launch behavior unchanged.
    """

    def __init__(
        self,
        bot_id: str,
        secret: str,
        *,
        scene: int | None = None,
        plug_version: str | None = None,
        reconnect_interval: int = 1000,
        max_reconnect_attempts: int = 10,
        max_auth_failure_attempts: int = 5,
        max_reply_queue_size: int = 500,
        heartbeat_interval: int = 30000,
        request_timeout: int = 10000,
        ws_url: str = "",
        ws_options: dict[str, Any] | None = None,
        logger: Any | None = None,
        downloader: Downloader | None = None,
        session_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.bot_id = bot_id
        self.secret = secret
        self.scene = scene
        self.plug_version = plug_version
        self.reconnect_interval = max(reconnect_interval / 1000.0, 0.0)
        self.max_reconnect_attempts = max_reconnect_attempts
        self.max_auth_failure_attempts = max_auth_failure_attempts
        self.max_reply_queue_size = max_reply_queue_size
        self.heartbeat_interval = max(heartbeat_interval / 1000.0, 0.0)
        self.request_timeout = max(request_timeout / 1000.0, 0.001)
        self.ws_url = ws_url or DEFAULT_WS_URL
        self.ws_options = ws_options or {}
        self.logger = logger
        self._downloader = downloader
        self._session_factory = session_factory

        self._listeners: dict[str, list[Handler]] = {}
        self._handler_tasks: set[asyncio.Task[Any]] = set()
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._session: Any = None
        self._ws: Any = None
        self._receive_task: asyncio.Task[Any] | None = None
        self._heartbeat_task: asyncio.Task[Any] | None = None
        self._manual_close = False
        self._started = False

    # ---- Event emitter -------------------------------------------------
    def on(self, event: str, handler: Handler) -> "WSClient":
        self._listeners.setdefault(event, []).append(handler)
        return self

    def off(self, event: str, handler: Handler | None = None) -> "WSClient":
        if handler is None:
            self._listeners.pop(event, None)
        elif event in self._listeners:
            self._listeners[event] = [item for item in self._listeners[event] if item != handler]
        return self

    def emit(self, event: str, *args: Any) -> None:
        for handler in list(self._listeners.get(event, [])):
            try:
                result = handler(*args)
                if inspect.isawaitable(result):
                    task = asyncio.create_task(result)  # SDK-compatible: do not block caller.
                    self._handler_tasks.add(task)
                    task.add_done_callback(lambda t, ev=event: self._on_handler_done(t, ev))
            except Exception as exc:  # pragma: no cover - defensive logging path
                self._log("error", f"Error in WeCom event handler {event!r}: {exc}")

    def _on_handler_done(self, task: asyncio.Task[Any], event: str) -> None:
        self._handler_tasks.discard(task)
        if task.cancelled():
            return
        try:
            exc = task.exception()
        except asyncio.CancelledError:  # pragma: no cover
            return
        if exc is not None:
            self._log("error", f"Error in async WeCom event handler {event!r}: {exc}")

    # ---- Connection management ----------------------------------------
    async def connect(self) -> "WSClient":
        if self._started:
            return self
        self._manual_close = False
        self._started = True
        await self._connect_once()
        return self

    async def disconnect(self) -> None:
        if not self._started and not self._ws:
            return
        self._manual_close = True
        self._started = False
        for task in (self._receive_task, self._heartbeat_task):
            if task and not task.done():
                task.cancel()
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(ConnectionError("WeCom websocket disconnected"))
        self._pending.clear()
        if self._handler_tasks:
            _, pending = await asyncio.wait(self._handler_tasks, timeout=30.0)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        if self._ws is not None:
            close = getattr(self._ws, "close", None)
            if close:
                result = close()
                if inspect.isawaitable(result):
                    await result
            self._ws = None
        if self._session is not None:
            close = getattr(self._session, "close", None)
            if close:
                result = close()
                if inspect.isawaitable(result):
                    await result
            self._session = None
        self.emit("disconnected", "manual close")

    async def _connect_once(self) -> None:
        try:
            if self._session_factory is not None:
                self._session = self._session_factory()
            else:
                import aiohttp  # Lazy import keeps import-smoke working without optional deps installed.

                self._session = aiohttp.ClientSession(trust_env=True)
            self._ws = await self._session.ws_connect(
                self.ws_url,
                heartbeat=max(self.heartbeat_interval * 2, 1.0),
                **self.ws_options,
            )
            self.emit("connected")
            await self._send_subscribe()
            self._receive_task = asyncio.create_task(self._receive_loop())
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        except Exception as exc:
            self.emit("error", exc)
            self._started = False
            raise

    async def _send_subscribe(self) -> None:
        body: dict[str, Any] = {"bot_id": self.bot_id, "secret": self.secret}
        if self.scene is not None:
            body["scene"] = self.scene
        if self.plug_version is not None:
            body["plug_version"] = self.plug_version
        await self._send_json(
            {
                "cmd": APP_CMD_SUBSCRIBE,
                "headers": {"req_id": generate_req_id(APP_CMD_SUBSCRIBE)},
                "body": body,
            }
        )

    async def _receive_loop(self) -> None:
        try:
            async for message in self._ws:
                payload = getattr(message, "data", message)
                if isinstance(payload, (bytes, bytearray)):
                    payload = payload.decode("utf-8")
                if not isinstance(payload, str):
                    continue
                await self._handle_frame(json.loads(payload))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self._manual_close:
                self.emit("error", exc)
        finally:
            if not self._manual_close:
                self.emit("disconnected", "connection closed")
                await self._reconnect_loop()

    async def _reconnect_loop(self) -> None:
        attempts = 0
        while self._started and not self._manual_close:
            if self.max_reconnect_attempts != -1 and attempts >= self.max_reconnect_attempts:
                self.emit("error", RuntimeError("Max WeCom reconnect attempts reached"))
                return
            attempts += 1
            self.emit("reconnecting", attempts)
            await asyncio.sleep(min(self.reconnect_interval * (2 ** (attempts - 1)), 30.0))
            try:
                await self._connect_once()
                return
            except Exception:
                continue

    async def _heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.heartbeat_interval)
                await self._send_json(
                    {"cmd": APP_CMD_PING, "headers": {"req_id": generate_req_id(APP_CMD_PING)}, "body": {}}
                )
        except asyncio.CancelledError:
            pass

    # ---- Dispatch and request correlation -----------------------------
    async def _handle_frame(self, frame: dict[str, Any]) -> None:
        cmd = str(frame.get("cmd") or "")
        req_id = self._payload_req_id(frame)

        if req_id.startswith(APP_CMD_SUBSCRIBE):
            self._raise_for_wecom_error(frame, "WeCom authentication")
            self.emit("authenticated")
            return

        if req_id and req_id in self._pending and cmd not in NON_RESPONSE_COMMANDS:
            future = self._pending.get(req_id)
            if future and not future.done():
                future.set_result(frame)
            return

        if cmd in CALLBACK_COMMANDS:
            self._dispatch_message(frame)
            return
        if cmd == APP_CMD_EVENT_CALLBACK:
            self._dispatch_event(frame)
            return
        if cmd == APP_CMD_PING:
            return

    def _dispatch_message(self, frame: dict[str, Any]) -> None:
        body = frame.get("body") if isinstance(frame.get("body"), dict) else {}
        self.emit("message", frame)
        msgtype = str(body.get("msgtype") or "")
        if msgtype:
            self.emit(f"message.{msgtype}", frame)

    def _dispatch_event(self, frame: dict[str, Any]) -> None:
        body = frame.get("body") if isinstance(frame.get("body"), dict) else {}
        event = body.get("event") if isinstance(body.get("event"), dict) else {}
        event_type = str(event.get("eventtype") or "")
        self.emit("event", frame)
        if event_type:
            self.emit(f"event.{event_type}", frame)
        if event_type == "disconnected_event":
            self.emit("disconnected", "server disconnected this connection")

    async def _send_json(self, frame: dict[str, Any]) -> None:
        if self._ws is None:
            raise ConnectionError("WeCom websocket is not connected")
        if hasattr(self._ws, "send_json"):
            await self._ws.send_json(frame)
            return
        data = json.dumps(frame, ensure_ascii=False)
        if hasattr(self._ws, "send_str"):
            await self._ws.send_str(data)
        else:
            await self._ws.send(data)

    async def _send_request(self, cmd: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self._send_correlated_request(generate_req_id(cmd), cmd, body)

    async def _send_correlated_request(
        self, req_id: str, cmd: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        normalized_req_id = str(req_id or "").strip()
        if not normalized_req_id:
            raise ValueError("headers.req_id is required")
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[normalized_req_id] = future
        try:
            await self._send_json({"cmd": cmd, "headers": {"req_id": normalized_req_id}, "body": body})
            response = await asyncio.wait_for(future, timeout=self.request_timeout)
            self._raise_for_wecom_error(response, cmd)
            return response
        finally:
            self._pending.pop(normalized_req_id, None)

    @staticmethod
    def _payload_req_id(frame: dict[str, Any]) -> str:
        headers = frame.get("headers") if isinstance(frame.get("headers"), dict) else {}
        return str(headers.get("req_id") or "")

    @staticmethod
    def _raise_for_wecom_error(frame: dict[str, Any], operation: str) -> None:
        errcode = frame.get("errcode", 0)
        if errcode not in (0, None):
            raise RuntimeError(f"{operation} failed: {frame.get('errmsg', '')} (errcode={errcode})")

    # ---- Public SDK-compatible send helpers ---------------------------
    async def reply(self, frame: dict[str, Any], body: dict[str, Any], cmd: str | None = None) -> dict[str, Any]:
        return await self._send_correlated_request(self._payload_req_id(frame), cmd or APP_CMD_RESPONSE, body)

    async def reply_stream(
        self,
        frame: dict[str, Any],
        stream_id: str,
        content: str,
        finish: bool = False,
        msg_item: list[dict[str, Any]] | None = None,
        feedback: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        stream: dict[str, Any] = {"id": stream_id, "finish": finish, "content": content}
        if finish and msg_item:
            stream["msg_item"] = msg_item
        if feedback:
            stream["feedback"] = feedback
        return await self.reply(frame, {"msgtype": "stream", "stream": stream})

    async def reply_welcome(self, frame: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
        return await self.reply(frame, body, APP_CMD_RESPONSE_WELCOME)

    async def upload_media(self, file_data: bytes, *, type: str, filename: str) -> dict[str, Any]:
        if not file_data:
            raise ValueError("Cannot upload empty media")
        total_chunks = (len(file_data) + UPLOAD_CHUNK_SIZE - 1) // UPLOAD_CHUNK_SIZE
        if total_chunks > MAX_UPLOAD_CHUNKS:
            raise ValueError(f"File too large: {total_chunks} chunks exceeds maximum of {MAX_UPLOAD_CHUNKS}")

        init_response = await self._send_request(
            APP_CMD_UPLOAD_MEDIA_INIT,
            {
                "type": type,
                "filename": filename,
                "total_size": len(file_data),
                "total_chunks": total_chunks,
                "md5": hashlib.md5(file_data).hexdigest(),
            },
        )
        init_body = init_response.get("body") if isinstance(init_response.get("body"), dict) else {}
        upload_id = str(init_body.get("upload_id") or "").strip()
        if not upload_id:
            raise RuntimeError(f"media upload init failed: missing upload_id in response {init_response}")

        for chunk_index, start in enumerate(range(0, len(file_data), UPLOAD_CHUNK_SIZE)):
            chunk = file_data[start : start + UPLOAD_CHUNK_SIZE]
            await self._send_request(
                APP_CMD_UPLOAD_MEDIA_CHUNK,
                {
                    "upload_id": upload_id,
                    "chunk_index": chunk_index,
                    "base64_data": base64.b64encode(chunk).decode("ascii"),
                },
            )

        finish_response = await self._send_request(APP_CMD_UPLOAD_MEDIA_FINISH, {"upload_id": upload_id})
        finish_body = finish_response.get("body") if isinstance(finish_response.get("body"), dict) else {}
        media_id = str(finish_body.get("media_id") or "").strip()
        if not media_id:
            raise RuntimeError(f"media upload finish failed: missing media_id in response {finish_response}")
        return {"type": str(finish_body.get("type") or type), "media_id": media_id, **finish_body}

    async def reply_media(self, frame: dict[str, Any], media_type: str, media_id: str) -> dict[str, Any]:
        return await self.reply(frame, {"msgtype": media_type, media_type: {"media_id": media_id}})

    async def send_media_message(self, chat_id: str, media_type: str, media_id: str) -> dict[str, Any]:
        return await self._send_request(
            APP_CMD_SEND,
            {"chatid": chat_id, "msgtype": media_type, media_type: {"media_id": media_id}},
        )

    # ---- Media download/decrypt ---------------------------------------
    async def download_file(self, url: str, aes_key: str | None = None) -> dict[str, Any]:
        data, headers = await self._download_bytes(url)
        if aes_key:
            data = self._decrypt_file_bytes(data, aes_key)
        return {"buffer": data, "filename": self._filename_from_headers(url, headers)}

    async def _download_bytes(self, url: str) -> tuple[bytes, dict[str, str]]:
        if self._downloader is not None:
            result = self._downloader(url)
            if inspect.isawaitable(result):
                result = await result
            if isinstance(result, dict):
                return bytes(result.get("buffer", b"")), {
                    str(k).lower(): str(v) for k, v in (result.get("headers") or {}).items()
                }
            if isinstance(result, tuple):
                data, headers = result
                return bytes(data), {str(k).lower(): str(v) for k, v in (headers or {}).items()}
            return bytes(result), {}

        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError(f"Unsupported media URL scheme: {parsed.scheme or '<empty>'}")
        import aiohttp  # Lazy import keeps compile/import checks independent of optional deps.

        async with aiohttp.ClientSession(trust_env=True) as session:
            async with session.get(url) as response:
                if response.status >= 400:
                    raise RuntimeError(f"media download failed: HTTP {response.status}")
                headers = {key.lower(): value for key, value in response.headers.items()}
                content_length = headers.get("content-length")
                if content_length and content_length.isdigit() and int(content_length) > MAX_DOWNLOAD_BYTES:
                    raise ValueError("media download exceeds maximum size")
                data = await response.read()
                if len(data) > MAX_DOWNLOAD_BYTES:
                    raise ValueError("media download exceeds maximum size")
                return data, headers

    @staticmethod
    def _decrypt_file_bytes(encrypted_data: bytes, aes_key: str) -> bytes:
        if not encrypted_data:
            raise ValueError("encrypted_data is empty")
        if not aes_key:
            raise ValueError("aes_key is required")
        padded = aes_key + "=" * ((4 - len(aes_key) % 4) % 4)
        key = base64.b64decode(padded)
        if len(key) != 32:
            raise ValueError(f"Invalid WeCom AES key length: expected 32 bytes, got {len(key)}")
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        except ImportError as exc:  # pragma: no cover - depends on selected extra install.
            raise RuntimeError("cryptography is required for WeCom media decryption") from exc

        cipher = Cipher(algorithms.AES(key), modes.CBC(key[:16]))
        decryptor = cipher.decryptor()
        decrypted = decryptor.update(encrypted_data) + decryptor.finalize()
        if not decrypted:
            raise ValueError("decrypted data is empty")
        pad_len = decrypted[-1]
        if pad_len < 1 or pad_len > 16 or pad_len > len(decrypted):
            raise ValueError(f"Invalid PKCS#7 padding value: {pad_len}")
        if any(byte != pad_len for byte in decrypted[-pad_len:]):
            raise ValueError("Invalid PKCS#7 padding: padding bytes mismatch")
        return decrypted[:-pad_len]

    @staticmethod
    def _filename_from_headers(url: str, headers: dict[str, str]) -> str:
        disposition = headers.get("content-disposition", "")
        if disposition:
            message = Message()
            message["content-disposition"] = disposition
            filename = message.get_param("filename", header="content-disposition")
            if filename:
                return os.path.basename(str(filename))
        path_name = os.path.basename(unquote(urlparse(url).path))
        if path_name:
            return path_name
        guessed = mimetypes.guess_extension(headers.get("content-type", "").split(";", 1)[0].strip())
        return f"wecom_media{guessed or ''}"

    def _log(self, level: str, message: str) -> None:
        if self.logger is None:
            return
        method = getattr(self.logger, level, None) or getattr(self.logger, "info", None)
        if method:
            method(message)
