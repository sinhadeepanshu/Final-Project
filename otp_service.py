"""
otp_service.py – OTP generation, hashing, storage and verification (Firestore backend).
"""
import random
import string
from datetime import datetime, timezone, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from flask import current_app
from models import OtpCode
from sms_service import send_otp_sms, normalize_phone


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _generate_otp(length: int = 6) -> str:
    return ''.join(random.choices(string.digits, k=length))


# ---------------------------------------------------------------------------
# send_otp
# ---------------------------------------------------------------------------

def send_otp(phone: str, purpose: str, transaction_id: str = None,
             amount: float = None, receiver_name: str = None,
             user_name: str = '') -> dict:
    phone = normalize_phone(phone)
    cfg   = current_app.config

    # Idempotency: if a recent valid OTP exists, respect resend cooldown
    all_otps = OtpCode.filter_by(phone=phone, purpose=purpose, used=False).all()
    # Filter by transaction_id
    matching = [o for o in all_otps if o.transaction_id == transaction_id]
    matching.sort(key=lambda o: o.created_at or utcnow(), reverse=True)
    existing = matching[0] if matching else None

    if existing:
        seconds_since = (utcnow() - existing.last_sent_at).total_seconds()
        cooldown = cfg.get('OTP_RESEND_COOLDOWN', 30)
        if seconds_since < cooldown:
            wait = int(cooldown - seconds_since)
            return {
                'success': False,
                'message': f'Please wait {wait}s before resending.',
                'dev_fallback': False,
            }
        if existing.resend_count >= cfg.get('OTP_MAX_RESENDS', 3):
            return {
                'success': False,
                'message': 'Maximum resend limit reached. Please start over.',
                'dev_fallback': False,
            }
        # Invalidate old OTP
        existing.used = True
        existing.save()

    otp    = _generate_otp()
    expiry = utcnow() + timedelta(seconds=cfg.get('OTP_EXPIRY_SECONDS', 300))
    expires_str = expiry.strftime('%Y-%m-%d %H:%M:%S UTC')

    if purpose == 'PAYMENT' and amount and receiver_name:
        amt_fmt = f'Rs {amount:,.0f}'
        body = (f'PayVanta: OTP {otp} to pay {amt_fmt} to {receiver_name}. '
                f'Valid 5 mins. Do not share.')
    elif purpose == 'SIGNUP':
        body = f'PayVanta: Your signup OTP is {otp}. Valid 5 minutes. Do not share.'
    else:
        body = f'PayVanta: Your login OTP is {otp}. Valid 5 minutes. Do not share.'

    resend_count = (existing.resend_count + 1) if existing else 0

    new_otp = OtpCode(
        phone=phone,
        purpose=purpose,
        transaction_id=transaction_id,
        otp_hash=generate_password_hash(str(otp)),
        expires_at=expiry,
        resend_count=resend_count,
        last_sent_at=utcnow(),
    )
    new_otp.save()

    send_otp_sms(
        phone=phone,
        otp=str(otp),
        message_body=body,
        purpose=purpose,
        user_name=user_name or phone,
        expires=expires_str,
    )

    dev_mode = bool(cfg.get('DEV_MODE', False))
    return {
        'success': True,
        'dev_fallback': dev_mode,
        'message': ('OTP is available on-screen and in the terminal for local testing.'
                    if dev_mode else 'OTP sent. Check your registered phone.'),
        **({'otp': str(otp)} if dev_mode else {}),
    }


# ---------------------------------------------------------------------------
# verify_otp
# ---------------------------------------------------------------------------

def verify_otp(phone: str, purpose: str, entered_otp: str,
               transaction_id: str = None) -> dict:
    phone   = normalize_phone(phone)
    entered = str(entered_otp).strip()

    all_otps = OtpCode.filter_by(phone=phone, purpose=purpose, used=False).all()
    matching = [o for o in all_otps if o.transaction_id == transaction_id]
    matching.sort(key=lambda o: o.created_at or utcnow(), reverse=True)
    record = matching[0] if matching else None

    now = utcnow()

    if record:
        print(
            f'[PayVanta OTP verify] phone={phone} purpose={purpose} '
            f'txn={transaction_id} record_id={record.id} '
            f'expires_at={record.expires_at} now={now} '
            f'attempts={record.attempts} used={record.used} '
            f'entered_len={len(entered)}'
        )
    else:
        print(f'[PayVanta OTP verify] phone={phone} purpose={purpose} '
              f'txn={transaction_id} reason=no_otp_found')

    if not record:
        return {
            'success': False,
            'reason': 'no_otp_found',
            'message': 'No OTP found. Please request a new one.',
        }

    if now > record.expires_at:
        record.used = True
        record.save()
        print(f'[PayVanta OTP verify] reason=expired')
        return {
            'success': False,
            'reason': 'expired',
            'message': 'OTP has expired. Please click Resend OTP.',
        }

    max_attempts = current_app.config.get('OTP_MAX_ATTEMPTS', 3)
    if record.attempts >= max_attempts:
        record.used = True
        record.save()
        print(f'[PayVanta OTP verify] reason=too_many_attempts')
        return {
            'success': False,
            'reason': 'too_many_attempts',
            'message': 'Too many wrong attempts. Please request a new OTP.',
        }

    if not check_password_hash(record.otp_hash, entered):
        record.attempts += 1
        record.save()
        remaining = max_attempts - record.attempts
        print(f'[PayVanta OTP verify] reason=wrong_code attempts={record.attempts} remaining={remaining}')
        if remaining <= 0:
            record.used = True
            record.save()
            return {
                'success': False,
                'reason': 'too_many_attempts',
                'message': 'Too many wrong attempts. Please request a new OTP.',
            }
        return {
            'success': False,
            'reason': 'wrong_code',
            'message': f'Incorrect OTP. {remaining} attempt(s) remaining.',
        }

    record.used = True
    record.save()
    print(f'[PayVanta OTP verify] reason=ok phone={phone} purpose={purpose}')
    return {'success': True, 'reason': 'ok', 'message': 'OTP verified.'}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def mask_phone(phone: str) -> str:
    phone = normalize_phone(phone)
    if len(phone) >= 10:
        visible = phone[-4:]
        return phone[:3] + ' XXXXXX' + visible
    return phone
