# Webhook Integration Guide

Complete guide for setting up automated trading with webhooks.

## 🎯 Overview

The scanner can send trading signals to your own webhook server, enabling:
- **Automated order execution** via Interactive Brokers
- **Custom trading logic** (filters, position sizing, etc.)
- **Integration with other platforms** (TradingView, Discord, etc.)
- **Trade journaling** and analytics

## 📋 Quick Start

### 1. Setup Webhook Server

```bash
# Install dependencies
pip install flask ib_insync

# Edit webhook_server.py configuration
nano webhook_server.py

# Change these settings:
CONFIG = {
    'auth_token': 'your_strong_secret_here',  # IMPORTANT!
    'auto_execute': False,  # Start with False for testing
    'max_position_size': 100,
    'max_daily_trades': 10,
}

# Run server
python webhook_server.py
```

Server starts on `http://localhost:5000`

### 2. Configure Scanner

Edit `config.py`:

```python
NOTIFICATIONS = {
    'webhook': {
        'enabled': True,
        'url': 'http://localhost:5000/webhook',
        'auth_token': 'your_strong_secret_here',  # Must match server
        'default_quantity': 100,
    }
}
```

### 3. Run Scanner with Webhook

```bash
python breakout_scanner.py watchlist.txt --mode swing --notify
```

## 📡 Webhook Payload Format

Scanner sends JSON payloads:

```json
{
  "timestamp": "2026-01-24T10:30:00.123456",
  "source": "breakout_scanner",
  "signals": [
    {
      "symbol": "AAPL",
      "action": "BUY",
      "price": 185.50,
      "stop_loss": 180.00,
      "take_profit": 195.00,
      "quantity": 100,
      "mode": "swing",
      "quality": "PREMIUM",
      "volume_ratio": 2.5,
      "risk_reward": 2.25,
      "level2_quality": "EXCELLENT",
      "level2_imbalance": 35.5
    }
  ]
}
```

## 🔐 Security

### Authentication

Server uses Bearer token authentication:

```bash
curl -X POST http://localhost:5000/webhook \
  -H "Authorization: Bearer your_secret_token_here" \
  -H "Content-Type: application/json" \
  -d '{"signals": [...]}'
```

**IMPORTANT:**
- Use a strong, random token
- Never commit tokens to git
- Rotate tokens regularly
- Use HTTPS in production

### Network Security

For production:
```python
# Restrict to specific IPs
ALLOWED_IPS = ['127.0.0.1', '192.168.1.100']

@app.before_request
def check_ip():
    if request.remote_addr not in ALLOWED_IPS:
        abort(403)
```

## 🚀 Production Deployment

### Using systemd (Linux)

Create `/etc/systemd/system/webhook.service`:

```ini
[Unit]
Description=Breakout Scanner Webhook Server
After=network.target

[Service]
Type=simple
User=trader
WorkingDirectory=/home/trader/breakout_scanner
ExecStart=/usr/bin/python3 /home/trader/breakout_scanner/webhook_server.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable webhook
sudo systemctl start webhook
sudo systemctl status webhook
```

### Using Docker

```dockerfile
FROM python:3.10-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY webhook_server.py .
EXPOSE 5000

CMD ["python", "webhook_server.py"]
```

Build and run:
```bash
docker build -t webhook-server .
docker run -d -p 5000:5000 --name webhook webhook-server
```

### Behind Nginx (Reverse Proxy)

```nginx
server {
    listen 443 ssl;
    server_name webhook.yourdomain.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location /webhook {
        proxy_pass http://localhost:5000/webhook;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

## 🧪 Testing

### Test Webhook Endpoint

```bash
# Health check
curl http://localhost:5000/status

# Test with sample signal
curl -X POST http://localhost:5000/webhook \
  -H "Authorization: Bearer your_token" \
  -H "Content-Type: application/json" \
  -d '{
    "timestamp": "2026-01-24T10:30:00",
    "source": "test",
    "signals": [{
      "symbol": "AAPL",
      "action": "BUY",
      "price": 185.50,
      "stop_loss": 180.00,
      "take_profit": 195.00,
      "quantity": 100,
      "mode": "swing",
      "quality": "HIGH"
    }]
  }'
```

### Enable Auto-Execution (CAREFUL!)

1. **Test thoroughly on paper trading first**
2. Edit `webhook_server.py`:
```python
CONFIG = {
    'auto_execute': True,  # Enable
    'ib_port': 7497,  # Keep on paper
}
```
3. Monitor `webhook_log.jsonl` for executions
4. Only move to live after extensive testing

## 📊 Monitoring

### View Logs

```bash
# Real-time log viewing
tail -f webhook_log.jsonl | jq

# Count today's signals
grep $(date +%Y-%m-%d) webhook_log.jsonl | wc -l

# Failed trades
grep '"success": false' webhook_log.jsonl | jq
```

### Monitoring Script

```bash
#!/bin/bash
# monitor_webhook.sh

while true; do
  response=$(curl -s http://localhost:5000/status)
  trades=$(echo $response | jq -r '.daily_trades')
  echo "$(date) - Active | Daily trades: $trades"
  sleep 60
done
```

## 🔄 Integration Examples

### With TradingView

TradingView can send alerts to your webhook:

1. Create alert in TradingView
2. Set webhook URL: `http://your-server.com/webhook`
3. Message format:
```json
{
  "timestamp": "{{timenow}}",
  "source": "tradingview",
  "signals": [{
    "symbol": "{{ticker}}",
    "action": "{{strategy.order.action}}",
    "price": {{close}},
    "quantity": 100
  }]
}
```

### With Discord Bot

Forward webhook data to Discord:

```python
@app.route('/webhook', methods=['POST'])
def webhook():
    # ... existing code ...
    
    # Send to Discord
    discord_url = "YOUR_DISCORD_WEBHOOK"
    discord_payload = {
        "content": f"New signal: {signal['symbol']} @ ${signal['price']}"
    }
    requests.post(discord_url, json=discord_payload)
```

## ⚠️ Important Warnings

1. **Auto-trading is risky** - Can lead to significant losses
2. **Test extensively** - Use paper trading for weeks/months
3. **Monitor actively** - Don't set and forget
4. **Have kill switch** - Be able to disable quickly
5. **Position limits** - Never risk more than you can afford
6. **Network issues** - Handle disconnections gracefully
7. **Market hours** - Don't trade outside regular hours
8. **Slippage** - Actual fills may differ from signals

## 🐛 Troubleshooting

### Webhook not receiving signals

```bash
# Check scanner is sending
python breakout_scanner.py watchlist.txt --mode swing --notify

# Check server is running
curl http://localhost:5000/status

# Check auth token matches
grep auth_token config.py webhook_server.py

# Check firewall
sudo ufw allow 5000
```

### Orders not executing

```bash
# Check IB connection
# - TWS/Gateway running?
# - API enabled?
# - Correct port?

# Check trade logs
tail -f webhook_log.jsonl

# Check IB paper account
# - Funds available?
# - Stock tradeable?
# - Market open?
```

## 📚 Additional Resources

- [Flask Documentation](https://flask.palletsprojects.com/)
- [IB API Guide](https://interactivebrokers.github.io/tws-api/)
- [ib_insync Docs](https://ib-insync.readthedocs.io/)

## 📄 License

Same as main scanner - MIT License. Use at your own risk.
