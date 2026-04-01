# Command Center — Project Claude Code Rules
# Mother (health monitor) + Skywise (Telegram bot). Runs on cutter74. See global CLAUDE.md for universal rules.

## Infrastructure
- Machine: cutter74 (Linux Mint, always-on)
- SSH: ssh guest74-linux@100.71.78.105
- Mother container: openclaw-openclaw-gateway-1
- Mother compose file: /home/guest74-linux/openclaw/docker-compose.yml
- Mother health script: /home/guest74-linux/mother-scripts/mother_health.py
- Mother primary .env: /home/guest74-linux/openclaw/.env (Docker Compose loads THIS one)
- Mother openclaw.json: /home/guest74-linux/.openclaw/openclaw.json
- Skywise DB: psql postgresql://skywise:skywise_pass@localhost:5432/skywise_db
- IB Gateway: port 7497 (paper trading), requires Xvfb :1 for headless operation
- Ollama: localhost:11434, Qwen 2.5 3B model

## Key Rules
- Mother runs INSIDE Docker — she cannot run host commands directly
- mother_health.py runs on HOST for actual checks — bot handles communication only
- NEVER use nano to update tokens — always Python replace scripts
- Mother Discord token lives in TWO places — both must be updated on rotation:
  1. /home/guest74-linux/openclaw/.env (no quotes: DISCORD_BOT_TOKEN=token)
  2. /home/guest74-linux/.openclaw/openclaw.json (channels.discord.token field)
- AXIS token format uses quotes, Mother does NOT — don't mix them up
- groupPolicy must be "allowlist" with guilds: {"1465452290369654857": {}}

## Restart Commands
- Mother: cd /home/guest74-linux/openclaw && docker compose down && docker compose up -d
- Verify: sleep 8 && docker logs openclaw-openclaw-gateway-1 --tail 10
- Cinnamon crash fix: DISPLAY=:0 cinnamon --replace & (SSH in from cutterspredator first)

## Health Monitoring
- Mother alerts on failures only — silence = healthy (intentional, not broken)
- check_strategy_health() reads AXIS_PMS health file via local docker exec
- Health file: scan-health-axis_pms.json inside openclaw container
- 30-min heartbeat active, 5-min health checks via cron
- vps_docker_check SSHes into Hetzner (5.78.179.50) to verify containers

## Skywise
- Telegram bot: /status, /brief, /tasks commands all confirmed working
- Morning brief fires at 7 AM PT
- Next task: connect IB Gateway to Skywise for live trade P&L data

## Security
- gateway bound to loopback (127.0.0.1) only — never 0.0.0.0
- logging.redactSensitive must be enabled
- Secrets vault: SOPS + age encryption, secrets.yaml on cutter74
