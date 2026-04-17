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
from datetime import datetime

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

        # Warn early if email is enabled but password is missing
        if self.email_enabled and not NOTIFICATIONS['email'].get('sender_password'):
            msg = (
                "EMAIL DISABLED: GMAIL_APP_PASSWORD is not set. "
                "In Docker, pass it via --env-file .env or -e GMAIL_APP_PASSWORD=... "
                "The .env file is excluded from the Docker image by .dockerignore."
            )
            logger.warning(msg)
            print(f"[notifier] WARNING: {msg}", flush=True)
            self.email_enabled = False

        # Persistent notification cache — survives across process runs within a day
        self._cache_file = Path('scanner_output') / '.notification_cache.json'
        self._sent_cache = self._load_cache()

    def _load_cache(self) -> set:
        """Load today's sent notification keys from disk."""
        try:
            if self._cache_file.exists():
                data = json.loads(self._cache_file.read_text())
                # Only use cache from today
                if data.get('date') == datetime.now().strftime('%Y-%m-%d'):
                    return set(data.get('keys', []))
        except Exception:
            pass
        return set()

    def _save_cache(self):
        """Persist sent notification keys to disk (locked to prevent race conditions)."""
        try:
            import fcntl
            self._cache_file.parent.mkdir(parents=True, exist_ok=True)
            lock_path = str(self._cache_file) + '.lock'
            with open(lock_path, 'w') as lf:
                fcntl.flock(lf, fcntl.LOCK_EX)
                try:
                    # Re-read cache under lock to merge with concurrent writers
                    existing_keys = set()
                    if self._cache_file.exists():
                        try:
                            disk = json.loads(self._cache_file.read_text())
                            if disk.get('date') == datetime.now().strftime('%Y-%m-%d'):
                                existing_keys = set(disk.get('keys', []))
                        except Exception:
                            pass
                    merged = existing_keys | self._sent_cache
                    self._sent_cache = merged
                    data = {
                        'date': datetime.now().strftime('%Y-%m-%d'),
                        'keys': list(merged),
                    }
                    self._cache_file.write_text(json.dumps(data))
                finally:
                    fcntl.flock(lf, fcntl.LOCK_UN)
        except Exception as e:
            logger.debug(f"Cache save failed: {e}")

    def _generate_cache_key(self, subject: str, signals: Optional[List[Dict]]) -> str:
        """Generate unique key for notification to prevent duplicates"""
        if signals:
            symbols = sorted([s.get('Symbol', s.get('symbol', '')) for s in signals])
            return f"{subject}:{','.join(symbols)}"
        return subject
    
    def send_all(self, subject: str, message: str, signals: Optional[List[Dict]] = None,
                 csv_path: Optional[str] = None, notification_type: str = 'signals',
                 force: bool = False):
        """Send notification via all enabled channels (prevents duplicates)

        Args:
            subject: Email subject / notification title
            message: Body message
            signals: List of signal dictionaries
            csv_path: Optional path to CSV file to attach to email
            notification_type: Type of notification ('signals', 'exits', 'errors', 'alerts')
            force: Skip dedup check (for user-triggered notifications)
        """
        # Check if already sent
        cache_key = self._generate_cache_key(subject, signals)
        if not force and cache_key in self._sent_cache:
            logger.debug(f"Skipping duplicate notification: {subject}")
            return
        
        if not any([self.email_enabled, self.telegram_enabled, self.discord_enabled, 
                   self.mac_native_enabled, self.webhook_enabled]):
            return
        
        # Mark as sent and persist to disk
        self._sent_cache.add(cache_key)
        self._save_cache()

        results = []
        
        if self.email_enabled:
            results.append(('Email', self.send_email(subject, message, signals, csv_path=csv_path)))
        
        if self.telegram_enabled:
            results.append(('Telegram', self.send_telegram(message, signals)))
        
        if self.discord_enabled:
            results.append(('Discord', self.send_discord(subject, message, signals, notification_type)))
        
        if self.mac_native_enabled:
            results.append(('Mac Native', self.send_mac_notification(subject, message, signals)))
        
        if self.webhook_enabled:
            results.append(('Webhook', self.send_webhook(signals)))

        # Expo push (always attempt if tokens exist)
        results.append(('Expo Push', self.send_expo_push(subject, message, signals)))

        # Log results — warn loudly if ALL channels fail (trader misses alerts)
        failures = []
        for channel, success in results:
            if success:
                logger.info(f"✓ {channel} notification sent")
            else:
                failures.append(channel)
                logger.warning(f"✗ {channel} notification failed")

        if failures and len(failures) == len(results):
            logger.critical(
                f"ALL notification channels failed for '{subject}' — "
                f"trader will NOT receive this alert! Channels: {', '.join(failures)}"
            )
    
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

            # Build message (plain text — no Markdown to avoid parse errors)
            text = f"🚨 {message}\n\n"

            if signals:
                text += f"📊 Found {len(signals)} signals:\n\n"
                for sig in signals[:5]:  # Limit to 5 in Telegram
                    sector = f" ({sig['Sector']})" if sig.get('Sector') else ""
                    text += (
                        f"🚀 {sig['Symbol']}{sector} — {sig['Quality']}\n"
                        f"   Price: ${sig['Price']} | SL: ${sig['Stop']} | TP: ${sig['Target']}\n"
                        f"   R:R: {sig['R:R']} | Vol: {sig['Vol']}x\n\n"
                    )

                if len(signals) > 5:
                    text += f"... and {len(signals) - 5} more signals\n"

            # Send via Telegram Bot API (no parse_mode — plain text avoids escaping issues)
            url = f"https://api.telegram.org/bot{config['bot_token']}/sendMessage"
            data = {
                'chat_id': config['chat_id'],
                'text': text,
            }

            response = requests.post(url, data=data, timeout=10)
            return response.status_code == 200
        
        except Exception as e:
            logger.error(f"Telegram notification failed: {e}")
            return False
    
    def send_discord(self, subject: str, message: str, signals: Optional[List[Dict]] = None,
                     notification_type: str = 'signals') -> bool:
        """Send Discord notification via webhook
        
        Args:
            subject: Notification title
            message: Notification body
            signals: List of signal dictionaries
            notification_type: Type of notification ('signals', 'exits', 'errors', 'alerts')
        """
        try:
            config = NOTIFICATIONS['discord']
            
            # Select webhook based on notification type
            webhooks = config.get('webhooks', {})
            if notification_type in webhooks:
                webhook_url = webhooks[notification_type]
            else:
                # Fallback to legacy single webhook or default
                webhook_url = config.get('webhook_url')
            
            if not webhook_url:
                logger.error(f"No Discord webhook configured for type '{notification_type}'")
                return False
            
            # Build embed with color based on notification type
            color_map = {
                'signals': 0x00ff00,    # Green
                'exits': 0xff9900,      # Orange
                'errors': 0xff0000,     # Red
                'alerts': 0xffff00,     # Yellow
            }
            color = color_map.get(notification_type, 0x00ff00)
            
            # Build embed
            embed = {
                'title': subject,
                'description': message,
                'color': color,
                'fields': []
            }
            
            if signals:
                embed['description'] += f"\n\nFound {len(signals)} signals:"
                
                for sig in signals[:10]:  # Limit to 10 in Discord
                    sector = f" [{sig['Sector']}]" if sig.get('Sector') else ""
                    value = (
                        f"Price: ${sig['Price']} | SL: ${sig['Stop']} | TP: ${sig['Target']}\n"
                        f"R:R: {sig['R:R']} | Vol: {sig['Vol']}x"
                    )
                    # Append FinBERT sentiment line when available
                    fb_label = sig.get('FinBERT')
                    if fb_label:
                        _emoji = {'bullish': '🟢', 'bearish': '🔴', 'neutral': '⚪'}.get(fb_label, '⚪')
                        fb_score = sig.get('FinBERT_Score', 0)
                        fb_net   = sig.get('FinBERT_Net', 0)
                        fb_hl    = sig.get('FinBERT_Headline', '')
                        conf_bar = '█' * round(fb_score * 5)
                        value += (
                            f"\n{_emoji} FinBERT: **{fb_label}** {fb_score:.2f} [{conf_bar}] "
                            f"net={fb_net:+.2f}"
                        )
                        if fb_hl:
                            value += f"\n_{fb_hl[:80]}_"
                        fb_promoted = sig.get('FinBERT_Promoted', '')
                        if fb_promoted:
                            value += f"\n**⬆ PROMOTED: {fb_promoted}**"
                    # Earnings date / warning
                    _earn_warn = sig.get('Earnings_Warning', '')
                    _earn_date = sig.get('Earnings_Date', '')
                    if _earn_warn and sig.get('Quality') in ('PREMIUM', 'GOLD'):
                        value += f"\n**⚠ {_earn_warn}**"
                    elif _earn_date:
                        _earn_timing = sig.get('Earnings_Timing', '')
                        _t_str = f" ({_earn_timing})" if _earn_timing else ""
                        value += f"\nEarnings: {_earn_date}{_t_str}"

                    embed['fields'].append({
                        'name': f"{sig['Symbol']}{sector} ({sig['Quality']})",
                        'value': value,
                        'inline': False
                    })
                
                if len(signals) > 10:
                    embed['fields'].append({
                        'name': 'More signals',
                        'value': f'... and {len(signals) - 10} additional signals',
                        'inline': False
                    })
            
            data = {'embeds': [embed]}
            
            response = requests.post(webhook_url, json=data, timeout=10)
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

    def send_expo_push(self, subject: str, message: str,
                       signals: Optional[List[Dict]] = None) -> bool:
        """Send push notification via Expo Push API to all registered devices."""
        try:
            from api.push_registry import get_all_tokens
            tokens = get_all_tokens()
            if not tokens:
                return False

            # Build concise body from signals
            body = message
            if signals and len(signals) <= 3:
                lines = [f"{s.get('Symbol','')} @ ${s.get('Price','')}" for s in signals]
                body = ' | '.join(lines)

            payload = [
                {"to": t, "title": subject, "body": body, "sound": "default"}
                for t in tokens
            ]
            resp = requests.post(
                'https://exp.host/--/api/v2/push/send',
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            ok = resp.status_code == 200
            if ok:
                logger.info(f"✓ Expo push sent to {len(tokens)} device(s)")
            else:
                logger.warning(f"Expo push returned {resp.status_code}")
            return ok
        except Exception as e:
            logger.error(f"Expo push failed: {e}")
            return False

    def send_exit_notification(self, exit_results: List[Dict],
                               csv_path: Optional[str] = None):
        """Send notification for exit decisions with optional CSV attachment"""
        if not exit_results:
            return

        # Filter for actionable exits
        actionable = [r for r in exit_results if r['Action'] != 'HOLD']

        if not actionable:
            return

        subject = f"Exit Alerts: {len(actionable)} positions need attention"
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

        self.send_all(subject, message, formatted, csv_path=csv_path)

    def send_monitor_alert(self, alerts: List[Dict], all_positions: List[Dict],
                           portfolio_label: str = "Portfolio"):
        """Send portfolio monitoring alert for positions that need attention

        Args:
            alerts: Positions that need attention (HIT_STOP, NEAR_STOP, FALLING)
            all_positions: All monitored positions (for CSV attachment)
            portfolio_label: Human-readable portfolio name (e.g. "Manual Portfolio",
                             "Auto Portfolio") — shown in subject and attachment filename.
        """
        if not alerts:
            return

        # Filter to only critical alerts (HIT_STOP, NEAR_STOP) for notification body
        critical = [a for a in alerts if a['status'] in ('HIT_STOP', 'NEAR_STOP')]

        subject = f"{'🔴' if critical else '🟡'} {portfolio_label} Alert: {len(critical)} critical, {len(alerts) - len(critical)} falling"

        # Notification body: show ONLY critical alerts
        lines = [f"{portfolio_label} monitor: {len(critical)} critical alerts (HIT_STOP/NEAR_STOP)\n"]
        for a in critical:
            icon = '🔴'
            lines.append(
                f"{icon} {a['Symbol']} ({a['mode']}): "
                f"${a['current']:.2f} | Entry: ${a['entry']:.2f} | "
                f"Stop: ${a['stop']:.2f} | P&L: {a['pnl_pct']:+.1f}% | "
                f"{a['status']}"
            )

        if len(alerts) > len(critical):
            lines.append(f"\n({len(alerts) - len(critical)} additional FALLING positions — see attached CSV)")

        message = "\n".join(lines)

        # Formatted signals for notification channels: ONLY critical
        formatted = []
        for a in critical:
            formatted.append({
                'Symbol': a['Symbol'],
                'Quality': a['status'],  # HIT_STOP, NEAR_STOP
                'Price': a['current'],
                'Stop': a['stop'],
                'Target': a['target'],
                'R:R': 0,
                'Vol': 0,
                'Sector': f"{a['mode']} | P&L: {a['pnl_pct']:+.1f}%",
            })

        # Generate CSV file with ALL positions (not just alerts)
        import csv
        import tempfile
        from datetime import datetime
        csv_path = None
        try:
            label_slug = portfolio_label.lower().replace(' ', '_')
            csv_path = tempfile.mktemp(suffix=f'_{label_slug}_{datetime.now():%Y%m%d_%H%M%S}.csv')
            with open(csv_path, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=['Symbol', 'Mode', 'Entry', 'Current', 'Stop', 'Target', 'P&L%', 'Status'])
                writer.writeheader()
                # Write ALL positions, not just alerts
                for pos in all_positions:
                    writer.writerow({
                        'Symbol': pos['Symbol'],
                        'Mode': pos['mode'],
                        'Entry': f"${pos['entry']:.2f}",
                        'Current': f"${pos['current']:.2f}",
                        'Stop': f"${pos['stop']:.2f}",
                        'Target': f"${pos['target']:.2f}",
                        'P&L%': f"{pos['pnl_pct']:+.1f}%",
                        'Status': pos['status'],
                    })
        except Exception as e:
            logger.warning(f"Failed to create monitor CSV: {e}")
            csv_path = None

        self.send_all(subject, message, formatted, csv_path=csv_path)
