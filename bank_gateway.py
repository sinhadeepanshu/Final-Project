import time
import random
import string
from datetime import datetime


def _generate_reference() -> str:
    today = datetime.now().strftime('%Y%m%d')
    suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f'PVB{today}{suffix}'


def execute_transfer(transaction) -> dict:
    """
    Simulate a bank transfer.
    Returns {'success': bool, 'reference': str, 'message': str}
    """
    # Simulate network delay (0.3–0.8 seconds)
    time.sleep(random.uniform(0.3, 0.8))

    # ~3% failure rate
    if random.random() < 0.03:
        return {
            'success': False,
            'reference': None,
            'message': 'Bank gateway timeout. Transaction failed. Please retry.',
        }

    ref = _generate_reference()
    return {
        'success': True,
        'reference': ref,
        'message': f'Transfer executed successfully. Reference: {ref}',
    }
