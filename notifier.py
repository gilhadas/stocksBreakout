"""
Notification module - supports Email, Telegram, and Discord
"""

import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Dict, Optional
import requests

from config import NOTIFICATIONS

logger = logging.getLogger(__name__)


class Notifier:
    """Handles notifications via multiple channels"""
    
    def __init__(self):
        self.email_enabled = NOTIFICATIONS['email']['enabled']
        self.telegram_enabled = NOTIFICATIONS['telegram']['enabled']
        self.discord_enabled = NOTIFICATIONS['discord']['enabled']
    
    def send_all(self, subject: str, message: str, signals: Optional[List[Dict]] = None):
        """Send notification via all enabled channels"""
        if not any([self.email_enabled, self.telegram_enabled, self.discord_enabled]):
            return
        
        results = []
        
        if self.email_enabled:
            results.append(('Email', self.send_email(subject, message, signals)))
        
        if self.telegram_enabled:
            results.append(('Telegram', self.send_telegram(message, signals)))
        
        if self.discord_enabled:
            results.append(('Discord', self.send_discord(subject, message, signals)))
        
        # Log results
        for channel, success in results:
            if success:
                logger.info(f"✓ {channel} notification sent")
            else:
                logger.warning(f"✗ {channel} notification failed")
    
    def send_email(self, subject: str, message: str, signals: Optional[List[Dict]] = None) -> bool:
        """Send email notification"""
        try:
            config = NOTIFICATIONS['email']
            
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = config['sender_email']
            msg['To'] = config['recipient_email']
            
            # Build email body
            body = f"{message}\n\n"
            
            if signals:
                body += f"Found {len(signals)} signals:\n\n"
                for sig in signals[:10]:  # Limit to 10 in email
                    body += (
                        f"• {sig['Symbol']} ({sig['Quality']}) @ ${sig['Price']}\n"
                        f"  Stop: ${sig['Stop']} | Target: ${sig['Target']} | R:R: {sig['R:R']}\n\n"
                    )
                
                if len(signals) > 10:
                    body += f"... and {len(signals) - 10} more signals. See CSV file.\n"
            
            msg.attach(MIMEText(body, 'plain'))
            
            # Send email
            with smtplib.SMTP(config['smtp_server'], config['smtp_port']) as server:
                server.starttls()
                server.login(config['sender_email'], config['sender_password'])
                server.send_message(msg)
            
            return True
        
        except Exception as e:
            logger.error(f"Email notification failed: {e}")
            return False
    
    def send_telegram(self, message: str, signals: Optional[List[Dict]] = None) -> bool:
        """Send Telegram notification"""
        try:
            config = NOTIFICATIONS['telegram']
            
            # Build message
            text = f"🚨 {message}\n\n"
            
            if signals:
                text += f"📊 Found {len(signals)} signals:\n\n"
                for sig in signals[:5]:  # Limit to 5 in Telegram
                    text += (
                        f"🚀 *{sig['Symbol']}* ({sig['Quality']})\n"
                        f"   💰 ${sig['Price']} | SL: ${sig['Stop']} | TP: ${sig['Target']}\n"
                        f"   📈 R:R: {sig['R:R']} | Vol: {sig['Vol']}x\n\n"
                    )
                
                if len(signals) > 5:
                    text += f"... and {len(signals) - 5} more signals\n"
            
            # Send via Telegram Bot API
            url = f"https://api.telegram.org/bot{config['bot_token']}/sendMessage"
            data = {
                'chat_id': config['chat_id'],
                'text': text,
                'parse_mode': 'Markdown'
            }
            
            response = requests.post(url, data=data, timeout=10)
            return response.status_code == 200
        
        except Exception as e:
            logger.error(f"Telegram notification failed: {e}")
            return False
    
    def send_discord(self, subject: str, message: str, signals: Optional[List[Dict]] = None) -> bool:
        """Send Discord notification via webhook"""
        try:
            config = NOTIFICATIONS['discord']
            
            # Build embed
            embed = {
                'title': subject,
                'description': message,
                'color': 0x00ff00 if signals else 0xffaa00,
                'fields': []
            }
            
            if signals:
                embed['description'] += f"\n\nFound {len(signals)} signals:"
                
                for sig in signals[:10]:  # Limit to 10 in Discord
                    embed['fields'].append({
                        'name': f"{sig['Symbol']} ({sig['Quality']})",
                        'value': (
                            f"Price: ${sig['Price']} | SL: ${sig['Stop']} | TP: ${sig['Target']}\n"
                            f"R:R: {sig['R:R']} | Vol: {sig['Vol']}x"
                        ),
                        'inline': False
                    })
                
                if len(signals) > 10:
                    embed['fields'].append({
                        'name': 'More signals',
                        'value': f'... and {len(signals) - 10} additional signals',
                        'inline': False
                    })
            
            data = {'embeds': [embed]}
            
            response = requests.post(config['webhook_url'], json=data, timeout=10)
            return response.status_code == 204
        
        except Exception as e:
            logger.error(f"Discord notification failed: {e}")
            return False
    
    def send_exit_notification(self, exit_results: List[Dict]):
        """Send notification for exit decisions"""
        if not exit_results:
            return
        
        # Filter for actionable exits
        actionable = [r for r in exit_results if r['Action'] != 'HOLD']
        
        if not actionable:
            return
        
        subject = f"⚠️ Exit Alerts: {len(actionable)} positions need attention"
        message = f"Exit evaluation completed. {len(actionable)} positions require action:"
        
        # Format for notification
        formatted = []
        for r in actionable:
            formatted.append({
                'Symbol': r['Symbol'],
                'Quality': r['Action'],
                'Price': r['Price'],
                'Stop': 0,
                'Target': 0,
                'R:R': r['UnrealizedR'],
                'Vol': 0
            })
        
        self.send_all(subject, message, formatted)
