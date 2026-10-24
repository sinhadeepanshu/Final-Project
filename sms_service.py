"""
Local OTP delivery for development and testing.
"""
import os
import re

def normalize_phone(phone: str) -> str:
    """Convert 10-digit Indian number to E.164 (+91XXXXXXXXXX)."""
    phone = re.sub(r'\s+', '', phone)
    if phone.startswith('+'):
        return phone
    digits = re.sub(r'\D', '', phone)
    if len(digits) == 10:
        return f'+91{digits}'
    if len(digits) == 12 and digits.startswith('91'):
        return f'+{digits}'
    return phone


def send_otp_sms(phone: str, otp: str, message_body: str = '',
                 purpose: str = '', user_name: str = '',
                 expires: str = '') -> bool:
    """
    Send OTP via Twilio if credentials are configured, otherwise print to terminal.

    New keyword args (purpose, user_name, expires) make the terminal output
    match the required format:
        [PayVanta OTP] user=<name> purpose=<purpose> code=<123456> expires=<time>
    """
    phone = normalize_phone(phone)

    twilio_sid    = os.environ.get('TWILIO_ACCOUNT_SID', '').strip()
    twilio_token  = os.environ.get('TWILIO_AUTH_TOKEN', '').strip()
    twilio_from   = os.environ.get('TWILIO_PHONE_NUMBER', '').strip()

    sms_sent = False
    if twilio_sid and twilio_token and twilio_from:
        try:
            from twilio.rest import Client  # type: ignore
            client = Client(twilio_sid, twilio_token)
            body = message_body or f'PayVanta OTP: {otp}. Valid 5 minutes. Do not share.'
            client.messages.create(body=body, from_=twilio_from, to=phone)
            sms_sent = True
        except Exception as exc:
            print(f'[PayVanta SMS] Twilio send failed ({exc}); falling back to terminal.')

    dev_mode = os.environ.get('DEV_MODE', 'false').strip().lower() == 'true'
    if dev_mode:
        label = f'[PayVanta OTP] user={user_name or phone} purpose={purpose or "unknown"} code={otp}'
        if expires:
            label += f' expires={expires}'
        print(label)

    if not sms_sent and dev_mode:
        # Extra box for local testing clarity
        lines = [
            'PAYVANTA OTP - LOCAL TESTING',
            f'Phone  : {phone}',
            f'Purpose: {purpose or "unknown"}',
            f'OTP    : {otp}',
            'SMS delivery disabled; use this code.',
        ]
        width = max(len(line) for line in lines)
        border = '+' + '-' * (width + 2) + '+'
        print(f'\n{border}')
        for line in lines:
            print(f'| {line.ljust(width)} |')
        print(f'{border}\n')

    return True
