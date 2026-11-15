"""
基于 aiortc / WebRTC 的 TCP-over-DataChannel 组网隧道（纯用户态、无需驱动）。

使用形态（Client-Server 模型）：
  - Server（服务端）：承载真正的 TCP 服务，允许多个 Client 同时连接。
    启动后自动检测本机 IP，生成一个识别码（包含 IP + 信令端口 + 会话 token），
    供 Client 输入连接，并统一管理各条连接。
  - Client（客户端）：输入服务端给出的识别码即自动完成握手并连接。

信令：识别码里的 IP/端口直连做一次 TCP 信令交换（代码内部完成 answer 回传），
数据则走 WebRTC DataChannel 直连，无需公网信令服务器。
  Client: connect_from_code(code) -> 解析 IP/端口/token
         -> 生成 offer 发给 Server -> 收 answer 完成握手 -> 本地监听/转发
  Server: 收到任一 Client 的 offer -> 为之建独立对等连接 -> 回送 answer -> 转发到后端服务

协议：单条 DataChannel 上做多路复用，每包为 8 字节头部(id, len) + 载荷，支持多条 TCP。
"""

import asyncio
import concurrent.futures
import json
import secrets
import socket
import struct
import threading
import zlib

from aiortc import (RTCConfiguration, RTCIceCandidate, RTCIceServer,
                    RTCPeerConnection, RTCSessionDescription)

_DEV = "data"

_ICE_SERVERS = RTCConfiguration(iceServers=[
    RTCIceServer(urls=["stun:stun.l.google.com:19302",
                       "stun:stun1.l.google.com:19302"]),
])

_HEADER = struct.Struct(">II")  # conn_id, payload len
_MAX_CHUNK = 16384

_MAGIC = b"OMCLTOK"
_VER = b"\x01"

# 码字符集：大写 + 小写 + 数字 + 常见符号（无空格、无引号、无反斜杠，粘贴安全）。
# 仅本应用能识别（前缀 + magic + 长度校验 + 解压）。
_CHARSET = ("ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            "abcdefghijklmnopqrstuvwxyz"
            "0123456789"
            "!#$%&()*+-./:;<=>?@[]^_")
_BASE = len(_CHARSET)  # 87


def _to_chars(n):
    digs = []
    while n > 0:
        n, r = divmod(n, _BASE)
        digs.append(_CHARSET[r])
    return ''.join(reversed(digs)) or _CHARSET[0]


def _from_chars(s):
    n = 0
    for ch in s:
        n = n * _BASE + _CHARSET.index(ch)
    return n


def make_code(sdp):
    """将 SDP 打包成一个只有本应用能识别的连接码字符串。"""
    return _blob_to_code(sdp.encode("utf-8"))


def read_code(code):
    """解析 SDP 连接码；非本应用码 / 校验失败返回 None。"""
    blob = _code_to_blob(code)
    if blob is None:
        return None
    return blob.decode("utf-8")


def make_meta(payload):
    """将字典（IP/端口/token 等）打包成连接码字符串。"""
    return _blob_to_code(json.dumps(payload, separators=(",", ":")).encode("utf-8"))


def read_meta(code):
    """解析元信息连接码；非本应用码 / 校验失败返回 None。"""
    blob = _code_to_blob(code)
    if blob is None:
        return None
    try:
        return json.loads(blob.decode("utf-8"))
    except Exception:
        return None


def _blob_to_code(blob_bytes):
    payload = zlib.compress(blob_bytes, 9)
    blob = _MAGIC + _VER + struct.pack(">I", len(payload)) + payload
    return "OMCL." + _to_chars(int.from_bytes(blob, "big"))


def _code_to_blob(code):
    s = (code or "").strip()
    if not s.startswith("OMCL."):
        return None
    try:
        n = _from_chars(s.split('.', 1)[1].strip())
    except Exception:
        return None
    try:
        data = bytearray()
        while n > 0:
            n, r = divmod(n, 256)
            data.append(r)
        data.reverse()
        blob = bytes(data)
        if not blob.startswith(_MAGIC + _VER):
            return None
        off = len(_MAGIC) + len(_VER)
        plen = struct.unpack(">I", blob[off:off + 4])[0]
        payload = blob[off + 4: off + 4 + plen]
        if len(payload) != plen:
            return None
        return zlib.decompress(payload)
    except Exception:
        return None


def local_ip():
    """自动检测本机在内网的 IPv4 地址，失败则回退到回环地址。"""
    try:
        hostname = socket.gethostname()
        for ip in socket.gethostbyname_ex(hostname)[2]:
            if not ip.startswith("127.") and ip.count(".") == 3:
                return ip
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
        finally:
            sock.close()
    except Exception:
        pass
    return "127.0.0.1"


class P2PTunnel:
    """单端 P2P 隧道封装。role 为 'provider' 或 'consumer'。"""

    def __init__(self, role, target_host="127.0.0.1", target_port=25565,
                 listen_host="127.0.0.1", listen_port=25565, on_state=None):
        self.role = role
        self.target_host = target_host
        self.target_port = target_port
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.on_state = on_state
        self._emit("init", role=role)

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

        self.pc = None
        self.channel = None
        self._server = None
        self._writer = None
        self._next_id = 0
        # 映射：conn_id -> (asyncio.StreamReader, asyncio.StreamWriter)
        self._conns = {}
        # 单条数据通道收包时，为每个 conn_id 缓冲不完整载荷
        self._buffers = {}
        self._payload_len = {}
        self._closed = False

    # ---------------------------------------------------------------- 线程/状态
    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
        finally:
            # 关闭事件循环，清理 Windows Proactor 内部残留的 accept 任务
            try:
                self._loop.close()
            except Exception:
                pass

    def _emit(self, state, **kw):
        if self.on_state:
            try:
                self.on_state(state, **kw)
            except Exception:
                pass

    def _submit(self, coro, timeout=20):
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout)

    # ---------------------------------------------------------------- 生命周期
    def create(self):
        """在独立事件循环线程里构建 RTCPeerConnection。"""
        async def _create():
            self.pc = RTCPeerConnection(configuration=_ICE_SERVERS)
            if self.role == "consumer":
                # 必须先建数据通道，SCTP 才会出现在 Offer 里
                self.channel = self.pc.createDataChannel(_DEV)
                self.channel.on("open", lambda: self._emit("channel_open"))
                self.channel.on("message", self._on_channel_data)
            self.pc.on("datachannel", self._on_datachannel)
            self._emit("created")
        self._submit(_create())

    def generate_offer(self):
        """Consumer：创建并导出 Offer SDP。"""
        if self.role != "consumer":
            raise RuntimeError("generate_offer 仅用于 consumer")

        async def _offer():
            offer = await self.pc.createOffer()
            await self.pc.setLocalDescription(offer)
            await self._wait_ice_complete()
            return self.pc.localDescription.sdp
        sdp = self._submit(_offer())
        self._emit("offer_ready")
        return sdp

    def receive_offer(self, sdp):
        """Provider：载入对方 Offer。"""
        async def _recv():
            desc = RTCSessionDescription(sdp=sdp, type="offer")
            await self.pc.setRemoteDescription(desc)
            self._emit("offer_received")
        self._submit(_recv())

    def generate_answer(self):
        """Provider：生成并导出 Answer SDP。"""
        if self.role != "provider":
            raise RuntimeError("generate_answer 仅用于 provider")

        async def _answer():
            answer = await self.pc.createAnswer()
            await self.pc.setLocalDescription(answer)
            await self._wait_ice_complete()
            return self.pc.localDescription.sdp
        sdp = self._submit(_answer())
        self._emit("answer_ready")
        return sdp

    def receive_answer(self, sdp):
        """Consumer：载入对方 Answer。"""
        async def _recv():
            desc = RTCSessionDescription(sdp=sdp, type="answer")
            await self.pc.setRemoteDescription(desc)
            self._emit("answer_received")
        self._submit(_recv())

    def start_listening(self):
        """Consumer：在本地监听端口，接受本地程序连接并转发。"""
        if self.role != "consumer":
            raise RuntimeError("start_listening 仅用于 consumer")

        async def _listen():
            self._server = await asyncio.start_server(
                self._ingress, self.listen_host, self.listen_port)
            self._emit("listening", port=self.listen_port)
        self._submit(_listen())
        return self.listen_port

    async def _wait_ice_complete(self):
        loop = asyncio.get_event_loop()
        fut = loop.create_future()

        def _check():
            if hasattr(self.pc, "iceGatheringState") and self.pc.iceGatheringState == "complete" and not fut.done():
                fut.set_result(None)
        self.pc.on("icegatheringstatechange", _check)
        _check()
        try:
            await asyncio.wait_for(fut, timeout=15)
        except asyncio.TimeoutError:
            pass

    # ---------------------------------------------------------------- 数据通道
    def _on_datachannel(self, channel):
        if channel.label != _DEV or self.channel is not None:
            return
        self.channel = channel
        self.channel.on("open", lambda: self._emit("channel_open"))
        self.channel.on("message", self._on_channel_data)

    def _ingress(self, reader, writer):
        """Consumer：一个本地 TCP 连接，为其分配 conn_id 并双向往返转发。"""
        conn_id = self._next_id
        self._next_id += 1
        self._conns[conn_id] = (reader, writer)

        async def pump_local():
            try:
                while True:
                    data = await reader.read(_MAX_CHUNK)
                    if not data:
                        break
                    self._send_frame(conn_id, data)
            except Exception:
                pass
            finally:
                self._close_conn(conn_id)

        asyncio.ensure_future(pump_local())

    def _send_frame(self, conn_id, data):
        if self.channel is None or self.channel.readyState != "open":
            return False
        try:
            self.channel.send(_HEADER.pack(conn_id, len(data)) + bytes(data))
            return True
        except Exception:
            return False

    def _on_channel_data(self, message):
        if isinstance(message, str):
            message = message.encode("utf-8", errors="replace")
        buf = self._buffers.setdefault(-1, b"") + bytes(message)
        self._buffers[-1] = buf
        while len(buf) >= _HEADER.size:
            conn_id, plen = _HEADER.unpack_from(buf, 0)
            need = _HEADER.size + plen
            if len(buf) < need:
                break
            payload = buf[_HEADER.size:need]
            buf = buf[need:]
            self._handle_payload(conn_id, payload)
        self._buffers[-1] = buf

    def _handle_payload(self, conn_id, payload):
        if self.role == "consumer":
            c = self._conns.get(conn_id)
            if c:
                _, writer = c
                async def _write():
                    try:
                        writer.write(payload)
                        await writer.drain()
                    except Exception:
                        self._close_conn(conn_id)
                asyncio.ensure_future(_write())
        else:  # provider
            self._handle_provider_payload(conn_id, payload)

    def _handle_provider_payload(self, conn_id, payload):
        def _pump(reader, writer):
            async def loop():
                try:
                    while True:
                        data = await reader.read(_MAX_CHUNK)
                        if not data:
                            break
                        self._send_frame(conn_id, data)
                except Exception:
                    pass
                finally:
                    writer.close()
                    self._conns.pop(conn_id, None)
            asyncio.ensure_future(loop())

        c = self._conns.get(conn_id)
        if c is None:
            async def _connect():
                try:
                    reader, writer = await asyncio.open_connection(
                        self.target_host, self.target_port)
                except Exception as e:
                    self._emit("target_error", error=str(e))
                    return
                self._conns[conn_id] = (reader, writer)
                _pump(reader, writer)
                writer.write(payload)
                await writer.drain()
            asyncio.ensure_future(_connect())
        else:
            _, writer = c
            async def _w():
                try:
                    writer.write(payload)
                    await writer.drain()
                except Exception:
                    self._close_conn(conn_id)
            asyncio.ensure_future(_w())

    def _close_conn(self, conn_id):
        c = self._conns.pop(conn_id, None)
        if c:
            _, writer = c
            try:
                writer.close()
            except Exception:
                pass

    # ---------------------------------------------------------------- 关闭
    def close(self):
        if self._closed:
            return
        self._closed = True

        async def _close():
            try:
                if self._server is not None:
                    self._server.close()
                    try:
                        await self._server.wait_closed()
                    except Exception:
                        pass
                for conn_id in list(self._conns.keys()):
                    self._close_conn(conn_id)
                if self.pc is not None:
                    try:
                        await self.pc.close()
                    except Exception:
                        pass
            finally:
                # 优雅取消本事件循环中残留的后台任务，避免退出时
                # asyncio 抛出 "Task was destroyed but it is pending!" 噪音。
                pending = [t for t in asyncio.all_tasks(self._loop)
                           if t is not asyncio.current_task()]
                for t in pending:
                    t.cancel()
                if pending:
                    try:
                        await asyncio.gather(*pending, return_exceptions=True)
                    except Exception:
                        pass
                self._loop.stop()
                self._emit("closed")
        try:
            asyncio.run_coroutine_threadsafe(_close(), self._loop)
        except Exception:
            pass
        self._thread.join(timeout=3)

    @staticmethod
    def candidate_from_sdp(sdp, candidate_line):
        """极简：将 remote sdp 中的候选解析导入（aiortc 自动处理大多数），默认无需调用。"""
        return RTCIceCandidate(
            foundation="x", component=1,
            protocol="udp", port=int(sdp.split()[-1]),
            ip=candidate_line.split()[4],
            type="host", priority=0, generation=0,
        )


class P2PServer:
    """服务端：统一承载被分段的服务，允许多个客户端并发连接并集中管理。

    启动后起一个内置 TCP 信令服务；生成识别码（含本机 IP + 信令端口 + token）。
    每个客户端连入信令端口时，创建一个独立对等连接并回送 answer，然后转发到
    target 服务。可列举各连接、单独断开。
    """

    _SIG = b"OMSIG/v1\n"

    def __init__(self, target_host="127.0.0.1", target_port=25565,
                 signal_port=0, on_state=None, on_clients=None):
        self.target_host = target_host
        self.target_port = target_port
        self.signal_port = signal_port or 0
        self.on_state = on_state
        self.on_clients = on_clients
        self._token = secrets.token_hex(5)
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=16)

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

        self._signal = None
        self._active_port = None
        self._tunnels = {}   # cid -> P2PTunnel(provider)
        self._conn_info = {}  # cid -> peer 信息
        self._next = 0
        self._closed = False

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
        finally:
            # 关闭事件循环，清理 Windows Proactor 内部残留的 accept 任务
            try:
                self._loop.close()
            except Exception:
                pass

    def _emit(self, state, **kw):
        if self.on_state:
            try:
                self.on_state(state, **kw)
            except Exception:
                pass

    def _notify_clients(self):
        if self.on_clients:
            try:
                self.on_clients(self.clients_info())
            except Exception:
                pass

    def _submit(self, coro, timeout=20):
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout)

    # ------------------------------------------------------------ 启停
    def start(self):
        async def _start():
            self._signal = await asyncio.start_server(
                self._on_signal, "0.0.0.0", self.signal_port)
            sip = self._signal.sockets[0]
            self._active_port = sip.getsockname()[1]
            self._emit("started", port=self._active_port)
        self._submit(_start())

    def run_bg(self):
        """在后台线程中启动（与 GUI 线程解耦），返回可用端口。"""
        self.start()
        return self._active_port

    def token(self):
        return self._token

    # ------------------------------------------------------------ 识别码
    def generate_code(self):
        return make_meta({
            "ver": 1,
            "kind": "P2P",
            "ip": local_ip(),
            "port": self._active_port,
            "token": self._token,
            "name": "openMCL",
        })

    # ------------------------------------------------------------ 信令
    def _on_signal(self, reader, writer):
        loop = asyncio.get_event_loop()
        loop.create_task(self._handle_signal(reader, writer))

    async def _handle_signal(self, reader, writer):
        try:
            head = await asyncio.wait_for(reader.readexactly(len(self._SIG)), 20)
            if head != self._SIG:
                return
            plen = struct.unpack(">I", await asyncio.wait_for(
                reader.readexactly(4), 20))[0]
            if plen <= 0 or plen > 1_000_000:
                return
            body = await asyncio.wait_for(reader.readexactly(plen), 20)
            nl = body.find(b"\n")
            if nl < 0:
                return
            token_line, offer = body[:nl].decode("utf-8"), body[nl + 1:]
            if token_line != self._token:
                writer.write(b"ERR\nTOKEN")  # 拒绝外部/错误 token
                await writer.drain()
                return
            loop = asyncio.get_event_loop()
            answer = await loop.run_in_executor(
                self._pool, self._build_peer, offer.decode("utf-8"))
            if answer is None:
                writer.write(b"ERR\nHANDSHAKE")
                await writer.drain()
                return
            out = answer.encode("utf-8")
            writer.write(struct.pack(">I", len(out)) + out)
            await writer.drain()
        except Exception:
            try:
                writer.write(b"ERR\nTIMEOUT")
                await writer.drain()
            except Exception:
                pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    def _build_peer(self, offer_sdp):
        try:
            t = P2PTunnel(role="provider",
                          target_host=self.target_host,
                          target_port=self.target_port,
                          on_state=lambda s, **k: self._tunnel_state(t, s, **k))
            t.create()
            t.receive_offer(offer_sdp)
            answer = t.generate_answer()
            cid = self._next
            self._next += 1
            self._tunnels[cid] = t
            self._conn_info[cid] = {
                "id": cid,
                "state": "handshake",
            }
            self._notify_clients()
            return answer
        except Exception:
            return None

    def _tunnel_state(self, tunnel, state, **kw):
        cid = next((c for c, t in self._tunnels.items() if t is tunnel), None)
        if cid is not None and state in ("channel_open", "target_error", "closed"):
            if state == "channel_open":
                self._conn_info[cid]["state"] = "connected"
            elif state == "target_error":
                self._conn_info[cid]["state"] = "target_error"
            elif state == "closed":
                self._conn_info[cid]["state"] = "closed"
            self._notify_clients()
            self._emit(state, id=cid)

    # ------------------------------------------------------------ 管理
    def clients_info(self):
        out = []
        for cid, t in self._tunnels.items():
            st = self._conn_info.get(cid, {}).get("state", "connecting")
            if t.channel is not None and t.channel.readyState == "open":
                st = "connected"
            elif t.pc is not None and t.pc.connectionState == "failed":
                st = "failed"
            out.append({"id": cid, "state": st})
        return out

    def disconnect(self, cid):
        t = self._tunnels.pop(cid, None)
        self._conn_info.pop(cid, None)
        if t is not None:
            try:
                t.close()
            except Exception:
                pass
        self._notify_clients()

    def stop(self):
        if self._closed:
            return
        self._closed = True
        for cid in list(self._tunnels.keys()):
            t = self._tunnels.pop(cid, None)
            if t is not None:
                try:
                    t.close()
                except Exception:
                    pass

        async def _stop():
            if self._signal is not None:
                self._signal.close()
                try:
                    await self._signal.wait_closed()
                except Exception:
                    pass
            self._loop.stop()
            self._emit("stopped")
        try:
            asyncio.run_coroutine_threadsafe(_stop(), self._loop)
        except Exception:
            pass
        try:
            self._pool.shutdown(wait=False)
        except Exception:
            pass
        self._thread.join(timeout=3)


class P2PClient:
    """客户端：输入服务端识别码即自动完成握手并连接。

    内部流程：解析码里的 IP/端口/token -> 建立本地监听 -> 生成 offer
    -> 经信令端口发回 server -> 收 answer -> 完成对等连接 -> 开始转发。
    """

    def __init__(self, listen_host="127.0.0.1", listen_port=25566,
                 on_state=None):
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.on_state = on_state
        self._SIG = P2PServer._SIG
        self._meta = None

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

        self._tunnel = None
        self._closed = False

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _emit(self, state, **kw):
        if self.on_state:
            try:
                self.on_state(state, **kw)
            except Exception:
                pass

    def _submit(self, coro, timeout=30):
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout)

    def is_connected(self):
        return (self._tunnel is not None
                and self._tunnel.channel is not None
                and self._tunnel.channel.readyState == "open")

    def connect_from_code(self, code):
        meta = read_meta(code)
        if meta is None or meta.get("kind") != "P2P":
            raise ValueError("code_not_recognized")
        ip = meta.get("ip")
        port = int(meta.get("port", 0))
        token = meta.get("token", "")
        if not ip or not port or not token:
            raise ValueError("code_not_recognized")

        async def _do():
            t = P2PTunnel(role="consumer",
                          listen_host=self.listen_host,
                          listen_port=self.listen_port,
                          on_state=lambda s, **k: self._tunnel_state(s, **k))
            t.create()
            offer = t.generate_offer()
            reader, writer = await asyncio.open_connection(ip, port)
            writer.write(self._SIG)
            payload = (token + "\n" + offer).encode("utf-8")
            writer.write(struct.pack(">I", len(payload)) + payload)
            await writer.drain()
            resp = await asyncio.wait_for(reader.readexactly(4), 25)
            alen = struct.unpack(">I", resp)[0]
            answer = await asyncio.wait_for(reader.readexactly(alen), 25)
            writer.close()
            if answer.startswith(b"ERR\n"):
                raise RuntimeError(answer[4:].decode("utf-8", "replace"))
            t.receive_answer(answer.decode("utf-8"))
            t.start_listening()
            self._tunnel = t
            self._meta = meta
            self._emit("connected", ip=ip, port=port)
        self._submit(_do(), timeout=60)

    def disconnect(self):
        if self._tunnel is not None:
            try:
                self._tunnel.close()
            except Exception:
                pass
        self._tunnel = None
        self._meta = None
        self._emit("disconnected")

    def _tunnel_state(self, state, **kw):
        if self._tunnel is None:
            return
        if state == "channel_open":
            self._emit("channel_open")
        elif state == "target_error":
            self._emit("target_error", **kw)

    def _unload(self):
        self.disconnect()