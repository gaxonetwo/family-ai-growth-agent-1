"""Generate fully synthetic Family AI product-operations data."""
from __future__ import annotations

import argparse
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw"
GT_DIR = ROOT / "data" / "ground_truth"

SCENARIOS = [
    ("healthy", 0.48),
    ("onboarding_dropoff", 0.16),
    ("device_issue", 0.12),
    ("low_feature_discovery", 0.10),
    ("churn_risk", 0.14),
]

FEEDBACK_BANK = {
    "device_connection": [
        "设备连接偶尔中断，希望连接过程更稳定。",
        "设备有时显示离线，重新连接后恢复。",
        "配网步骤不太顺畅，希望提供更清晰的指引。",
    ],
    "onboarding": [
        "第一次使用时不太清楚从哪里开始。",
        "希望增加更直观的新手引导。",
        "完成注册后，不知道下一步应该使用什么功能。",
    ],
    "feature_discovery": [
        "没有注意到部分功能入口。",
        "希望在首页更容易找到常用功能。",
        "功能介绍不够明显，需要更多使用提示。",
    ],
    "performance": [
        "页面加载有时比较慢。",
        "部分操作响应时间较长。",
        "希望优化应用运行流畅度。",
    ],
    "positive": [
        "整体使用体验不错，常用功能比较方便。",
        "最近的功能更新比较实用。",
        "操作流程清晰，使用起来比较顺手。",
    ],
}

def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()

def random_time(start: datetime, end: datetime) -> datetime:
    if end <= start:
        return start
    seconds = int((end - start).total_seconds())
    return start + timedelta(seconds=random.randint(0, max(seconds, 1)))

def weighted_choice(items):
    labels, weights = zip(*items)
    return random.choices(labels, weights=weights, k=1)[0]

def generate(users_n: int = 1000, days: int = 90, seed: int = 42, end_date: str = "2026-11-13"):
    random.seed(seed)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    GT_DIR.mkdir(parents=True, exist_ok=True)

    end = datetime.fromisoformat(end_date).replace(tzinfo=timezone.utc, hour=23, minute=59, second=0)
    start_floor = end - timedelta(days=days - 1)

    users, devices, events, feedback, payments, ground_truth, feedback_labels = [], [], [], [], [], [], []
    event_counter = feedback_counter = payment_counter = 0

    for i in range(1, users_n + 1):
        uid = f"U{i:06d}"
        scenario = weighted_choice(SCENARIOS)
        signup = random_time(start_floor, end - timedelta(days=1))
        channel = weighted_choice([
            ("organic", 0.40), ("social", 0.25), ("referral", 0.20), ("paid_ads", 0.15)
        ])
        region = weighted_choice([
            ("east", 0.30), ("south", 0.25), ("north", 0.20), ("west", 0.15), ("central", 0.10)
        ])

        bind_prob = 0.82 if scenario != "onboarding_dropoff" else 0.58
        has_device = random.random() < bind_prob
        device_id = f"D{i:06d}" if has_device else None
        bound_at = random_time(signup, min(signup + timedelta(days=3), end)) if has_device else None

        onboarding_prob = {
            "healthy": 0.94, "onboarding_dropoff": 0.38, "device_issue": 0.88,
            "low_feature_discovery": 0.90, "churn_risk": 0.86
        }[scenario]
        onboarding_done = random.random() < onboarding_prob

        premium_prob = {
            "healthy": 0.16, "onboarding_dropoff": 0.025, "device_issue": 0.07,
            "low_feature_discovery": 0.08, "churn_risk": 0.045
        }[scenario]
        plan = "premium" if random.random() < premium_prob else "free"

        users.append({
            "user_id": uid, "signup_at": iso(signup),
            "acquisition_channel": channel, "region": region, "plan_type": plan
        })
        ground_truth.append({"user_id": uid, "scenario_group": scenario})

        if has_device:
            device_type = weighted_choice([("family_hub", 0.65), ("smart_speaker", 0.35)])
            offline_now = scenario == "device_issue" and random.random() < 0.38
            last_seen = random_time(max(bound_at, end - timedelta(days=5)), end) if not offline_now else random_time(bound_at, end - timedelta(days=2))
            devices.append({
                "device_id": device_id, "user_id": uid, "device_type": device_type,
                "bound_at": iso(bound_at), "current_status": "offline" if offline_now else "online",
                "last_seen_at": iso(last_seen), "firmware_version": random.choice(["1.2.0", "1.2.1", "1.3.0"])
            })
            event_counter += 1
            events.append({
                "event_id": f"E{event_counter:08d}", "user_id": uid, "device_id": device_id,
                "event_time": iso(bound_at), "event_name": "device_bind",
                "feature_name": None, "session_id": None
            })

        # Onboarding events
        event_counter += 1
        events.append({
            "event_id": f"E{event_counter:08d}", "user_id": uid, "device_id": device_id,
            "event_time": iso(signup + timedelta(minutes=random.randint(1, 30))),
            "event_name": "onboarding_start", "feature_name": None, "session_id": f"S{i:07d}A"
        })
        if onboarding_done:
            event_counter += 1
            onboarding_complete_at = min(signup + timedelta(hours=random.randint(1, 36)), end)
            events.append({
                "event_id": f"E{event_counter:08d}", "user_id": uid, "device_id": device_id,
                "event_time": iso(onboarding_complete_at),
                "event_name": "onboarding_complete", "feature_name": None, "session_id": f"S{i:07d}A"
            })

        # Daily behavior. Each scenario has a different synthetic behavior pattern.
        day = signup.date()
        final_day = end.date()
        while day <= final_day:
            day_dt = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
            age_days = (day - signup.date()).days
            app_prob = {
                "healthy": 0.42, "onboarding_dropoff": 0.10, "device_issue": 0.27,
                "low_feature_discovery": 0.34, "churn_risk": (0.42 if age_days < 18 else 0.12)
            }[scenario]
            if random.random() < app_prob:
                session = f"S{i:07d}{day.strftime('%j')}"
                event_counter += 1
                events.append({
                    "event_id": f"E{event_counter:08d}", "user_id": uid, "device_id": device_id,
                    "event_time": iso(day_dt + timedelta(hours=random.randint(6, 22), minutes=random.randint(0, 59))),
                    "event_name": "app_open", "feature_name": None, "session_id": session
                })

                ai_prob = {
                    "healthy": 0.72, "onboarding_dropoff": 0.08, "device_issue": 0.42,
                    "low_feature_discovery": 0.52, "churn_risk": (0.70 if age_days < 18 else 0.18)
                }[scenario]
                if onboarding_done and random.random() < ai_prob:
                    event_counter += 1
                    events.append({
                        "event_id": f"E{event_counter:08d}", "user_id": uid, "device_id": device_id,
                        "event_time": iso(day_dt + timedelta(hours=random.randint(6, 22), minutes=random.randint(0, 59))),
                        "event_name": "ai_interaction", "feature_name": "assistant_chat", "session_id": session
                    })

                feature_prob = {
                    "healthy": 0.48, "onboarding_dropoff": 0.04, "device_issue": 0.22,
                    "low_feature_discovery": 0.08, "churn_risk": (0.42 if age_days < 18 else 0.08)
                }[scenario]
                if random.random() < feature_prob:
                    event_counter += 1
                    feature = random.choice(["routine_planner", "family_reminder", "knowledge_helper"])
                    events.append({
                        "event_id": f"E{event_counter:08d}", "user_id": uid, "device_id": device_id,
                        "event_time": iso(day_dt + timedelta(hours=random.randint(6, 22), minutes=random.randint(0, 59))),
                        "event_name": "feature_use", "feature_name": feature, "session_id": session
                    })

            # Device instability events are intentionally more frequent in this scenario,
            # but not all users who churn belong to this group.
            if has_device and scenario == "device_issue" and random.random() < 0.075:
                event_counter += 1
                t = day_dt + timedelta(hours=random.randint(0, 23))
                events.append({
                    "event_id": f"E{event_counter:08d}", "user_id": uid, "device_id": device_id,
                    "event_time": iso(t), "event_name": "device_offline",
                    "feature_name": None, "session_id": None
                })
                if random.random() < 0.75:
                    event_counter += 1
                    events.append({
                        "event_id": f"E{event_counter:08d}", "user_id": uid, "device_id": device_id,
                        "event_time": iso(t + timedelta(minutes=random.randint(5, 180))),
                        "event_name": "device_online", "feature_name": None, "session_id": None
                    })
            day += timedelta(days=1)

        # Feedback: synthetic text only; ground-truth category is retained for later testing.
        if random.random() < 0.18:
            category = {
                "healthy": weighted_choice([("positive", 0.65), ("feature_discovery", 0.15), ("performance", 0.20)]),
                "onboarding_dropoff": weighted_choice([("onboarding", 0.75), ("feature_discovery", 0.25)]),
                "device_issue": weighted_choice([("device_connection", 0.80), ("performance", 0.20)]),
                "low_feature_discovery": weighted_choice([("feature_discovery", 0.75), ("onboarding", 0.25)]),
                "churn_risk": weighted_choice([("performance", 0.35), ("feature_discovery", 0.35), ("device_connection", 0.30)])
            }[scenario]
            feedback_counter += 1
            feedback_time = random_time(signup, end)
            sentiment = "positive" if category == "positive" else ("negative" if category in ["device_connection", "performance", "onboarding"] else "neutral")
            feedback_id = f"F{feedback_counter:06d}"
            feedback.append({
                "feedback_id": feedback_id, "user_id": uid,
                "created_at": iso(feedback_time), "channel": random.choice(["in_app", "email", "survey"]),
                "feedback_text": random.choice(FEEDBACK_BANK[category])
            })
            feedback_labels.append({
                "feedback_id": feedback_id, "expected_category": category,
                "expected_sentiment": sentiment
            })

        if plan == "premium":
            payment_counter += 1
            payments.append({
                "payment_id": f"P{payment_counter:06d}", "user_id": uid,
                "paid_at": iso(random_time(signup, end)), "amount": round(random.choice([9.9, 19.9, 29.9]), 2),
                "currency": "CNY", "plan_type": "premium",
                "payment_status": random.choices(["success", "failed", "refunded"], [0.91, 0.06, 0.03])[0]
            })

    frames = {
        "users.csv": pd.DataFrame(users),
        "devices.csv": pd.DataFrame(devices),
        "events.csv": pd.DataFrame(events),
        "feedback.csv": pd.DataFrame(feedback),
        "payments.csv": pd.DataFrame(payments),
    }
    for name, df in frames.items():
        df.to_csv(RAW_DIR / name, index=False, encoding="utf-8-sig")

    pd.DataFrame(ground_truth).to_csv(GT_DIR / "user_scenarios.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(feedback_labels).to_csv(GT_DIR / "feedback_labels.csv", index=False, encoding="utf-8-sig")

    print("Synthetic dataset generated:")
    for name, df in frames.items():
        print(f"  {name}: {len(df):,} rows")
    print(f"  user_scenarios.csv (evaluation only): {len(ground_truth):,} rows")
    print(f"  feedback_labels.csv (evaluation only): {len(feedback_labels):,} rows")
    print(f"  Dataset end date: {end_date}; random seed: {seed}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--users", type=int, default=1000)
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--end-date", type=str, default="2026-11-13")
    args = parser.parse_args()
    generate(args.users, args.days, args.seed, args.end_date)
