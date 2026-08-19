class UserAlreadyExistsException(Exception):
    def __init__(self, detail: str = "User already exists"):
        self.detail = detail
        super().__init__(detail)


class InvalidCredentialsException(Exception):
    def __init__(self, detail: str = "Invalid credentials"):
        self.detail = detail
        super().__init__(detail)


class DuplicateDocumentException(Exception):
    def __init__(self, detail: str = "Document already exists"):
        self.detail = detail
        super().__init__(detail)


class InvalidFileException(Exception):
    def __init__(self, detail: str = "Invalid file"):
        self.detail = detail
        super().__init__(detail)


class CustomerAlreadyExistsException(Exception):
    def __init__(self, detail: str = "Customer already exists"):
        self.detail = detail
        super().__init__(detail)


class CustomerNotFoundException(Exception):
    def __init__(self, detail: str = "Customer not found"):
        self.detail = detail
        super().__init__(detail)



class UserAccountDeactivatedException(Exception):
    def __init__(self, detail:str="Your Account has been deactivated. Please  contact your Administrator"):
        self.detail=detail
        super().__init__(detail)

class OTPVerificationFailedException(Exception):
    def __init__(self, detail: str = "OTP verification failed"):
        self.detail = detail
        super().__init__(detail) 


class OTPExpiredException(Exception):
    def __init__(self, detail: str = "OTP has expired"):
        self.detail = detail
        super().__init__(detail)

class IncorrectOtpException(Exception):
    def __init__(self, detail: str = "Incorrect OTP"):
        self.detail = detail
        super().__init__(detail)


class OtpDeliveryException(Exception):
    """
    The OTP could not be SENT — SMTP was unreachable, rejected our
    credentials, or timed out.

    Deliberately separate from IncorrectOtpException / OTPExpiredException,
    which are about a code the user got wrong. This one is not the user's
    fault at all: it is an infrastructure outage, it is retryable, and it
    maps to 503 rather than a 4xx. Keeping them distinct also keeps the
    audit trail honest — a mail server outage must not look like failed
    authentication attempts.
    """
    def __init__(
        self,
        detail: str = "Could not send the verification code. Please try again in a moment."
    ):
        self.detail = detail
        super().__init__(detail)
        