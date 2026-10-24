"""
models.py – Local JSON file storage (replaces Firebase Firestore).

Data is stored in  data/<collection>.json  files next to this file.
No Firebase, no cloud keys required.  Works fully offline.

Collections
-----------
  users                     ← User
  accounts                  ← Account
  beneficiaries             ← Beneficiary
  transactions              ← Transaction
  verifications             ← Verification
  risk_assessments          ← RiskAssessment
  audit_logs                ← AuditLog
  otp_codes                 ← OtpCode
  pending_registrations     ← PendingRegistration
  bank_officer_applications ← BankOfficerApplication
  notifications             ← Notification
  blacklist                 ← Blacklist
  bank_approvals            ← BankApproval
  call_logs                 ← CallLog
"""

import uuid, json, os, threading
from datetime import datetime, timezone
from werkzeug.security import generate_password_hash, check_password_hash

# ── helpers ───────────────────────────────────────────────────────────────────

def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)

def gen_uuid():
    return str(uuid.uuid4())

# ── JSON file storage ─────────────────────────────────────────────────────────

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
_locks: dict[str, threading.Lock] = {}

def _lock(collection: str) -> threading.Lock:
    if collection not in _locks:
        _locks[collection] = threading.Lock()
    return _locks[collection]

def _path(collection: str) -> str:
    os.makedirs(_DATA_DIR, exist_ok=True)
    return os.path.join(_DATA_DIR, f'{collection}.json')

def _dt_to_str(v):
    """Serialize datetime → ISO string for JSON."""
    if isinstance(v, datetime):
        return {'__dt__': v.isoformat()}
    if isinstance(v, dict):
        return {k: _dt_to_str(val) for k, val in v.items()}
    if isinstance(v, list):
        return [_dt_to_str(i) for i in v]
    return v

def _str_to_dt(v):
    """Deserialize ISO string → naive UTC datetime."""
    if isinstance(v, dict):
        if '__dt__' in v:
            dt = datetime.fromisoformat(v['__dt__'])
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        return {k: _str_to_dt(val) for k, val in v.items()}
    if isinstance(v, list):
        return [_str_to_dt(i) for i in v]
    return v

def _load(collection: str) -> dict:
    p = _path(collection)
    if not os.path.exists(p):
        return {}
    with open(p, 'r', encoding='utf-8') as f:
        raw = json.load(f)
    return {k: _str_to_dt(v) for k, v in raw.items()}

def _save_store(collection: str, store: dict):
    p = _path(collection)
    with open(p, 'w', encoding='utf-8') as f:
        json.dump({k: _dt_to_str(v) for k, v in store.items()},
                  f, indent=2, ensure_ascii=False)

def _put(collection: str, doc_id: str, data: dict):
    with _lock(collection):
        store = _load(collection)
        store[doc_id] = data
        _save_store(collection, store)

def _remove(collection: str, doc_id: str):
    with _lock(collection):
        store = _load(collection)
        store.pop(doc_id, None)
        _save_store(collection, store)

def _get_doc(collection: str, doc_id: str):
    with _lock(collection):
        return _load(collection).get(doc_id)

def _query(collection: str, filters: dict) -> list[dict]:
    with _lock(collection):
        store = _load(collection)
    results = []
    for doc in store.values():
        if all(doc.get(k) == v for k, v in filters.items()):
            results.append(doc)
    return results


# ── Compatibility shim ────────────────────────────────────────────────────────

class _Session:
    def add(self, obj):    obj.save()
    def delete(self, obj): obj.delete()
    def commit(self):      pass
    def flush(self):       pass
    def rollback(self):    pass

class _DB:
    session = _Session()
    def init_app(self, app): pass
    def create_all(self):    pass

db = _DB()


# ── Generic query object ──────────────────────────────────────────────────────

class _GenericQuery:
    def __init__(self, model_cls, filters):
        self._cls     = model_cls
        self._filters = dict(filters)
        self._limit_n = None

    def filter_by(self, **kwargs):
        self._filters.update(kwargs)
        return self

    def filter(self, *conditions):
        # SQLAlchemy-style conditions are ignored; use filter_by for key=val
        return self

    def order_by(self, *args):
        return self

    def limit(self, n):
        self._limit_n = n
        return self

    def count(self):
        return len(self._run())

    def first(self):
        r = self._run()
        return r[0] if r else None

    def first_or_404(self):
        obj = self.first()
        if not obj:
            from flask import abort
            abort(404)
        return obj

    def all(self):
        return self._run()

    def _run(self):
        docs = _query(self._cls.COLLECTION, self._filters)
        results = [self._cls._from_doc(d) for d in docs]
        results = [r for r in results if r is not None]
        if self._limit_n:
            results = results[:self._limit_n]
        return results


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  USER                                                                   ║
# ╚══════════════════════════════════════════════════════════════════════════╝

class User:
    COLLECTION = 'users'

    def __init__(self, name='', phone='', email='', password_hash='',
                 role='USER', kyc_status='PENDING', kyc_pan=None,
                 kyc_aadhaar_last4=None, kyc_address=None,
                 is_active=True, created_at=None, user_id=None):
        self.user_id           = user_id or gen_uuid()
        self.name              = name
        self.phone             = phone
        self.email             = email
        self.password_hash     = password_hash
        self.role              = role
        self.kyc_status        = kyc_status
        self.kyc_pan           = kyc_pan
        self.kyc_aadhaar_last4 = kyc_aadhaar_last4
        self.kyc_address       = kyc_address
        self.is_active         = is_active
        self.created_at        = created_at or utcnow()

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)

    def save(self):
        _put(self.COLLECTION, self.user_id, self._to_doc())

    def delete(self):
        _remove(self.COLLECTION, self.user_id)

    def _to_doc(self):
        return {
            'user_id':           self.user_id,
            'name':              self.name,
            'phone':             self.phone,
            'email':             self.email,
            'password_hash':     self.password_hash,
            'role':              self.role,
            'kyc_status':        self.kyc_status,
            'kyc_pan':           self.kyc_pan,
            'kyc_aadhaar_last4': self.kyc_aadhaar_last4,
            'kyc_address':       self.kyc_address,
            'is_active':         self.is_active,
            'created_at':        self.created_at,
        }

    @classmethod
    def _from_doc(cls, data):
        if not data:
            return None
        obj = cls.__new__(cls)
        obj.user_id           = data.get('user_id')
        obj.name              = data.get('name', '')
        obj.phone             = data.get('phone', '')
        obj.email             = data.get('email', '')
        obj.password_hash     = data.get('password_hash', '')
        obj.role              = data.get('role', 'USER')
        obj.kyc_status        = data.get('kyc_status', 'PENDING')
        obj.kyc_pan           = data.get('kyc_pan')
        obj.kyc_aadhaar_last4 = data.get('kyc_aadhaar_last4')
        obj.kyc_address       = data.get('kyc_address')
        obj.is_active         = data.get('is_active', True)
        obj.created_at        = data.get('created_at', utcnow())
        return obj

    @classmethod
    def get(cls, user_id):
        if not user_id:
            return None
        d = _get_doc(cls.COLLECTION, str(user_id))
        return cls._from_doc(d)

    @classmethod
    def get_or_404(cls, user_id):
        obj = cls.get(user_id)
        if not obj:
            from flask import abort; abort(404)
        return obj

    @classmethod
    def filter_by(cls, **kwargs):
        return _GenericQuery(cls, kwargs)

    @classmethod
    def query_all(cls):
        return _GenericQuery(cls, {})


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  ACCOUNT                                                                ║
# ╚══════════════════════════════════════════════════════════════════════════╝

class Account:
    COLLECTION = 'accounts'

    def __init__(self, user_id='', bank_name='PayVanta Bank',
                 account_number='', ifsc='PVNT0001001',
                 balance=1_000_000.0, status='ACTIVE',
                 created_at=None, account_id=None):
        self.account_id     = account_id or gen_uuid()
        self.user_id        = user_id
        self.bank_name      = bank_name
        self.account_number = account_number
        self.ifsc           = ifsc
        self.balance        = balance
        self.status         = status
        self.created_at     = created_at or utcnow()

    def save(self):
        _put(self.COLLECTION, self.account_id, self._to_doc())

    def delete(self):
        _remove(self.COLLECTION, self.account_id)

    def _to_doc(self):
        return {
            'account_id':     self.account_id,
            'user_id':        self.user_id,
            'bank_name':      self.bank_name,
            'account_number': self.account_number,
            'ifsc':           self.ifsc,
            'balance':        self.balance,
            'status':         self.status,
            'created_at':     self.created_at,
        }

    @classmethod
    def _from_doc(cls, data):
        if not data:
            return None
        obj = cls.__new__(cls)
        obj.account_id     = data.get('account_id')
        obj.user_id        = data.get('user_id', '')
        obj.bank_name      = data.get('bank_name', 'PayVanta Bank')
        obj.account_number = data.get('account_number', '')
        obj.ifsc           = data.get('ifsc', 'PVNT0001001')
        obj.balance        = data.get('balance', 0.0)
        obj.status         = data.get('status', 'ACTIVE')
        obj.created_at     = data.get('created_at', utcnow())
        return obj

    @classmethod
    def get(cls, account_id):
        if not account_id:
            return None
        d = _get_doc(cls.COLLECTION, str(account_id))
        return cls._from_doc(d)

    @classmethod
    def get_or_404(cls, account_id):
        obj = cls.get(account_id)
        if not obj:
            from flask import abort; abort(404)
        return obj

    @classmethod
    def filter_by(cls, **kwargs):
        return _GenericQuery(cls, kwargs)


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  BENEFICIARY                                                            ║
# ╚══════════════════════════════════════════════════════════════════════════╝

class Beneficiary:
    COLLECTION = 'beneficiaries'

    def __init__(self, user_id='', name='', account_number='',
                 ifsc='', bank_name='', status='ACTIVE',
                 created_at=None, id=None):
        self.id             = id or gen_uuid()
        self.user_id        = user_id
        self.name           = name
        self.account_number = account_number
        self.ifsc           = ifsc
        self.bank_name      = bank_name
        self.status         = status
        self.created_at     = created_at or utcnow()

    def save(self):
        _put(self.COLLECTION, str(self.id), self._to_doc())

    def delete(self):
        _remove(self.COLLECTION, str(self.id))

    def _to_doc(self):
        return {
            'id':             str(self.id),
            'user_id':        self.user_id,
            'name':           self.name,
            'account_number': self.account_number,
            'ifsc':           self.ifsc,
            'bank_name':      self.bank_name,
            'status':         self.status,
            'created_at':     self.created_at,
        }

    @classmethod
    def _from_doc(cls, data):
        if not data:
            return None
        obj = cls.__new__(cls)
        obj.id             = data.get('id')
        obj.user_id        = data.get('user_id', '')
        obj.name           = data.get('name', '')
        obj.account_number = data.get('account_number', '')
        obj.ifsc           = data.get('ifsc', '')
        obj.bank_name      = data.get('bank_name', '')
        obj.status         = data.get('status', 'ACTIVE')
        obj.created_at     = data.get('created_at', utcnow())
        return obj

    @classmethod
    def get(cls, id):
        if not id:
            return None
        d = _get_doc(cls.COLLECTION, str(id))
        return cls._from_doc(d)

    @classmethod
    def filter_by(cls, **kwargs):
        return _GenericQuery(cls, kwargs)


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  TRANSACTION                                                            ║
# ╚══════════════════════════════════════════════════════════════════════════╝

class Transaction:
    COLLECTION = 'transactions'

    def __init__(self, sender_acc_id='', receiver_name='', receiver_acc='',
                 receiver_ifsc='', receiver_bank='', amount=0.0, purpose='',
                 channel='RTGS', remarks='', status='PENDING_OTP',
                 reject_reason=None, bank_reference=None,
                 created_at=None, updated_at=None, transaction_id=None):
        self.transaction_id = transaction_id or gen_uuid()
        self.sender_acc_id  = sender_acc_id
        self.receiver_name  = receiver_name
        self.receiver_acc   = receiver_acc
        self.receiver_ifsc  = receiver_ifsc
        self.receiver_bank  = receiver_bank
        self.amount         = amount
        self.purpose        = purpose
        self.channel        = channel
        self.remarks        = remarks
        self.status         = status
        self.reject_reason  = reject_reason
        self.bank_reference = bank_reference
        self.created_at     = created_at or utcnow()
        self.updated_at     = updated_at or utcnow()

    @property
    def sender_account(self):
        return Account.get(self.sender_acc_id)

    def save(self):
        self.updated_at = utcnow()
        _put(self.COLLECTION, self.transaction_id, self._to_doc())

    def delete(self):
        _remove(self.COLLECTION, self.transaction_id)

    def _to_doc(self):
        return {
            'transaction_id': self.transaction_id,
            'sender_acc_id':  self.sender_acc_id,
            'receiver_name':  self.receiver_name,
            'receiver_acc':   self.receiver_acc,
            'receiver_ifsc':  self.receiver_ifsc,
            'receiver_bank':  self.receiver_bank,
            'amount':         self.amount,
            'purpose':        self.purpose,
            'channel':        self.channel,
            'remarks':        self.remarks,
            'status':         self.status,
            'reject_reason':  self.reject_reason,
            'bank_reference': self.bank_reference,
            'created_at':     self.created_at,
            'updated_at':     self.updated_at,
        }

    @classmethod
    def _from_doc(cls, data):
        if not data:
            return None
        obj = cls.__new__(cls)
        obj.transaction_id = data.get('transaction_id')
        obj.sender_acc_id  = data.get('sender_acc_id', '')
        obj.receiver_name  = data.get('receiver_name', '')
        obj.receiver_acc   = data.get('receiver_acc', '')
        obj.receiver_ifsc  = data.get('receiver_ifsc', '')
        obj.receiver_bank  = data.get('receiver_bank', '')
        obj.amount         = data.get('amount', 0.0)
        obj.purpose        = data.get('purpose', '')
        obj.channel        = data.get('channel', 'RTGS')
        obj.remarks        = data.get('remarks', '')
        obj.status         = data.get('status', 'PENDING_OTP')
        obj.reject_reason  = data.get('reject_reason')
        obj.bank_reference = data.get('bank_reference')
        obj.created_at     = data.get('created_at', utcnow())
        obj.updated_at     = data.get('updated_at', utcnow())
        return obj

    @classmethod
    def get(cls, txn_id):
        if not txn_id:
            return None
        d = _get_doc(cls.COLLECTION, str(txn_id))
        return cls._from_doc(d)

    @classmethod
    def get_or_404(cls, txn_id):
        obj = cls.get(txn_id)
        if not obj:
            from flask import abort; abort(404)
        return obj

    @classmethod
    def filter_by(cls, **kwargs):
        return _GenericQuery(cls, kwargs)

    @classmethod
    def query_all(cls):
        return _GenericQuery(cls, {})


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  VERIFICATION                                                           ║
# ╚══════════════════════════════════════════════════════════════════════════╝

class Verification:
    COLLECTION = 'verifications'

    def __init__(self, transaction_id='', party='', result='',
                 details='', created_at=None, verification_id=None):
        self.verification_id = verification_id or gen_uuid()
        self.transaction_id  = transaction_id
        self.party           = party
        self.result          = result
        self.details         = details
        self.created_at      = created_at or utcnow()

    def save(self):
        _put(self.COLLECTION, self.verification_id, self._to_doc())

    def delete(self):
        _remove(self.COLLECTION, self.verification_id)

    def _to_doc(self):
        return {
            'verification_id': self.verification_id,
            'transaction_id':  self.transaction_id,
            'party':           self.party,
            'result':          self.result,
            'details':         self.details,
            'created_at':      self.created_at,
        }

    @classmethod
    def _from_doc(cls, data):
        if not data:
            return None
        obj = cls.__new__(cls)
        obj.verification_id = data.get('verification_id')
        obj.transaction_id  = data.get('transaction_id', '')
        obj.party           = data.get('party', '')
        obj.result          = data.get('result', '')
        obj.details         = data.get('details', '')
        obj.created_at      = data.get('created_at', utcnow())
        return obj

    @classmethod
    def filter_by(cls, **kwargs):
        return _GenericQuery(cls, kwargs)


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  RISK ASSESSMENT                                                        ║
# ╚══════════════════════════════════════════════════════════════════════════╝

class RiskAssessment:
    COLLECTION = 'risk_assessments'

    def __init__(self, transaction_id='', score=0.0, level='LOW',
                 flags='[]', reasons='[]', created_at=None, risk_id=None):
        self.risk_id        = risk_id or gen_uuid()
        self.transaction_id = transaction_id
        self.score          = score
        self.level          = level
        self.flags          = flags
        self.reasons        = reasons
        self.created_at     = created_at or utcnow()

    def save(self):
        _put(self.COLLECTION, self.risk_id, self._to_doc())

    def delete(self):
        _remove(self.COLLECTION, self.risk_id)

    def _to_doc(self):
        return {
            'risk_id':        self.risk_id,
            'transaction_id': self.transaction_id,
            'score':          self.score,
            'level':          self.level,
            'flags':          self.flags,
            'reasons':        self.reasons,
            'created_at':     self.created_at,
        }

    @classmethod
    def _from_doc(cls, data):
        if not data:
            return None
        obj = cls.__new__(cls)
        obj.risk_id        = data.get('risk_id')
        obj.transaction_id = data.get('transaction_id', '')
        obj.score          = data.get('score', 0.0)
        obj.level          = data.get('level', 'LOW')
        obj.flags          = data.get('flags', '[]')
        obj.reasons        = data.get('reasons', '[]')
        obj.created_at     = data.get('created_at', utcnow())
        return obj

    @classmethod
    def filter_by(cls, **kwargs):
        return _GenericQuery(cls, kwargs)

    @classmethod
    def query_all(cls):
        return _GenericQuery(cls, {})


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  AUDIT LOG                                                              ║
# ╚══════════════════════════════════════════════════════════════════════════╝

class AuditLog:
    COLLECTION = 'audit_logs'

    def __init__(self, transaction_id=None, event_type='', actor='',
                 details='', timestamp=None, log_id=None):
        self.log_id         = log_id or gen_uuid()
        self.transaction_id = transaction_id
        self.event_type     = event_type
        self.actor          = actor
        self.timestamp      = timestamp or utcnow()
        self.details        = details

    def save(self):
        _put(self.COLLECTION, self.log_id, self._to_doc())

    def delete(self):
        _remove(self.COLLECTION, self.log_id)

    def _to_doc(self):
        return {
            'log_id':         self.log_id,
            'transaction_id': self.transaction_id,
            'event_type':     self.event_type,
            'actor':          self.actor,
            'timestamp':      self.timestamp,
            'details':        self.details,
        }

    @classmethod
    def _from_doc(cls, data):
        if not data:
            return None
        obj = cls.__new__(cls)
        obj.log_id         = data.get('log_id')
        obj.transaction_id = data.get('transaction_id')
        obj.event_type     = data.get('event_type', '')
        obj.actor          = data.get('actor', '')
        obj.timestamp      = data.get('timestamp', utcnow())
        obj.details        = data.get('details', '')
        return obj

    @classmethod
    def filter_by(cls, **kwargs):
        return _GenericQuery(cls, kwargs)

    @classmethod
    def query_all(cls):
        return _GenericQuery(cls, {})


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  OTP CODE                                                               ║
# ╚══════════════════════════════════════════════════════════════════════════╝

class OtpCode:
    COLLECTION = 'otp_codes'

    def __init__(self, phone='', purpose='', transaction_id=None,
                 otp_hash='', expires_at=None, attempts=0,
                 used=False, resend_count=0, last_sent_at=None,
                 created_at=None, id=None):
        self.id             = id or gen_uuid()
        self.phone          = phone
        self.purpose        = purpose
        self.transaction_id = transaction_id
        self.otp_hash       = otp_hash
        self.expires_at     = expires_at or utcnow()
        self.attempts       = attempts
        self.used           = used
        self.resend_count   = resend_count
        self.last_sent_at   = last_sent_at or utcnow()
        self.created_at     = created_at or utcnow()

    def save(self):
        _put(self.COLLECTION, str(self.id), self._to_doc())

    def delete(self):
        _remove(self.COLLECTION, str(self.id))

    def _to_doc(self):
        return {
            'id':             str(self.id),
            'phone':          self.phone,
            'purpose':        self.purpose,
            'transaction_id': self.transaction_id,
            'otp_hash':       self.otp_hash,
            'expires_at':     self.expires_at,
            'attempts':       self.attempts,
            'used':           self.used,
            'resend_count':   self.resend_count,
            'last_sent_at':   self.last_sent_at,
            'created_at':     self.created_at,
        }

    @classmethod
    def _from_doc(cls, data):
        if not data:
            return None
        obj = cls.__new__(cls)
        obj.id             = data.get('id')
        obj.phone          = data.get('phone', '')
        obj.purpose        = data.get('purpose', '')
        obj.transaction_id = data.get('transaction_id')
        obj.otp_hash       = data.get('otp_hash', '')
        obj.expires_at     = data.get('expires_at', utcnow())
        obj.attempts       = data.get('attempts', 0)
        obj.used           = data.get('used', False)
        obj.resend_count   = data.get('resend_count', 0)
        obj.last_sent_at   = data.get('last_sent_at', utcnow())
        obj.created_at     = data.get('created_at', utcnow())
        return obj

    @classmethod
    def filter_by(cls, **kwargs):
        return _GenericQuery(cls, kwargs)


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  PENDING REGISTRATION                                                   ║
# ╚══════════════════════════════════════════════════════════════════════════╝

class PendingRegistration:
    COLLECTION = 'pending_registrations'

    def __init__(self, name='', phone='', email='', password_hash='',
                 created_at=None, id=None):
        self.id            = id or gen_uuid()
        self.name          = name
        self.phone         = phone
        self.email         = email
        self.password_hash = password_hash
        self.created_at    = created_at or utcnow()

    def save(self):
        _put(self.COLLECTION, str(self.id), self._to_doc())

    def delete(self):
        _remove(self.COLLECTION, str(self.id))

    def _to_doc(self):
        return {
            'id':            str(self.id),
            'name':          self.name,
            'phone':         self.phone,
            'email':         self.email,
            'password_hash': self.password_hash,
            'created_at':    self.created_at,
        }

    @classmethod
    def _from_doc(cls, data):
        if not data:
            return None
        obj = cls.__new__(cls)
        obj.id            = data.get('id')
        obj.name          = data.get('name', '')
        obj.phone         = data.get('phone', '')
        obj.email         = data.get('email', '')
        obj.password_hash = data.get('password_hash', '')
        obj.created_at    = data.get('created_at', utcnow())
        return obj

    @classmethod
    def filter_by(cls, **kwargs):
        return _GenericQuery(cls, kwargs)


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  BANK OFFICER APPLICATION                                               ║
# ╚══════════════════════════════════════════════════════════════════════════╝

class BankOfficerApplication:
    COLLECTION = 'bank_officer_applications'

    def __init__(self, bank_name='', applicant_name='', phone='',
                 email='', password_hash='', status='PENDING',
                 review_reason=None, reviewed_by=None, reviewed_at=None,
                 officer_id=None, created_at=None, id=None):
        self.id             = id or gen_uuid()
        self.bank_name      = bank_name
        self.applicant_name = applicant_name
        self.phone          = phone
        self.email          = email
        self.password_hash  = password_hash
        self.status         = status
        self.review_reason  = review_reason
        self.reviewed_by    = reviewed_by
        self.reviewed_at    = reviewed_at
        self.officer_id     = officer_id
        self.created_at     = created_at or utcnow()

    def save(self):
        _put(self.COLLECTION, str(self.id), self._to_doc())

    def delete(self):
        _remove(self.COLLECTION, str(self.id))

    def _to_doc(self):
        return {
            'id':             str(self.id),
            'bank_name':      self.bank_name,
            'applicant_name': self.applicant_name,
            'phone':          self.phone,
            'email':          self.email,
            'password_hash':  self.password_hash,
            'status':         self.status,
            'review_reason':  self.review_reason,
            'reviewed_by':    self.reviewed_by,
            'reviewed_at':    self.reviewed_at,
            'officer_id':     self.officer_id,
            'created_at':     self.created_at,
        }

    @classmethod
    def _from_doc(cls, data):
        if not data:
            return None
        obj = cls.__new__(cls)
        obj.id             = data.get('id')
        obj.bank_name      = data.get('bank_name', '')
        obj.applicant_name = data.get('applicant_name', '')
        obj.phone          = data.get('phone', '')
        obj.email          = data.get('email', '')
        obj.password_hash  = data.get('password_hash', '')
        obj.status         = data.get('status', 'PENDING')
        obj.review_reason  = data.get('review_reason')
        obj.reviewed_by    = data.get('reviewed_by')
        obj.reviewed_at    = data.get('reviewed_at')
        obj.officer_id     = data.get('officer_id')
        obj.created_at     = data.get('created_at', utcnow())
        return obj

    @classmethod
    def get(cls, id):
        if not id:
            return None
        d = _get_doc(cls.COLLECTION, str(id))
        return cls._from_doc(d)

    @classmethod
    def get_or_404(cls, id):
        obj = cls.get(id)
        if not obj:
            from flask import abort; abort(404)
        return obj

    @classmethod
    def filter_by(cls, **kwargs):
        return _GenericQuery(cls, kwargs)

    @classmethod
    def query_all(cls):
        return _GenericQuery(cls, {})


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  NOTIFICATION                                                           ║
# ╚══════════════════════════════════════════════════════════════════════════╝

class Notification:
    COLLECTION = 'notifications'

    def __init__(self, user_id='', message='', is_read=False,
                 created_at=None, id=None):
        self.id         = id or gen_uuid()
        self.user_id    = user_id
        self.message    = message
        self.is_read    = is_read
        self.created_at = created_at or utcnow()

    def save(self):
        _put(self.COLLECTION, str(self.id), self._to_doc())

    def delete(self):
        _remove(self.COLLECTION, str(self.id))

    def _to_doc(self):
        return {
            'id':         str(self.id),
            'user_id':    self.user_id,
            'message':    self.message,
            'is_read':    self.is_read,
            'created_at': self.created_at,
        }

    @classmethod
    def _from_doc(cls, data):
        if not data:
            return None
        obj = cls.__new__(cls)
        obj.id         = data.get('id')
        obj.user_id    = data.get('user_id', '')
        obj.message    = data.get('message', '')
        obj.is_read    = data.get('is_read', False)
        obj.created_at = data.get('created_at', utcnow())
        return obj

    @classmethod
    def filter_by(cls, **kwargs):
        return _GenericQuery(cls, kwargs)


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  BLACKLIST                                                              ║
# ╚══════════════════════════════════════════════════════════════════════════╝

class Blacklist:
    COLLECTION = 'blacklist'

    def __init__(self, account_number='', reason='', added_by='',
                 created_at=None, id=None):
        self.id             = id or gen_uuid()
        self.account_number = account_number
        self.reason         = reason
        self.added_by       = added_by
        self.created_at     = created_at or utcnow()

    def save(self):
        _put(self.COLLECTION, str(self.id), self._to_doc())

    def delete(self):
        _remove(self.COLLECTION, str(self.id))

    def _to_doc(self):
        return {
            'id':             str(self.id),
            'account_number': self.account_number,
            'reason':         self.reason,
            'added_by':       self.added_by,
            'created_at':     self.created_at,
        }

    @classmethod
    def _from_doc(cls, data):
        if not data:
            return None
        obj = cls.__new__(cls)
        obj.id             = data.get('id')
        obj.account_number = data.get('account_number', '')
        obj.reason         = data.get('reason', '')
        obj.added_by       = data.get('added_by', '')
        obj.created_at     = data.get('created_at', utcnow())
        return obj

    @classmethod
    def get(cls, id):
        if not id:
            return None
        d = _get_doc(cls.COLLECTION, str(id))
        return cls._from_doc(d)

    @classmethod
    def filter_by(cls, **kwargs):
        return _GenericQuery(cls, kwargs)

    @classmethod
    def query_all(cls):
        return _GenericQuery(cls, {})


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  BANK APPROVAL                                                          ║
# ╚══════════════════════════════════════════════════════════════════════════╝

class BankApproval:
    COLLECTION = 'bank_approvals'

    def __init__(self, transaction_id='', status='PENDING', forwarded_by=None,
                 forwarded_at=None, officer_id=None, officer_name=None,
                 remarks=None, decided_at=None, created_at=None, id=None):
        self.id             = id or gen_uuid()
        self.transaction_id = transaction_id
        self.status         = status
        self.forwarded_by   = forwarded_by
        self.forwarded_at   = forwarded_at
        self.officer_id     = officer_id
        self.officer_name   = officer_name
        self.remarks        = remarks
        self.decided_at     = decided_at
        self.created_at     = created_at or utcnow()

    @property
    def transaction(self):
        return Transaction.get(self.transaction_id)

    def save(self):
        _put(self.COLLECTION, str(self.id), self._to_doc())

    def delete(self):
        _remove(self.COLLECTION, str(self.id))

    def _to_doc(self):
        return {
            'id':             str(self.id),
            'transaction_id': self.transaction_id,
            'status':         self.status,
            'forwarded_by':   self.forwarded_by,
            'forwarded_at':   self.forwarded_at,
            'officer_id':     self.officer_id,
            'officer_name':   self.officer_name,
            'remarks':        self.remarks,
            'decided_at':     self.decided_at,
            'created_at':     self.created_at,
        }

    @classmethod
    def _from_doc(cls, data):
        if not data:
            return None
        obj = cls.__new__(cls)
        obj.id             = data.get('id')
        obj.transaction_id = data.get('transaction_id', '')
        obj.status         = data.get('status', 'PENDING')
        obj.forwarded_by   = data.get('forwarded_by')
        obj.forwarded_at   = data.get('forwarded_at')
        obj.officer_id     = data.get('officer_id')
        obj.officer_name   = data.get('officer_name')
        obj.remarks        = data.get('remarks')
        obj.decided_at     = data.get('decided_at')
        obj.created_at     = data.get('created_at', utcnow())
        return obj

    @classmethod
    def get(cls, id):
        if not id:
            return None
        d = _get_doc(cls.COLLECTION, str(id))
        return cls._from_doc(d)

    @classmethod
    def get_or_404(cls, id):
        obj = cls.get(id)
        if not obj:
            from flask import abort; abort(404)
        return obj

    @classmethod
    def filter_by(cls, **kwargs):
        return _GenericQuery(cls, kwargs)

    @classmethod
    def query_all(cls):
        return _GenericQuery(cls, {})


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  CALL LOG                                                               ║
# ╚══════════════════════════════════════════════════════════════════════════╝

class CallLog:
    COLLECTION = 'call_logs'

    def __init__(self, transaction_id='', user_id='', phone='',
                 message='', status='PLACED', created_at=None, id=None):
        self.id             = id or gen_uuid()
        self.transaction_id = transaction_id
        self.user_id        = user_id
        self.phone          = phone
        self.message        = message
        self.status         = status
        self.created_at     = created_at or utcnow()

    def save(self):
        _put(self.COLLECTION, str(self.id), self._to_doc())

    def delete(self):
        _remove(self.COLLECTION, str(self.id))

    def _to_doc(self):
        return {
            'id':             str(self.id),
            'transaction_id': self.transaction_id,
            'user_id':        self.user_id,
            'phone':          self.phone,
            'message':        self.message,
            'status':         self.status,
            'created_at':     self.created_at,
        }

    @classmethod
    def _from_doc(cls, data):
        if not data:
            return None
        obj = cls.__new__(cls)
        obj.id             = data.get('id')
        obj.transaction_id = data.get('transaction_id', '')
        obj.user_id        = data.get('user_id', '')
        obj.phone          = data.get('phone', '')
        obj.message        = data.get('message', '')
        obj.status         = data.get('status', 'PLACED')
        obj.created_at     = data.get('created_at', utcnow())
        return obj

    @classmethod
    def filter_by(cls, **kwargs):
        return _GenericQuery(cls, kwargs)
