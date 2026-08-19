from apscheduler.schedulers.background import BackgroundScheduler

from datetime import datetime
from app.models.otp import OTP


from app.database.session import SessionLocal

scheduler = BackgroundScheduler()

def cleanup_otps():
    db = SessionLocal()
    try:
        db.query(OTP).filter(
            OTP.expires_at <= datetime.utcnow()
        ).delete(synchronize_session=False)
      
        print(db.query(OTP).filter(OTP.expires_at <= datetime.utcnow()).all())
        db.commit()
    finally:
        db.close()

scheduler.add_job(cleanup_otps, "interval", minutes=1)
print("OTP cleanup job scheduled to run every 1 minute.")