# Discord verification integration

The API implements Discord OAuth `identify`, verifies guild membership with
the configured bot credential, and can enforce a specific verified role.
Configure `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `DISCORD_REDIRECT_URI`,
`DISCORD_GUILD_ID`, `DISCORD_BOT_TOKEN`, and optionally
`DISCORD_VERIFIED_ROLE_ID` in the API environment.

No bot token or OAuth secret belongs in source control or the desktop app.
There is no independent Discord command bot in this repository yet. Bot
commands, role assignment, slash-command registration, and supporter role
synchronization require the production Discord guild/application and permission
decisions.
