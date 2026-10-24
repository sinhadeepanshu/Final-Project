"""
fraud_model.py – AI-based fraud detection using Isolation Forest.
Trains on synthetic data on first run, then persists model with joblib.
"""
import os
import json
import logging
import tempfile
import numpy as np
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)
if os.environ.get('VERCEL'):
    base_dir = tempfile.gettempdir()
else:
    base_dir = os.path.dirname(__file__)
os.makedirs(base_dir, exist_ok=True)
MODEL_PATH = os.path.join(base_dir, 'fraud_model.joblib')
SCALER_PATH = os.path.join(base_dir, 'fraud_scaler.joblib')


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _train_model():
    """Generate synthetic data and train Isolation Forest."""
    import pandas as pd
    import joblib
    from sklearn.ensemble import IsolationForest
    from sklearn.preprocessing import StandardScaler

    logger.info("Training fraud detection model on synthetic data…")
    np.random.seed(42)
    n = 2000

    # Normal transactions (80%)
    normal_n = int(n * 0.8)
    fraud_n  = n - normal_n

    normal = pd.DataFrame({
        'amount':           np.random.uniform(200_000, 800_000, normal_n),
        'hour':             np.random.randint(8, 22, normal_n),
        'txns_24h':         np.random.randint(0, 4, normal_n),
        'avg_past_amount':  np.random.uniform(200_000, 600_000, normal_n),
        'new_beneficiary':  np.random.binomial(1, 0.2, normal_n),
        'account_age_days': np.random.randint(90, 1500, normal_n),
    })
    # Anomalous (fraud-like)
    fraud = pd.DataFrame({
        'amount':           np.random.uniform(4_000_000, 10_000_000, fraud_n),
        'hour':             np.random.choice([0,1,2,3,23], fraud_n),
        'txns_24h':         np.random.randint(5, 20, fraud_n),
        'avg_past_amount':  np.random.uniform(100_000, 300_000, fraud_n),
        'new_beneficiary':  np.ones(fraud_n, dtype=int),
        'account_age_days': np.random.randint(1, 30, fraud_n),
    })

    df = pd.concat([normal, fraud], ignore_index=True).sample(frac=1, random_state=42)
    X  = df[['amount','hour','txns_24h','avg_past_amount','new_beneficiary','account_age_days']].values

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    model = IsolationForest(n_estimators=200, contamination=0.10, random_state=42)
    model.fit(X_scaled)

    joblib.dump(model,  MODEL_PATH)
    joblib.dump(scaler, SCALER_PATH)
    logger.info("Fraud model trained and saved.")
    return model, scaler


def _load_model():
    import joblib
    if os.path.exists(MODEL_PATH) and os.path.exists(SCALER_PATH):
        return joblib.load(MODEL_PATH), joblib.load(SCALER_PATH)
    return _train_model()


def _compute_features(transaction, sender_account) -> dict:
    """Compute real features from transaction + history (Firestore)."""
    from models import Transaction as Txn, User

    now   = utcnow()
    hour  = now.hour
    since = now - timedelta(hours=24)

    # All transactions for this account
    all_txns = Txn.filter_by(sender_acc_id=transaction.sender_acc_id).all()

    # Transactions in last 24h (relevant statuses)
    recent_statuses = {'EXECUTED', 'PENDING_ADMIN_APPROVAL', 'VERIFYING'}
    recent = [t for t in all_txns
              if t.status in recent_statuses and t.created_at and t.created_at >= since]
    txns_24h = len(recent)

    # Average past amount (executed only)
    past = [t for t in all_txns if t.status == 'EXECUTED']
    avg_past = np.mean([t.amount for t in past]) if past else transaction.amount

    # New beneficiary flag
    past_receivers = {t.receiver_acc for t in past}
    new_beneficiary = 1 if transaction.receiver_acc not in past_receivers else 0

    # Account age
    user = User.get(sender_account.user_id)
    age_days = (now - user.created_at).days if user else 365

    return {
        'amount':           transaction.amount,
        'hour':             hour,
        'txns_24h':         txns_24h,
        'avg_past_amount':  avg_past,
        'new_beneficiary':  new_beneficiary,
        'account_age_days': age_days,
        '_avg_past':        avg_past,  # for rule evaluation
    }


def assess_risk(transaction, sender_account) -> dict:
    """
    Run AI + rule-based fraud assessment.
    Returns {'score': 0-100, 'level': str, 'flags': list, 'reasons': list}
    """
    try:
        model, scaler = _load_model()
    except Exception as e:
        logger.error("Could not load fraud model: %s", e)
        return {'score': 50, 'level': 'MEDIUM', 'flags': ['model_error'], 'reasons': ['AI model unavailable; manual review required.']}

    features = _compute_features(transaction, sender_account)
    X = np.array([[
        features['amount'],
        features['hour'],
        features['txns_24h'],
        features['avg_past_amount'],
        features['new_beneficiary'],
        features['account_age_days'],
    ]])
    X_scaled = scaler.transform(X)
    score_raw = model.decision_function(X_scaled)[0]  # negative = more anomalous

    # Map to 0–100 (higher = more risky)
    # Isolation Forest scores typically range from ~ -0.5 to +0.5
    ai_score = max(0, min(100, int((0.5 - score_raw) * 100)))

    # ── Rule-based flags ────────────────────────────────────────────────
    flags   = []
    reasons = []
    avg     = features['_avg_past']

    if avg > 0 and transaction.amount > avg * 5:
        ratio = transaction.amount / avg
        flags.append('high_amount_vs_avg')
        reasons.append(f'Amount is {ratio:.1f}x above your average transaction.')

    if features['new_beneficiary'] and transaction.amount > 500_000:
        flags.append('new_bene_high_amount')
        reasons.append('First transfer to this beneficiary with a high amount.')

    if features['hour'] in [0, 1, 2, 3, 23]:
        flags.append('late_night')
        reasons.append('Transaction initiated in late-night hours.')

    if features['txns_24h'] >= 5:
        flags.append('high_velocity')
        reasons.append(f'{features["txns_24h"]} transactions in the last 24 hours.')

    if transaction.amount % 100_000 == 0:
        flags.append('round_amount')
        reasons.append('Round-number amount is a common fraud pattern.')

    if features['account_age_days'] < 30:
        flags.append('new_account')
        reasons.append('Account is less than 30 days old.')

    # Blend AI score with rule count
    rule_boost = len(flags) * 8
    final_score = min(100, ai_score + rule_boost)

    if final_score >= 70:
        level = 'HIGH'
    elif final_score >= 40:
        level = 'MEDIUM'
    else:
        level = 'LOW'

    return {
        'score':   final_score,
        'level':   level,
        'flags':   flags,
        'reasons': reasons,
    }
