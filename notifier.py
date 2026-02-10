"""
Notification module - supports Email, Telegram, Discord, Mac Native, and Webhooks
"""

import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from typing import List, Dict, Optional
from pathlib import Path
import requests
import platform
import subprocess
import json
import pandas as pd

from config import NOTIFICATIONS

logger = logging.getLogger(__name__)


class Notifier:
    """Handles notifications via multiple channels"""
    
    def __init__(self):
        self.email_enabled = NOTIFICATIONS['email']['enabled']
        self.telegram_enabled = NOTIFICATIONS['telegram']['enabled']
        self.discord_enabled = NOTIFICATIONS['discord']['enabled']
        self.webhook_enabled = NOTIFICATIONS.get('webhook', {}).get('enabled', False)
        
        # Mac native notifications (auto-detect)
        self.mac_native_enabled = platform.system() == 'Darwin'
        
        # Track sent notifications to prevent duplicates
        self._sent_cache = set()
    
    def _generate_cache_key(self, subject: str, signals: Optional[List[Dict]]) -> str:
        """Generate unique key for notification to prevent duplicates"""
        if signals:
            symbols = sorted([s['Symbol'] for s in signals])
            return f"{subject}:{','.join(symbols)}"
        return subject
    
    def send_all(self, subject: str, message: str, signals: Optional[List[Dict]] = None,
                 csv_path: Optional[str] = None):
        """Send notification via all enabled channels (prevents duplicates)
        
        Args:
            subject: Email subject / notification title
            message: Body message
            signals: List of signal dictionaries
            csv_path: Optional path to CSV file to attach to email
        """
        # Check if already sent
        cache_key = self._generate_cache_key(subject, signals)
        if cache_key in self._sent_cache:
            logger.debug(f"Skipping duplicate notification: {subject}")
            return
        
        if not any([self.email_enabled, self.telegram_enabled, self.discord_enabled, 
                   self.mac_native_enabled, self.webhook_enabled]):
            return
        
        # Mark as sent
        self._sent_cache.add(cache_key)
        
        results = []
        
        if self.email_enabled:
            results.append(('Email', self.send_email(subject, message, signals, csv_path=csv_path)))
        
        if self.telegram_enabled:
            results.append(('Telegram', self.send_telegram(message, signals)))
        
        if self.discord_enabled:
            results.append(('Discord', self.send_discord(subject, message, signals)))
        
        if self.mac_native_enabled:
            results.append(('Mac Native', self.send_mac_notification(subject, message, signals)))
        
        if self.webhook_enabled:
            results.append(('Webhook', self.send_webhook(signals)))
        
        # Log results
        for channel, success in results:
            if success:
                logger.info(f"✓ {channel} notification sent")
            else:
                logger.warning(f"✗ {channel} notification failed")
    
    def send_email(self, subject: str, message: str, signals: Optional[List[Dict]] = None,
                   csv_path: Optional[str] = None) -> bool:
        """Send email notification with optional CSV attachment"""
        try:
            config = NOTIFICATIONS['email']
            
            msg = MIMEMultipart('mixed')
            msg['Subject'] = subject
            msg['From'] = config['sender_email']
            msg['To'] = config['recipient_email']
            
            # Build email body
            body = f"{message}\n\n"
            
            if signals:
                body += f"Found {len(signals)} signals:\n\n"
                for sig in signals[:10]:  # Limit to 10 in email
                    sector = f" [{sig['Sector']}]" if sig.get('Sector') else ""
                    body += (
                        f"• {sig['Symbol']}{sector} ({sig['Quality']}) @ ${sig['Price']}\n"
                        f"  Stop: ${sig['Stop']} | Target: ${sig['Target']} | R:R: {sig['R:R']}\n\n"
                    )
                
                if len(signals) > 10:
                    body += f"... and {len(signals) - 10} more signals. See attached CSV.\n"
            
            msg.attach(MIMEText(body, 'plain'))
            
            # Attach CSV if provided
            if csv_path:
                csv_file = Path(csv_path)
                if csv_file.exists():
                    with open(csv_file, 'rb') as f:
                        part = MIMEBase('application', 'octet-stream')
                        part.set_payload(f.read())
                    encoders.encode_base64(part)
                    part.add_header(
                        'Content-Disposition',
                        f'attachment; filename="{csv_file.name}"'
                    )
                    msg.attach(part)
                    logger.debug(f"Attached CSV: {csv_file.name}")
            
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
                    sector = f" [{sig['Sector']}]" if sig.get('Sector') else ""
                    text += (
                        f"🚀 *{sig['Symbol']}*{sector} ({sig['Quality']})\n"
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
                    sector = f" [{sig['Sector']}]" if sig.get('Sector') else ""
                    embed['fields'].append({
                        'name': f"{sig['Symbol']}{sector} ({sig['Quality']})",
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
    
    def send_mac_notification(self, subject: str, message: str, 
                             signals: Optional[List[Dict]] = None) -> bool:
        """
        Send Mac native notification
        Uses osascript to trigger Notification Center
        """
        try:
            # Build notification text
            if signals:
                body = f"{message}\n\nFound {len(signals)} signals"
                if signals:
                    top_signal = signals[0]
                    body += f"\nTop: {top_signal['Symbol']} @ ${top_signal['Price']}"
            else:
                body = message
            
            # Escape quotes for AppleScript
            subject_escaped = subject.replace('"', '\\"')
            body_escaped = body.replace('"', '\\"').replace('\n', ' ')
            
            # AppleScript command
            script = f'''
            display notification "{body_escaped}" with title "{subject_escaped}" sound name "Glass"
            '''
            
            # Execute
            subprocess.run(
                ['osascript', '-e', script],
                capture_output=True,
                timeout=5
            )
            
            return True
        
        except Exception as e:
            logger.error(f"Mac notification failed: {e}")
            return False
    
    def send_webhook(self, signals: Optional[List[Dict]] = None) -> bool:
        """
        Send webhook for automated trading integration
        Sends JSON payload to configured endpoint
        """
        try:
            config = NOTIFICATIONS.get('webhook', {})
            
            if not config.get('url'):
                return False
            
            # Build payload
            payload = {
                'timestamp': pd.Timestamp.now().isoformat(),
                'source': 'breakout_scanner',
                'signals': []
            }
            
            if signals:
                for sig in signals:
                    # Convert to trading-friendly format
                    signal_data = {
                        'symbol': sig['Symbol'],
                        'sector': sig.get('Sector', ''),
                        'action': 'BUY',  # Breakout = long entry
                        'price': sig['Price'],
                        'stop_loss': sig['Stop'],
                        'take_profit': sig['Target'],
                        'quantity': config.get('default_quantity', 100),
                        'mode': sig['Mode'],
                        'quality': sig['Quality'],
                        'volume_ratio': sig['Vol'],
                        'risk_reward': sig['R:R']
                    }
                    
                    # Add Level 2 data if available
                    if 'Level2_Quality' in sig:
                        signal_data['level2_quality'] = sig['Level2_Quality']
                        signal_data['level2_imbalance'] = sig.get('Level2_Imbalance', 0)
                    
                    payload['signals'].append(signal_data)
            
            # Send webhook
            headers = {
                'Content-Type': 'application/json'
            }
            
            # Add authentication if configured
            if config.get('auth_token'):
                headers['Authorization'] = f"Bearer {config['auth_token']}"
            
            response = requests.post(
                config['url'],
                json=payload,
                headers=headers,
                timeout=10
            )
            
            if response.status_code in [200, 201, 202]:
                logger.info(f"Webhook sent successfully: {len(payload['signals'])} signals")
                return True
            else:
                logger.warning(f"Webhook returned status {response.status_code}")
                return False
        
        except Exception as e:
            logger.error(f"Webhook notification failed: {e}")
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
