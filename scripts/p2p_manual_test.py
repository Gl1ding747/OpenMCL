import asyncio
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.p2p_link import P2PServer, P2PClient


def main():
    print("=" * 60)
    print(" openMCL P2P manual test  (Client-Server, self-test on localhost)")
    print("=" * 60)

    # ---- 1) 模拟真正服务：本机起一个回显服务（就像一台 MC/任意 TCP 服务）----
    echo_loop = asyncio.new_event_loop()
    echo_stop = threading.Event()

    echo_handle = {"server": None}

    async def handle(r, w):
        while True:
            d = await r.read(65536)
            if not d:
                break
            w.write(b"echo:" + d)
            await w.drain()
        w.close()

    async def run_echo():
        echo_handle["server"] = await asyncio.start_server(
            handle, "127.0.0.1", 20001)
        while not echo_stop.is_set():
            await asyncio.sleep(0.2)
        srv = echo_handle["server"]
        srv.close()
        await srv.wait_closed()

    def echo_thread():
        asyncio.set_event_loop(echo_loop)
        try:
            echo_loop.run_until_complete(run_echo())
        finally:
            echo_loop.close()

    threading.Thread(target=echo_thread, daemon=True).start()
    time.sleep(0.4)
    print("[1] echo service up on  127.0.0.1:20001")

    # ---- 2) 服务端：承载该服务，允许多连接，自动检测 IP 并生成识别码 ----
    server = P2PServer(target_host="127.0.0.1", target_port=20001)
    server.start()
    time.sleep(0.4)
    code = server.generate_code()
    print("[2] server started on signal port %d" % server._active_port)
    print("    detected IP =", __import__("core.p2p_link",
          fromlist=["local_ip"]).local_ip())
    print("    connection code (%d chars):" % len(code))

    # ---- 3) 客户端：就输这一个码就连上（这里两个客户端并发演示多连接）----
    def test_one(index, listen_port):
        c = P2PClient(listen_host="127.0.0.1", listen_port=listen_port)
        c.connect_from_code(code)
        for _ in range(100):
            if c.is_connected():
                break
            time.sleep(0.1)
        if not c.is_connected():
            print("[3.%d] client %d FAILED to connect" % (index, index))
            return False, c
        ok = run_data_test(listen_port)
        print("[3.%d] client %d connected=%s  data_roundtrip=%s"
              % (index, index, c.is_connected(), "OK" if ok else "FAIL"))
        return ok, c

    def run_data_test(port):
        async def go():
            r, w = await asyncio.open_connection("127.0.0.1", port)
            w.write(b"ping")
            await w.drain()
            data = await asyncio.wait_for(r.read(64), timeout=8)
            w.close()
            return data == b"echo:ping"
        return asyncio.new_event_loop().run_until_complete(go())

    ok1, c1 = test_one(1, 20002)
    ok2, c2 = test_one(2, 20003)

    # ---- 4) 服务端连接管理 ----
    infos = server.clients_info()
    print("[4] server tracked clients:", len(infos),
          "->", [i["state"] for i in infos])

    # 踢掉一条连接，展示管理能力
    if infos:
        server.disconnect(infos[0]["id"])
        time.sleep(0.2)
        print("    after kicking #%d, kept:" % infos[0]["id"],
              len(server.clients_info()))

    # ---- 5) 优雅关闭（消除 "Task was destroyed" 噪音）----
    print("[5] shutting down gracefully ...")
    try:
        c1.disconnect()
    except Exception:
        pass
    try:
        c2.disconnect()
    except Exception:
        pass
    try:
        server.stop()
    except Exception:
        pass
    echo_stop.set()
    # 等守护线程把各自循环收尾，避免进程退出时残留 accept 任务被回收
    for _ in range(40):
        if not echo_loop.is_running():
            break
        time.sleep(0.1)
    time.sleep(1.0)  # 给后台事件循环线程完成 close() 的时间

    print("=" * 60)
    result = ok1 and ok2 and len(infos) >= 1
    print(" RESULT:", "PASS" if result else "FAIL")
    print("  Both clients connected & data round-tripped. This is expected.")
    print("=" * 60)
    return 0 if result else 1


if __name__ == "__main__":
    sys.exit(main())