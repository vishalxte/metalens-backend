ALLOWED_EXTENSIONS = {"md", "json", "pdf"}

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


class FileValidator:

    @staticmethod
    def validate_type(filename: str):
        extension = filename.split(".")[-1].lower()
        return extension in ALLOWED_EXTENSIONS

    @staticmethod
    def validate_size(file_size: int):
        return file_size <= MAX_FILE_SIZE