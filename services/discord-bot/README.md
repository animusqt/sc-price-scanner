# Discord bot setup

This integration uses Discord's signed interactions endpoint; it does not run a
gateway-connected bot process. The API verifies every Discord request with the
application public key and supports `/ping`, `/market`, `/bug-report`, and
moderator-only `/review-bug`.

## Configure the Discord application

### Create server channels

In Discord, open **Server Settings → Channels** (or create channels from the
server's channel list) and create a `SCIP` category with:

- `#bot-commands` — members can send slash commands; keep bot replies ephemeral.
- `#market-reports` — community discussion or links to scan submissions.
- `#bug-reports` — discussion around reported bugs.
- `#announcements` — read-only for members if you plan to post release news.

The current slash commands reply ephemerally and do not post automatically to
these channels. Restrict `/review-bug` using **Server Settings → Integrations →
SCIP Bot → Commands** so only moderators can run it; the API independently
checks the configured moderator allow-list.

1. In the Discord Developer Portal, open the application and its **General
   Information** page. Set `DISCORD_CLIENT_ID` to the Application ID and
   `DISCORD_PUBLIC_KEY` to its Public Key in the private root `.env`.
2. On the **Bot** page, create/reset the bot token and add it only to the
   ignored `.env` as `DISCORD_BOT_TOKEN`. Never commit or share the token.
3. Add the bot to the test server with the `applications.commands` scope. The
   server ID is `DISCORD_GUILD_ID` in `.env`; guild-scoped command registration
   makes new commands available promptly.
4. Start the API and web stack, then set **Interactions Endpoint URL** on the
   app's General Information page to:
   `https://<your-public-domain>/api/v2/discord/interactions`.
   Discord must be able to reach this HTTPS endpoint. `localhost` is not
   reachable by Discord's servers; use a deployed HTTPS endpoint or a
   temporary HTTPS tunnel only for development.
5. Register or update the commands from the repository root:

   ```powershell
   python services/discord-bot/register_commands.py
   ```

   The script reads the ignored `.env`. If `DISCORD_GUILD_ID` is configured,
   it registers guild commands; otherwise, it registers global commands.

## Commands

- `/ping` — bot/API availability check.
- `/market patch shard [commodity] [location]` — recent confirmed market data
  only; pending player reports are not displayed as confirmed prices.
- `/bug-report category description [location] [patch]` — submits a report
  for moderation. Optional locations must already exist in market reference
  data.
- `/review-bug bug_id decision [route_penalty]` — moderator review. Access is
  restricted to `SCIP_MODERATOR_DISCORD_IDS` or an active moderator account.
  A penalty is only applied to a confirmed report that has both location and
  patch context; rejecting/resolving a report disables earlier penalties.

OAuth redirect URLs (such as the website's `/api/v2/auth/discord/callback`)
are separate from the **Interactions Endpoint URL**. A `scip://` redirect is
not used by this bot integration.
