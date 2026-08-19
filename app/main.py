from threading import Thread

from fastapi import FastAPI
from fastapi.responses import JSONResponse
# Aliased on purpose. There is a second `start_scheduler` further down in
# this file (the OTP scheduler's @app.on_event("startup") handler). Both
# live in this module's namespace, so the later `async def
# start_scheduler` REBINDS this name at import time. The startup handler
# below runs after the module is fully imported, so without the alias it
# would hand the OTP coroutine function to Thread(target=...) instead of
# the backup one — the thread would build a coroutine, never await it,
# and the backup would silently never run.
from app.backup.backupscheduler import start_scheduler as start_backup_scheduler
from app.api.router import api_router
from app.core.config import settings
from app.core.logging import logger
from app.core.middleware import RequestLoggingMiddleware

from app.core.exceptions import (
    IncorrectOtpException,
    UserAlreadyExistsException,
    InvalidCredentialsException,
    UserAccountDeactivatedException,
    OTPVerificationFailedException,
    OTPExpiredException,
    OtpDeliveryException,
)

from app.core.exceptions import (
    DuplicateDocumentException,
    InvalidFileException
)

from app.core.exceptions import (
    CustomerAlreadyExistsException,
    CustomerNotFoundException
)

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title=settings.PROJECT_NAME,
    # Keeps the Bearer token you enter in Swagger's "Authorize" dialog
    # saved in the browser (localStorage) across page refreshes and
    # backend --reload restarts, so you don't have to re-paste the token
    # after every code change / reload during local development.
    swagger_ui_parameters={"persistAuthorization": True}
)


@app.on_event("startup")
async def log_startup():
    logger.info(
        f"Application startup: {settings.PROJECT_NAME} "
        f"(environment={settings.ENVIRONMENT}, log_level={settings.LOG_LEVEL})",
        extra={
            "event": "app_startup",
            "environment": settings.ENVIRONMENT,
            "log_level": settings.LOG_LEVEL
        }
    )

    # Close out sessions whose token expired while the process was down.
    # Those sessions have no "moment" at which anything could have
    # written ended_at — nobody logged out, nobody revoked them, they
    # simply ran out of time — so without this they would keep
    # ended_at = NULL forever and read as though still open.
    #
    # Imported here rather than at module scope to keep the existing
    # import block untouched, and because this is the only place in
    # main.py that needs it. Never raises (see SessionService), so it
    # cannot prevent the application from starting.
    from app.services.session_service import session_service

    session_service.expire_stale()


@app.on_event("shutdown")
async def log_shutdown():
    logger.info(
        f"Application shutdown: {settings.PROJECT_NAME}",
        extra={"event": "app_shutdown"}
    )

@app.on_event("startup")
async def startup_event():
    Thread(target=start_backup_scheduler, daemon=True).start()



app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Added after CORSMiddleware so it ends up outermost (Starlette runs
# middlewares in reverse of add order) — request/response logging then
# wraps the entire request, including CORS handling.
app.add_middleware(RequestLoggingMiddleware)

app.include_router(api_router)


@app.exception_handler(UserAlreadyExistsException)
async def user_exists_handler(request, exc):
    logger.warning(f"UserAlreadyExistsException on {request.url.path}")
    return JSONResponse(
        status_code=400,
        content={
            "message": "User already exists"
        }
    )


@app.exception_handler(InvalidCredentialsException)
async def invalid_credentials_handler(request, exc):
    logger.warning(f"InvalidCredentialsException (failed login) on {request.url.path}")
    return JSONResponse(
        status_code=401,
        content={
            "message": "Invalid credentials"
        }
    )

@app.exception_handler(
    DuplicateDocumentException
)
async def duplicate_handler(
    request,
    exc
):
    logger.warning(f"DuplicateDocumentException on {request.url.path}: {getattr(exc, 'detail', '')}")
    return JSONResponse(
        status_code=400,
        content={
            "message":
            "Document already exists"
        }
    )


@app.exception_handler(
    InvalidFileException
)
async def invalid_file_handler(
    request,
    exc
):
    logger.warning(f"InvalidFileException on {request.url.path}: {getattr(exc, 'detail', '')}")
    return JSONResponse(
        status_code=400,
        content={
            "message":
            "Invalid file"
        }
    )


@app.exception_handler(CustomerAlreadyExistsException)
async def customer_exists_handler(request, exc):
    logger.warning(f"CustomerAlreadyExistsException on {request.url.path}: {exc.detail}")
    return JSONResponse(
        status_code=400,
        content={
            "message": exc.detail
        }
    )

@app.exception_handler(UserAccountDeactivatedException)
async def user_account_deactivated_handler(request,exc):
    logger.warning(f"UserAccountDeactivatedException on {request.url.path}: {getattr(exc, 'detail', '')}")
    
    return JSONResponse(
     status_code=401,
     content={
         "message":"Your Account has been deactivated.Please contact your administrator."
     }
    )



@app.exception_handler(CustomerNotFoundException)
async def customer_not_found_handler(request, exc):
    logger.warning(f"CustomerNotFoundException on {request.url.path}: {exc.detail}")
    return JSONResponse(
        status_code=404,
        content={
            "message": exc.detail
        }
    )

@app.exception_handler(IncorrectOtpException)
async def incorrect_otp_handler(request, exc):
    logger.warning(f"IncorrectOtpException on {request.url.path}: {getattr(exc, 'detail', '')}")
    return JSONResponse(
        status_code=400,
        content={
            "message": "Incorrect Otp,OTP verification failed"
        }
    )


# 503, not 4xx: the credentials were fine and the user did nothing wrong
# — the mail server is unreachable or rejecting us. 503 tells the client
# (and any monitoring in front of it) that this is a transient
# server-side outage worth retrying, which a 400/401 would not.
#
# AuthService has already written the OTP_SEND_FAILED audit row and
# logged the full exception, so this handler only shapes the response.
# The SMTP error text is deliberately not echoed back — it can contain
# the configured username and connection detail.
@app.exception_handler(OtpDeliveryException)
async def otp_delivery_handler(request, exc):
    logger.warning(f"OtpDeliveryException on {request.url.path}: {getattr(exc, 'detail', '')}")
    return JSONResponse(
        status_code=503,
        content={
            "message": getattr(
                exc,
                "detail",
                "Could not send the verification code. Please try again in a moment."
            )
        }
    )

@app.exception_handler(OTPVerificationFailedException)
async def otp_verification_failed_handler(request, exc):
    logger.warning(f"OTPVerificationFailedException on {request.url.path}: {getattr(exc, 'detail', '')}")
    return JSONResponse(
        status_code=400,
        content={
            "message": "OTP verification failed"
        }
    )

@app.exception_handler(OTPExpiredException)
async def otp_expired_handler(request, exc):
    logger.warning(f"OTPExpiredException on {request.url.path}: {getattr(exc, 'detail', '')}")
    return JSONResponse(
        status_code=400,
        content={
            "message": "OTP has expired"
        }
    )

@app.get("/")
def health_check():
    return {
        "message": "AI Document Search"
    }


@app.on_event("startup")
async def start_scheduler():
    from app.scheduler.otpscheduler import scheduler
    scheduler.start()


@app.on_event("shutdown")
async def shutdown_scheduler():
    from app.scheduler.otpscheduler import scheduler
    scheduler.shutdown()    