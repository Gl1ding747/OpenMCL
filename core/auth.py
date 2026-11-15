import json
import os
import time
import uuid
import webbrowser
import urllib.parse

import requests

MICROSOFT_CLIENT_ID = "00000000402b5328"
MICROSOFT_REDIRECT_URI = "https://login.live.com/oauth20_desktop.srf"


class AuthResult:
    def __init__(self, username, uuid_str, access_token, user_type="msa", api_root=""):
        self.username = username
        self.uuid = uuid_str
        self.access_token = access_token
        self.user_type = user_type
        self.api_root = api_root

    def to_dict(self):
        return {
            "username": self.username,
            "uuid": self.uuid,
            "access_token": self.access_token,
            "user_type": self.user_type,
            "api_root": self.api_root,
        }

    @staticmethod
    def from_dict(d):
        return AuthResult(
            username=d.get("username", ""),
            uuid_str=d.get("uuid", ""),
            access_token=d.get("access_token", ""),
            user_type=d.get("user_type", "msa"),
            api_root=d.get("api_root", ""),
        )


class AccountManager:
    def __init__(self, minecraft_dir=None):
        if minecraft_dir is None:
            minecraft_dir = os.path.join(os.path.expanduser("~"), ".openmcl")
        self.minecraft_dir = minecraft_dir
        self.accounts_file = os.path.join(minecraft_dir, "accounts.json")
        self.current_account = None
        os.makedirs(minecraft_dir, exist_ok=True)

    def load_accounts(self):
        if not os.path.exists(self.accounts_file):
            return []
        with open(self.accounts_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [AuthResult.from_dict(d) for d in data.get("accounts", [])]

    def save_accounts(self, accounts):
        data = {
            "accounts": [a.to_dict() for a in accounts],
        }
        with open(self.accounts_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def add_offline_account(self, username):
        offline_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"offline:{username}"))
        account = AuthResult(
            username=username,
            uuid_str=offline_uuid,
            access_token="0",
            user_type="offline",
        )
        accounts = self.load_accounts()
        accounts = [a for a in accounts if a.username != username]
        accounts.append(account)
        self.save_accounts(accounts)
        self.current_account = account
        return account

    def login_microsoft(self, callback=None):
        auth_url = (
            "https://login.live.com/oauth20_authorize.srf?"
            + urllib.parse.urlencode({
                "client_id": MICROSOFT_CLIENT_ID,
                "response_type": "code",
                "redirect_uri": MICROSOFT_REDIRECT_URI,
                "scope": "XboxLive.signin offline_access",
                "display": "touch",
            })
        )
        msg = (
            "A browser window will open.\n"
            "After signing in, you will see a code.\n"
            "Copy the code and paste it below."
        )
        if callback:
            callback("message", msg)

        webbrowser.open(auth_url)
        code = callback("input", "Enter the code from your browser:")

        if not code or not code.strip():
            raise Exception("Login cancelled")

        return self._complete_microsoft_auth(code.strip())

    def _complete_microsoft_auth(self, auth_code):
        token_url = "https://login.live.com/oauth20_token.srf"
        token_data = {
            "client_id": MICROSOFT_CLIENT_ID,
            "code": auth_code,
            "redirect_uri": MICROSOFT_REDIRECT_URI,
            "grant_type": "authorization_code",
        }
        resp = requests.post(token_url, data=token_data, timeout=30)
        resp.raise_for_status()
        token_json = resp.json()
        ms_access_token = token_json["access_token"]

        xbl_result = self._xbox_live_auth(ms_access_token)
        xsts_token = xbl_result["Token"]
        uhs = xbl_result["DisplayClaims"]["xui"][0]["uhs"]

        mc_result = self._minecraft_auth(xsts_token, uhs)
        mc_access_token = mc_result["access_token"]

        profile_result = self._minecraft_profile(mc_access_token)
        username = profile_result["name"]
        player_uuid = profile_result["id"]

        account = AuthResult(
            username=username,
            uuid_str=player_uuid,
            access_token=mc_access_token,
            user_type="msa",
        )

        accounts = self.load_accounts()
        accounts = [a for a in accounts if a.username != username]
        accounts.append(account)
        self.save_accounts(accounts)
        self.current_account = account
        return account

    def _xbox_live_auth(self, ms_access_token):
        xbl_url = "https://user.auth.xboxlive.com/user/authenticate"
        xbl_data = {
            "Properties": {
                "AuthMethod": "RPS",
                "SiteName": "user.auth.xboxlive.com",
                "RpsTicket": f"d={ms_access_token}",
            },
            "RelyingParty": "http://auth.xboxlive.com",
            "TokenType": "JWT",
        }
        resp = requests.post(xbl_url, json=xbl_data, timeout=30)
        resp.raise_for_status()
        xbl_json = resp.json()

        xsts_url = "https://xsts.auth.xboxlive.com/xsts/authorize"
        xsts_data = {
            "Properties": {
                "SandboxId": "RETAIL",
                "UserTokens": [xbl_json["Token"]],
            },
            "RelyingParty": "rp://api.minecraftservices.com/",
            "TokenType": "JWT",
        }
        resp = requests.post(xsts_url, json=xsts_data, timeout=30)
        if resp.status_code == 401:
            raise Exception("XSTS authentication failed. You may not own Minecraft.")
        resp.raise_for_status()
        return resp.json()

    def _minecraft_auth(self, xsts_token, uhs):
        mc_url = "https://api.minecraftservices.com/authentication/login_with_xbox"
        mc_data = {"identityToken": f"XBL3.0 x={uhs};{xsts_token}"}
        resp = requests.post(mc_url, json=mc_data, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _minecraft_profile(self, mc_access_token):
        profile_url = "https://api.minecraftservices.com/minecraft/profile"
        headers = {"Authorization": f"Bearer {mc_access_token}"}
        resp = requests.get(profile_url, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def login_skin_station(self, email, password, api_root, callback=None):
        api_root = api_root.rstrip("/")

        auth_payload = {
            "agent": {"name": "Minecraft", "version": 1},
            "username": email,
            "password": password,
            "requestUser": True,
        }
        resp = requests.post(
            f"{api_root}/authserver/authenticate",
            json=auth_payload,
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()

        if "error" in result:
            raise Exception(
                result.get("errorMessage", result.get("error", "Unknown error"))
            )

        access_token = result["accessToken"]
        selected = result.get("selectedProfile")
        if not selected:
            profiles = result.get("availableProfiles", [])
            if profiles:
                selected = profiles[0]
            else:
                raise Exception("No character found on this account.")

        username = selected["name"]
        player_uuid = selected["id"]

        account = AuthResult(
            username=username,
            uuid_str=player_uuid,
            access_token=access_token,
            user_type="yggdrasil",
            api_root=api_root,
        )

        accounts = self.load_accounts()
        accounts = [a for a in accounts if not (a.username == username and a.user_type == "yggdrasil" and a.api_root == api_root)]
        accounts.append(account)
        self.save_accounts(accounts)
        self.current_account = account
        return account