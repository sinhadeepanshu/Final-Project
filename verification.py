"""
verification.py – Dual verification (sender + receiver). Firestore backend.
"""
import re
from datetime import datetime, timezone, timedelta
from models import Account, Verification, Transaction, Blacklist, User
from flask import current_app


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _daily_sent(sender_acc_id: str) -> float:
    since = utcnow() - timedelta(hours=24)
    all_txns = Transaction.filter_by(sender_acc_id=sender_acc_id, status='EXECUTED').all()
    return sum(t.amount for t in all_txns if t.created_at and t.created_at >= since)


def verify_sender(transaction) -> dict:
    account = Account.get(transaction.sender_acc_id)
    if not account:
        return {'pass': False, 'details': 'Sender account not found.'}
    if account.status != 'ACTIVE':
        return {'pass': False, 'details': f'Sender account is {account.status}.'}

    user = User.get(account.user_id)
    if not user or user.kyc_status != 'VERIFIED':
        return {'pass': False, 'details': 'KYC verification required before transfers.'}

    if account.balance < transaction.amount:
        return {'pass': False, 'details': 'Insufficient balance.'}

    daily_limit = current_app.config.get('DAILY_LIMIT', 20_000_000)
    sent_today  = _daily_sent(transaction.sender_acc_id)
    if sent_today + transaction.amount > daily_limit:
        return {'pass': False, 'details': f'Daily limit of Rs {daily_limit:,.0f} would be exceeded.'}

    return {'pass': True, 'details': 'Sender verification passed.'}


def verify_receiver(transaction) -> dict:
    if not re.match(r'^[A-Z]{4}0[A-Z0-9]{6}$', transaction.receiver_ifsc or ''):
        return {'pass': False, 'details': 'Invalid IFSC code format.'}

    acc_num = transaction.receiver_acc or ''
    if not (9 <= len(acc_num) <= 18 and acc_num.isdigit()):
        return {'pass': False, 'details': 'Invalid beneficiary account number.'}

    sender_account = Account.get(transaction.sender_acc_id)
    if sender_account and sender_account.account_number == acc_num:
        return {'pass': False, 'details': 'Self-transfers are not allowed.'}

    bl = Blacklist.filter_by(account_number=acc_num).first()
    if bl:
        return {'pass': False, 'details': f'Beneficiary account is blacklisted: {bl.reason}'}

    return {'pass': True, 'details': 'Receiver verification passed.'}


def run_dual_verification(transaction) -> dict:
    sender_result   = verify_sender(transaction)
    receiver_result = verify_receiver(transaction)

    v_sender = Verification(
        transaction_id=transaction.transaction_id,
        party='SENDER',
        result='PASS' if sender_result['pass'] else 'FAIL',
        details=sender_result['details'],
    )
    v_sender.save()

    v_receiver = Verification(
        transaction_id=transaction.transaction_id,
        party='RECEIVER',
        result='PASS' if receiver_result['pass'] else 'FAIL',
        details=receiver_result['details'],
    )
    v_receiver.save()

    reject_reason = None
    if not sender_result['pass']:
        reject_reason = f"Sender: {sender_result['details']}"
    elif not receiver_result['pass']:
        reject_reason = f"Receiver: {receiver_result['details']}"

    return {
        'sender_pass':   sender_result['pass'],
        'receiver_pass': receiver_result['pass'],
        'reject_reason': reject_reason,
    }
