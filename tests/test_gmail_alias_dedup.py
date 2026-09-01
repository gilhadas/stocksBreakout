"""
tests/test_gmail_alias_dedup.py
================================
gil.hadas@gmail.com, gil.hadas+1@gmail.com and gil.hadas+2@gmail.com are the
same real inbox (Gmail ignores everything from '+' to '@'). Per-user exit
emails must collapse these into one send to the base address, not one send
per '+alias' account.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from notifier import Notifier, _canonical_email, _dedupe_recipients


# ── _canonical_email ─────────────────────────────────────────────────────────

def test_gmail_plus_alias_stripped():
    assert _canonical_email('gil.hadas+1@gmail.com') == 'gil.hadas@gmail.com'
    assert _canonical_email('gil.hadas+2@gmail.com') == 'gil.hadas@gmail.com'


def test_base_gmail_address_unchanged():
    assert _canonical_email('gil.hadas@gmail.com') == 'gil.hadas@gmail.com'


def test_googlemail_alias_also_stripped():
    assert _canonical_email('gil.hadas+9@googlemail.com') == 'gil.hadas@googlemail.com'


def test_non_gmail_plus_address_untouched():
    """A '+tag' on a non-Gmail domain is not necessarily an alias — leave it."""
    assert _canonical_email('someone+tag@yahoo.com') == 'someone+tag@yahoo.com'


def test_no_plus_non_gmail_untouched():
    assert _canonical_email('plain@example.com') == 'plain@example.com'


# ── _dedupe_recipients ───────────────────────────────────────────────────────

def test_dedupe_recipients_collapses_plus_aliases():
    raw = 'gil.hadas@gmail.com, gil.hadas+1@gmail.com, gil.hadas+2@gmail.com'
    assert _dedupe_recipients(raw) == 'gil.hadas@gmail.com'


def test_dedupe_recipients_preserves_distinct_addresses():
    raw = 'gil.hadas@gmail.com, other@example.com, gil.hadas+9@gmail.com'
    assert _dedupe_recipients(raw) == 'gil.hadas@gmail.com, other@example.com'


def test_dedupe_recipients_single_alias_resolves_to_base():
    assert _dedupe_recipients('gil.hadas+1@gmail.com') == 'gil.hadas@gmail.com'


# ── send_exit_notification: per-user fan-out merges aliased accounts ────────

def _notifier(monkeypatch):
    monkeypatch.setenv('SB_ALLOW_TEST_NOTIFICATIONS', '1')
    n = Notifier()
    n._sent_cache.clear()
    n.email_enabled = True
    n.telegram_enabled = False
    n.discord_enabled = False
    n.mac_native_enabled = False
    n.webhook_enabled = False
    return n


def _record(sent):
    """Stand-in for Notifier.send_email that records calls by keyword,
    matching send_email(self, subject, message, signals=None, csv_path=None,
    recipient=None) regardless of whether the caller passes positionally."""
    def _fn(subject, message, signals=None, csv_path=None, recipient=None):
        sent.append({'subject': subject, 'message': message, 'signals': signals,
                      'csv_path': csv_path, 'recipient': recipient})
        return True
    return _fn


def test_exit_notification_sends_once_to_base_address(monkeypatch):
    """Two '+alias' accounts holding different symbols -> ONE email, base address."""
    n = _notifier(monkeypatch)
    sent = []
    monkeypatch.setattr(n, 'send_email', _record(sent))

    exit_results = [
        {'Symbol': 'AAA', 'Action': 'EXIT_STOP', 'Price': 10.0, 'UnrealizedR': -1.0},
        {'Symbol': 'BBB', 'Action': 'EXIT_TARGET', 'Price': 20.0, 'UnrealizedR': 2.0},
    ]
    symbol_to_users = {
        'AAA': [{'email': 'gil.hadas@gmail.com', 'user_id': 'u1'}],
        'BBB': [{'email': 'gil.hadas+1@gmail.com', 'user_id': 'u2'}],
    }

    n.send_exit_notification(exit_results, symbol_to_users=symbol_to_users)

    assert len(sent) == 1, f"Expected exactly one email, got {len(sent)}: {sent}"
    call = sent[0]
    assert call['recipient'] == 'gil.hadas@gmail.com'
    symbols = {s['Symbol'] for s in call.get('signals', [])}
    assert symbols == {'AAA', 'BBB'}, f"Merged email should list both symbols, got {symbols}"


def test_exit_notification_same_symbol_not_duplicated_within_merge(monkeypatch):
    """Both aliased accounts hold the SAME symbol -> it appears once, not twice."""
    n = _notifier(monkeypatch)
    sent = []
    monkeypatch.setattr(n, 'send_email', _record(sent))

    exit_results = [
        {'Symbol': 'AAA', 'Action': 'EXIT_STOP', 'Price': 10.0, 'UnrealizedR': -1.0},
    ]
    symbol_to_users = {
        'AAA': [
            {'email': 'gil.hadas@gmail.com', 'user_id': 'u1'},
            {'email': 'gil.hadas+2@gmail.com', 'user_id': 'u3'},
        ],
    }

    n.send_exit_notification(exit_results, symbol_to_users=symbol_to_users)

    assert len(sent) == 1
    assert len(sent[0]['signals']) == 1, "AAA should appear once, not once per aliased account"


def test_exit_notification_distinct_real_users_stay_separate(monkeypatch):
    """A genuinely different Gmail address must NOT be merged."""
    n = _notifier(monkeypatch)
    sent = []
    monkeypatch.setattr(n, 'send_email', _record(sent))

    exit_results = [
        {'Symbol': 'AAA', 'Action': 'EXIT_STOP', 'Price': 10.0, 'UnrealizedR': -1.0},
        {'Symbol': 'BBB', 'Action': 'EXIT_STOP', 'Price': 5.0, 'UnrealizedR': -0.5},
    ]
    symbol_to_users = {
        'AAA': [{'email': 'gil.hadas@gmail.com', 'user_id': 'u1'}],
        'BBB': [{'email': 'someoneelse@gmail.com', 'user_id': 'u4'}],
    }

    n.send_exit_notification(exit_results, symbol_to_users=symbol_to_users)

    recipients = {c['recipient'] for c in sent}
    assert recipients == {'gil.hadas@gmail.com', 'someoneelse@gmail.com'}
