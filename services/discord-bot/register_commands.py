import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "api"))
sys.path.insert(0, str(ROOT / "scanner-engine"))

from scip_api.discord_interactions import APPLICATION_COMMANDS


def load_local_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name and name not in os.environ:
            os.environ[name] = value


def main() -> int:
    load_local_env()
    application_id = os.environ.get("DISCORD_CLIENT_ID", "").strip()
    bot_token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    guild_id = os.environ.get("DISCORD_GUILD_ID", "").strip()
    if not application_id or not bot_token:
        print("Set DISCORD_CLIENT_ID and DISCORD_BOT_TOKEN in the environment or ignored .env.", file=sys.stderr)
        return 2

    if guild_id:
        endpoint = (
            f"https://discord.com/api/v10/applications/{application_id}"
            f"/guilds/{guild_id}/commands"
        )
        scope = "guild"
    else:
        endpoint = f"https://discord.com/api/v10/applications/{application_id}/commands"
        scope = "global"

    try:
        with httpx.Client(timeout=20) as client:
            response = client.put(
                endpoint,
                headers={"Authorization": f"Bot {bot_token}"},
                json=APPLICATION_COMMANDS,
            )
        if response.is_error:
            print(
                f"Discord rejected command registration ({response.status_code}): "
                f"{response.text[:1000]}",
                file=sys.stderr,
            )
            return 1
        registered = response.json()
    except httpx.RequestError as exc:
        print(f"Could not reach Discord command registration API: {exc}", file=sys.stderr)
        return 1

    print(f"Registered {len(registered)} {scope} commands: " +
          ", ".join(command["name"] for command in registered))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
