import random
from email.message import EmailMessage


from app.core.exceptions import IncorrectOtpException, InvalidCredentialsException
import aiosmtplib
import os
from sqlalchemy.orm import Session
from app.models.otp import OTP
from datetime import datetime, timedelta

from app.core.security import create_access_token
from dotenv import find_dotenv, load_dotenv


from app.models.user import User

# usecwd=True is REQUIRED here, not cosmetic.
#
# Plain load_dotenv() calls find_dotenv() with no arguments, which walks
# sys._getframe() back through the caller's stack looking for the first
# frame whose co_filename exists on disk, asserting `frame.f_back is not
# None` on the way. Once this package is compiled by Nuitka into a single
# extension module there is no such Python-level caller frame, the walk
# runs off the end of the stack and the assert fires — the app dies at
# import time with a bare AssertionError from dotenv/main.py.
#
# usecwd=True skips the frame walk entirely and looks for .env in the
# process working directory, which is the project root in every
# documented way of starting this app (and /app inside the container).
# Behaviour under plain `python -m uvicorn` from the project root is
# unchanged.
load_dotenv(find_dotenv(usecwd=True))

async def send_otp(email: str):

    otp = str(random.randint(100000, 999999))

    message = EmailMessage()
    message["From"] = os.getenv("FROM_EMAIL")
    message["To"] = email
    message["Subject"] = "Your OTP"

    message.set_content(
        f"""
Your verification code is:

{otp}

Valid for 5 minutes.
"""
    )

    await aiosmtplib.send(
        message,
        hostname=os.getenv("SMTP_HOST"),
        port=587,
        start_tls=True,
        username=os.getenv("SMTP_USER"),
        password=os.getenv("SMTP_PASSWORD"),
    )

    return otp



def store_otp(
        
        db: Session,
        email: str,
        otp: str,
       
    ):
        otp_entry = OTP(
            email=email,
            otp=otp,
            expires_at=datetime.utcnow() + timedelta(minutes=5)
        )
        db.add(otp_entry)
        db.commit()
        db.refresh(otp_entry)
        return otp_entry


async def resend_otp(
    email: str,
    db: Session
):
    new_otp = await send_otp(email)
    new_expires_at = datetime.utcnow() + timedelta(minutes=5)

    otp_entry = (
        db.query(OTP)
        .filter(OTP.email == email)
        .order_by(OTP.id.desc())
    .first()
)
    

    if otp_entry:
        otp_entry.otp = new_otp
        otp_entry.expires_at = new_expires_at
        db.commit()
        db.refresh(otp_entry)
    else:
        otp_entry = store_otp(
            db,
            email,
            new_otp
        ) 

    return {
        "message": "OTP resent successfully.",
        "email": email,
        "expires_at": otp_entry.expires_at
    }


async def send(email: str):

    otp = await send_otp(email)

    store_otp(email, otp)

    return {"message": "OTP sent"}

