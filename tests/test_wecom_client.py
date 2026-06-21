import asyncio
import base64
import unittest

from frontends import wecom_client as wc


class FakeWebSocket:
    def __init__(self, client, *, ack=True, error_cmds=None):
        self.client = client
        self.ack = ack
        self.error_cmds = error_cmds or {}
        self.sent = []

    async def send_json(self, frame):
        self.sent.append(frame)
        if not self.ack:
            return
        cmd = frame["cmd"]
        req_id = frame["headers"]["req_id"]
        if cmd in self.error_cmds:
            await self.client._handle_frame({"headers": {"req_id": req_id}, "errcode": 400, "errmsg": self.error_cmds[cmd]})
            return
        body = {}
        if cmd == wc.APP_CMD_UPLOAD_MEDIA_INIT:
            body = {"upload_id": "upload-1"}
        elif cmd == wc.APP_CMD_UPLOAD_MEDIA_FINISH:
            body = {"media_id": "media-1", "type": "image"}
        await self.client._handle_frame({"headers": {"req_id": req_id}, "errcode": 0, "body": body})


class WeComClientTests(unittest.IsolatedAsyncioTestCase):
    def make_client(self, *, ack=True, error_cmds=None, request_timeout=50):
        client = wc.WSClient("bot", "secret", request_timeout=request_timeout)
        client._ws = FakeWebSocket(client, ack=ack, error_cmds=error_cmds)
        return client

    async def test_event_emitter_on_off_and_async_handler(self):
        client = self.make_client()
        seen = []

        def sync_handler(value):
            seen.append(("sync", value))

        async def async_handler(value):
            await asyncio.sleep(0)
            seen.append(("async", value))

        self.assertIs(client.on("evt", sync_handler), client)
        self.assertIs(client.on("evt", async_handler), client)
        client.emit("evt", 1)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertIn(("sync", 1), seen)
        self.assertIn(("async", 1), seen)
        self.assertIs(client.off("evt", sync_handler), client)
        client.emit("evt", 2)
        await asyncio.sleep(0)
        self.assertNotIn(("sync", 2), seen)

    async def test_dispatches_message_event_and_legacy_callback_frames(self):
        client = self.make_client()
        seen = []
        client.on("message", lambda frame: seen.append(("message", frame["body"]["msgtype"])))
        client.on("message.text", lambda frame: seen.append(("text", frame["body"]["text"]["content"])))
        client.on("message.image", lambda frame: seen.append(("image", frame["body"]["msgid"])))
        client.on("message.file", lambda frame: seen.append(("file", frame["body"]["msgid"])))
        client.on("event", lambda frame: seen.append(("event", frame["body"]["event"]["eventtype"])))
        client.on("event.enter_chat", lambda frame: seen.append(("enter", frame["headers"]["req_id"])))
        client.on("event.disconnected_event", lambda frame: seen.append(("server-disconnect", frame["headers"]["req_id"])))
        client.on("disconnected", lambda reason: seen.append(("disconnected", reason)))

        await client._handle_frame({"cmd": wc.APP_CMD_CALLBACK, "headers": {"req_id": "r1"}, "body": {"msgtype": "text", "text": {"content": "hi"}}})
        await client._handle_frame({"cmd": wc.APP_CMD_LEGACY_CALLBACK, "headers": {"req_id": "r2"}, "body": {"msgtype": "image", "msgid": "m2"}})
        await client._handle_frame({"cmd": wc.APP_CMD_CALLBACK, "headers": {"req_id": "r3"}, "body": {"msgtype": "file", "msgid": "m3"}})
        await client._handle_frame({"cmd": wc.APP_CMD_EVENT_CALLBACK, "headers": {"req_id": "r4"}, "body": {"msgtype": "event", "event": {"eventtype": "enter_chat"}}})
        await client._handle_frame({"cmd": wc.APP_CMD_EVENT_CALLBACK, "headers": {"req_id": "r5"}, "body": {"msgtype": "event", "event": {"eventtype": "disconnected_event"}}})

        self.assertIn(("message", "text"), seen)
        self.assertIn(("text", "hi"), seen)
        self.assertIn(("image", "m2"), seen)
        self.assertIn(("file", "m3"), seen)
        self.assertIn(("event", "enter_chat"), seen)
        self.assertIn(("enter", "r4"), seen)
        self.assertIn(("server-disconnect", "r5"), seen)
        self.assertTrue(any(item[0] == "disconnected" for item in seen))

    async def test_reply_stream_welcome_media_and_send_shapes(self):
        client = self.make_client()
        inbound = {"headers": {"req_id": "inbound-1"}, "body": {"msgtype": "text"}}

        await client.reply_stream(inbound, "stream-1", "hello", finish=True)
        await client.reply_welcome(inbound, {"msgtype": "text", "text": {"content": "welcome"}})
        await client.reply_media(inbound, "image", "media-1")
        await client.send_media_message("chat-1", "file", "media-2")

        self.assertEqual(client._ws.sent[0]["cmd"], wc.APP_CMD_RESPONSE)
        self.assertEqual(client._ws.sent[0]["headers"]["req_id"], "inbound-1")
        self.assertEqual(client._ws.sent[0]["body"], {"msgtype": "stream", "stream": {"id": "stream-1", "finish": True, "content": "hello"}})
        self.assertEqual(client._ws.sent[1]["cmd"], wc.APP_CMD_RESPONSE_WELCOME)
        self.assertEqual(client._ws.sent[1]["headers"]["req_id"], "inbound-1")
        self.assertEqual(client._ws.sent[2]["body"], {"msgtype": "image", "image": {"media_id": "media-1"}})
        self.assertEqual(client._ws.sent[3]["cmd"], wc.APP_CMD_SEND)
        self.assertEqual(client._ws.sent[3]["body"], {"chatid": "chat-1", "msgtype": "file", "file": {"media_id": "media-2"}})

    async def test_upload_media_chunks_and_finish_result(self):
        client = self.make_client()
        data = b"a" * (wc.UPLOAD_CHUNK_SIZE + 3)
        result = await client.upload_media(data, type="image", filename="pic.png")

        self.assertEqual(result["media_id"], "media-1")
        init, chunk0, chunk1, finish = client._ws.sent
        self.assertEqual(init["cmd"], wc.APP_CMD_UPLOAD_MEDIA_INIT)
        self.assertEqual(init["body"]["total_chunks"], 2)
        self.assertEqual(init["body"]["md5"], __import__("hashlib").md5(data).hexdigest())
        self.assertEqual(chunk0["cmd"], wc.APP_CMD_UPLOAD_MEDIA_CHUNK)
        self.assertEqual(chunk0["body"]["chunk_index"], 0)
        self.assertEqual(base64.b64decode(chunk0["body"]["base64_data"]), b"a" * wc.UPLOAD_CHUNK_SIZE)
        self.assertEqual(chunk1["body"]["chunk_index"], 1)
        self.assertEqual(base64.b64decode(chunk1["body"]["base64_data"]), b"aaa")
        self.assertEqual(finish["cmd"], wc.APP_CMD_UPLOAD_MEDIA_FINISH)

    async def test_ack_errors_timeouts_and_missing_reply_req_id_raise(self):
        error_client = self.make_client(error_cmds={wc.APP_CMD_SEND: "bad"})
        with self.assertRaisesRegex(RuntimeError, "bad"):
            await error_client.send_media_message("chat", "file", "media")

        timeout_client = self.make_client(ack=False, request_timeout=1)
        with self.assertRaises(asyncio.TimeoutError):
            await timeout_client.send_media_message("chat", "file", "media")

        with self.assertRaisesRegex(ValueError, "headers.req_id"):
            await self.make_client().reply_stream({"headers": {}}, "stream", "content")

    async def test_download_file_uses_decrypt_seam_and_filename_headers(self):
        async def downloader(url):
            return {"buffer": b"encrypted", "headers": {"content-disposition": 'attachment; filename="a.txt"'}}

        client = wc.WSClient("bot", "secret", downloader=downloader)
        original = wc.WSClient._decrypt_file_bytes
        try:
            wc.WSClient._decrypt_file_bytes = staticmethod(lambda data, key: b"plain:" + data + b":" + key.encode())
            result = await client.download_file("https://example.test/file", "key")
        finally:
            wc.WSClient._decrypt_file_bytes = original

        self.assertEqual(result["buffer"], b"plain:encrypted:key")
        self.assertEqual(result["filename"], "a.txt")

    def test_generate_req_id_and_decrypt_validation(self):
        req_id = wc.generate_req_id("stream")
        self.assertTrue(req_id.startswith("stream_"))
        with self.assertRaisesRegex(ValueError, "aes_key"):
            wc.WSClient._decrypt_file_bytes(b"data", "")

    def test_decrypt_file_bytes_with_known_vector_when_crypto_extra_available(self):
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        except ImportError:
            self.skipTest("cryptography extra is not installed in this interpreter")

        key = b"0123456789abcdef0123456789abcdef"
        plain = b"hello wecom"
        pad_len = 16 - (len(plain) % 16)
        padded = plain + bytes([pad_len]) * pad_len
        cipher = Cipher(algorithms.AES(key), modes.CBC(key[:16]))
        encryptor = cipher.encryptor()
        encrypted = encryptor.update(padded) + encryptor.finalize()
        aes_key = base64.b64encode(key).decode("ascii").rstrip("=")

        self.assertEqual(wc.WSClient._decrypt_file_bytes(encrypted, aes_key), plain)

    def test_decrypt_file_bytes_rejects_padding_over_aes_block_when_crypto_extra_available(self):
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        except ImportError:
            self.skipTest("cryptography extra is not installed in this interpreter")

        key = b"0123456789abcdef0123456789abcdef"
        invalid_plain = b"x" * 15 + bytes([17]) * 17
        cipher = Cipher(algorithms.AES(key), modes.CBC(key[:16]))
        encryptor = cipher.encryptor()
        encrypted = encryptor.update(invalid_plain) + encryptor.finalize()
        aes_key = base64.b64encode(key).decode("ascii").rstrip("=")

        with self.assertRaisesRegex(ValueError, "padding value"):
            wc.WSClient._decrypt_file_bytes(encrypted, aes_key)


if __name__ == "__main__":
    unittest.main()
