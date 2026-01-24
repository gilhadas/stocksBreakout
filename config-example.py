"""
Configuration Example File
Copy this to config.py and customize for your setup
"""

# --- Notification Settings ---
# Enable the channels you want to use

NOTIFICATIONS = {
    # Email notifications via SMTP
    'email': {
        'enabled': False,  # Set to True to enable
        'smtp_server': 'smtp.gmail.com',
        'smtp_port': 587,
        'sender_email': 'your_scanner@gmail.com',
        'sender_password': 'your_app_password_here',  # Use Gmail App Password
        'recipient_email': 'your_alerts@gmail.com',
    },
    
    # Telegram notifications
    'telegram': {
        'enabled': False,  # Set to True to enable
        'bot_token': 'YOUR_BOT_TOKEN_FROM_BOTFATHER',
        'chat_id': 'YOUR_CHAT_ID',
    },
    
    # Discord notifications via webhook
    'discord': {
        'enabled': False,  # Set to True to enable
        'webhook_url': 'YOUR_DISCORD_WEBHOOK_URL',
    }
}

# --- Gmail App Password Setup ---
# 1. Go to https://myaccount.google.com/security
# 2. Enable 2-Step Verification
# 3. Go to App Passwords: https://myaccount.google.com/apppasswords
# 4. Create password for "Mail" on "Other (Custom name)"
# 5. Use the 16-character password above

# --- Telegram Bot Setup ---
# 1. Open Telegram and search for @BotFather
# 2. Send /newbot and follow instructions
# 3. Save the bot token
# 4. Search for @userinfobot and send /start
# 5. Save your chat ID
# 6. Start a conversation with your bot (send /start)

# --- Discord Webhook Setup ---
# 1. Open your Discord server
# 2. Go to Server Settings > Integrations > Webhooks
# 3. Click "New Webhook"
# 4. Name it (e.g., "Breakout Scanner")
# 5. Choose the channel for alerts
# 6. Copy webhook URL

# Example notification configurations:

# GMAIL EXAMPLE:
# 'email': {
#     'enabled': True,
#     'smtp_server': 'smtp.gmail.com',
#     'smtp_port': 587,
#     'sender_email': 'scanner.alerts@gmail.com',
#     'sender_password': 'abcd efgh ijkl mnop',  # 16-char app password
#     'recipient_email': 'mytrading@gmail.com',
# }

# TELEGRAM EXAMPLE:
# 'telegram': {
#     'enabled': True,
#     'bot_token': '123456789:ABCdefGHIjklMNOpqrsTUVwxyz',
#     'chat_id': '987654321',
# }

# DISCORD EXAMPLE:
# 'discord': {
#     'enabled': True,
#     'webhook_url': 'https://discord.com/api/webhooks/123/abc...',
# }

# WEBHOOK FOR AUTOMATED TRADING:
# 'webhook': {
#     'enabled': True,
#     'url': 'http://localhost:5000/webhook',  # Your webhook server
#     'auth_token': 'your_secret_token_here',  # Must match server config
#     'default_quantity': 100,  # Default order size
# }

# Mac Native Notifications:
# Automatically enabled on macOS - no configuration needed!
# Shows notifications in Notification Center

# You can enable multiple channels at once!
# The scanner will send notifications to all enabled channels.

# --- Level 2 (Market Depth) Settings ---
# Enable with --level2 flag when running scanner
# Requires IB market depth subscription
# Analyzes bid/ask liquidity for higher probability entries
