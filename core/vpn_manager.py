import os
import json
import socket
import random
import string
import subprocess
import platform

VPN_CONFIG = os.path.join(os.path.expanduser("~"), ".openmcl", "vpn_servers.json")
VPN_SERVER_CONF = os.path.join(os.path.expanduser("~"), ".openmcl", "vpn_server.json")

DEFAULT_SERVERS = []


def _ensure_config():
    os.makedirs(os.path.dirname(VPN_CONFIG), exist_ok=True)
    if not os.path.exists(VPN_CONFIG):
        with open(VPN_CONFIG, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_SERVERS, f, indent=2, ensure_ascii=False)


def load_servers():
    _ensure_config()
    try:
        with open(VPN_CONFIG, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return list(DEFAULT_SERVERS)


def save_servers(servers):
    os.makedirs(os.path.dirname(VPN_CONFIG), exist_ok=True)
    with open(VPN_CONFIG, "w", encoding="utf-8") as f:
        json.dump(servers, f, indent=2, ensure_ascii=False)


def load_server_conf():
    if os.path.exists(VPN_SERVER_CONF):
        try:
            with open(VPN_SERVER_CONF, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_server_conf(conf):
    os.makedirs(os.path.dirname(VPN_SERVER_CONF), exist_ok=True)
    with open(VPN_SERVER_CONF, "w", encoding="utf-8") as f:
        json.dump(conf, f, indent=2, ensure_ascii=False)


def generate_psk(length=32):
    chars = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
    return "".join(random.choice(chars) for _ in range(length))


def _run_ps(script, timeout=30):
    try:
        flags = 0x08000000 if os.name == "nt" else 0
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=timeout, creationflags=flags)
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    except subprocess.TimeoutExpired:
        return "", "timeout", 1
    except Exception as e:
        return "", str(e), 1


def _run_cmd(cmd, timeout=30):
    try:
        flags = 0x08000000 if os.name == "nt" else 0
        r = subprocess.run(cmd, shell=True, capture_output=True,
                           text=True, timeout=timeout, creationflags=flags)
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    except Exception as e:
        return "", str(e), 1


def check_platform():
    return platform.system().lower() == "windows"


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def get_public_ip():
    try:
        import urllib.request
        resp = urllib.request.urlopen("https://api.ipify.org", timeout=5)
        return resp.read().decode().strip()
    except Exception:
        try:
            resp = urllib.request.urlopen("https://icanhazip.com", timeout=5)
            return resp.read().decode().strip()
        except Exception:
            return ""


def _conn_name(server_name):
    return f"openMCL_{server_name}"


SERVER_NAME = "openMCL_Server"


def vpn_exists(server_name):
    cn = _conn_name(server_name)
    ps = f'Get-VpnConnection -Name "{cn}" -ErrorAction SilentlyContinue | Select -ExpandProperty Name'
    stdout, _, _ = _run_ps(ps)
    return stdout.strip() == cn


def incoming_vpn_exists():
    ps = f'Get-VpnConnection -Name "{SERVER_NAME}" -ErrorAction SilentlyContinue | Select -ExpandProperty Name'
    stdout, _, _ = _run_ps(ps)
    return stdout.strip() == SERVER_NAME


def local_user_exists(username):
    ps = f'$u = Get-LocalUser -Name "{username}" -ErrorAction SilentlyContinue; if ($u) {{ "yes" }}'
    stdout, _, _ = _run_ps(ps)
    return stdout.strip() == "yes"


def create_local_user(username, password):
    if local_user_exists(username):
        _run_cmd(f'net user "{username}" /delete')
    stdout, stderr, rc = _run_cmd(f'net user "{username}" "{password}" /add /fullname:"openMCL VPN" /passwordchg:no')
    if rc != 0:
        err = stderr or stdout
        if "access" in err.lower() or "denied" in err.lower() or "5" in err:
            return False, "需要管理员权限"
        return False, f"创建用户失败: {err}"
    _run_cmd(f'net localgroup "Users" "{username}" /delete')
    return True, ""


def add_user_to_vpn_group(username):
    ps = (
        f'$user = [ADSI]"WinNT://./{username},user"; '
        f'$user.Put("UserFlags", 66177); '
        f'$user.SetInfo()'
    )
    _, stderr, rc = _run_ps(ps)
    if rc != 0:
        return False, f"权限配置失败: {stderr}"
    return True, ""


USERNAME = "openmclvpn"


def start_vpn_server(psk="", ip_range="10.0.0.100-10.0.0.200"):
    if not check_platform():
        return False, "Windows only"

    if not psk:
        psk = generate_psk()

    if incoming_vpn_exists():
        _run_ps(f'Remove-VpnConnection -Name "{SERVER_NAME}" -Force')

    local_ip = get_local_ip()

    fw_rules = [
        'netsh advfirewall firewall add rule name="openMCL IKE" dir=in action=allow protocol=udp localport=500',
        'netsh advfirewall firewall add rule name="openMCL NAT-T" dir=in action=allow protocol=udp localport=4500',
        'netsh advfirewall firewall add rule name="openMCL L2TP" dir=in action=allow protocol=udp localport=1701',
        'netsh advfirewall firewall add rule name="openMCL ESP" dir=in action=allow protocol=50',
    ]
    for rule in fw_rules:
        _run_cmd(rule)

    conf = {
        "psk": psk,
        "local_ip": local_ip,
        "ip_range": ip_range,
        "running": True,
    }
    save_server_conf(conf)

    public_ip = get_public_ip()
    return True, {"psk": psk, "local_ip": local_ip, "public_ip": public_ip}


def stop_vpn_server():
    if incoming_vpn_exists():
        _run_ps(f'Remove-VpnConnection -Name "{SERVER_NAME}" -Force')

    fw_rules = [
        'netsh advfirewall firewall delete rule name="openMCL IKE"',
        'netsh advfirewall firewall delete rule name="openMCL NAT-T"',
        'netsh advfirewall firewall delete rule name="openMCL L2TP"',
        'netsh advfirewall firewall delete rule name="openMCL ESP"',
    ]
    for rule in fw_rules:
        _run_cmd(rule)

    if os.path.exists(VPN_SERVER_CONF):
        os.remove(VPN_SERVER_CONF)
    return True, "Server stopped"


def get_server_status():
    conf = load_server_conf()
    if not conf.get("running"):
        return {"running": False}
    return {
        "running": True,
        "psk": conf.get("psk", ""),
        "local_ip": conf.get("local_ip", ""),
        "public_ip": get_public_ip(),
    }


def create_client_vpn(server_name, server_address, psk):
    if not check_platform():
        return False, "Windows only"

    cn = _conn_name(server_name)
    if vpn_exists(server_name):
        _run_ps(f'Remove-VpnConnection -Name "{cn}" -Force')
    safe_psk = psk.replace('"', '`"')
    ps = (
        f'Add-VpnConnection -Name "{cn}" -ServerAddress "{server_address}" '
        f'-TunnelType L2tp -L2tpPsk "{safe_psk}" '
        f'-AuthenticationMethod MSChapv2 -EncryptionLevel Required -Force -RememberCredential'
    )
    _, stderr, rc = _run_ps(ps)
    if rc != 0:
        return False, f"VPN creation failed: {stderr}"
    return True, ""


def remove_client_vpn(server_name):
    cn = _conn_name(server_name)
    if not vpn_exists(server_name):
        return True, ""
    _run_ps(f'Remove-VpnConnection -Name "{cn}" -Force')
    return True, ""


def connect_vpn(server_name, username="openmclvpn", password=""):
    cn = _conn_name(server_name)
    if not vpn_exists(server_name):
        return False, "VPN connection not found. Please add it first."
    cmd = f'rasdial "{cn}" "{username}" "{password}"'
    try:
        flags = 0x08000000 if os.name == "nt" else 0
        r = subprocess.run(cmd, shell=True, capture_output=True,
                           text=True, timeout=30, creationflags=flags)
        if r.returncode == 0:
            return True, "Connected"
        out = r.stdout + r.stderr
        if "691" in out:
            return False, "Auth failed: PSK mismatch"
        if "623" in out:
            return False, "Server not found"
        if "789" in out:
            return False, "L2TP failed: check PSK and firewall"
        if "809" in out:
            return False, "NAT/router blocking L2TP (UDP 500, 4500, 1701)"
        lines = [l.strip() for l in out.split("\n") if l.strip()]
        return False, lines[-1] if lines else out.strip()
    except Exception as e:
        return False, str(e)


def disconnect_vpn(server_name):
    cn = _conn_name(server_name)
    cmd = f'rasdial "{cn}" /disconnect'
    try:
        flags = 0x08000000 if os.name == "nt" else 0
        subprocess.run(cmd, shell=True, capture_output=True,
                       text=True, timeout=15, creationflags=flags)
        return True, "Disconnected"
    except Exception as e:
        return False, str(e)


def get_connection_status(server_name):
    cn = _conn_name(server_name)
    ps = (
        f'$c = Get-VpnConnection -Name "{cn}" -ErrorAction SilentlyContinue; '
        f'if ($c) {{ $c.ConnectionStatus }} else {{ "NotFound" }}'
    )
    stdout, _, _ = _run_ps(ps)
    return stdout.strip() if stdout.strip() else "NotFound"


def get_vpn_ip(server_name):
    cn = _conn_name(server_name)
    ps = (
        f'$a = Get-NetAdapter | ? {{ $_.Name -like "*{cn}*" }} | Select -First 1; '
        f'if ($a) {{ (Get-NetIPAddress -AddressFamily IPv4 '
        f'-InterfaceIndex $a.InterfaceIndex -EA 0 | Select -First 1).IPAddress }}'
    )
    stdout, _, _ = _run_ps(ps)
    return stdout.strip()