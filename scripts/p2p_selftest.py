import asyncio
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.p2p_link import P2PServer, P2PClient


def main():
    async def _echo_server_start():
        async def handle(r, w):
            while True:
                d = await r.read(65536)
                if not d:
                    break
                w.write(b"echo:" + d)
                await w.drain()
            w.close()
        await asyncio.start_server(handle, "127.0.0.1", 20001)

    se = asyncio.new_event_loop()

    async def _book():
        await _echo_server_start()
        await asyncio.Future()  # keep loop alive

    threading.Thread(target=lambda: (asyncio.set_event_loop(se),
                                     se.run_until_complete(_book())),
                     daemon=True).start()
    time.sleep(0.3)

    # --- Server: shared service, multi-client ---
    server = P2PServer(target_host="127.0.0.1", target_port=20001,
                       signal_port=0)
    server.start()
    time.sleep(0.3)
    code = server.generate_code()
    print("code_len =", len(code))
    print("ip =", __import__("core.p2p_link", fromlist=["local_ip"]).local_ip())

    # --- 2 clients connect concurrently ---
    c1 = P2PClient(listen_host="127.0.0.1", listen_port=20002)
    c2 = P2PClient(listen_host="127.0.0.1", listen_port=20003)
    c1.connect_from_code(code)
    c2_err = None
    try:
        c2.connect_from_code(code)
    except Exception as e:
        c2_err = e
    print("client2 connect exc:", c2_err)

    for _ in range(100):
        if c1.is_connected() and c2.is_connected():
            break
        time.sleep(0.1)

    print("client1 connected:", c1.is_connected())
    print("client2 connected:", c2.is_connected())

    def _t(c):
        async def test():
            r, w = await asyncio.open_connection("127.0.0.1", c)
            w.write(b"ping")
            await w.drain()
            data = await asyncio.wait_for(r.read(64), timeout=8)
            w.close()
            return data == b"echo:ping"
        return asyncio.new_event_loop().run_until_complete(test())

    ok1 = _t(20002)
    ok2 = _t(20003)
    print("client1 data:", "OK" if ok1 else "FAIL")
    print("client2 data:", "OK" if ok2 else "FAIL")

    infos = server.clients_info()
    print("server tracked clients:", len(infos))

    # kick one client via management
    if infos:
        server.disconnect(infos[0]["id"])
        time.sleep(0.2)
        print("server tracked after kick:", len(server.clients_info()))

    server.stop()
    for c in (c1, c2):
        try:
            c.disconnect()
        except Exception:
            pass
    print("RESULT:", "PASS" if (ok1 and ok2 and len(infos) == 2) else "FAIL")


if __name__ == "__main__":
    main()