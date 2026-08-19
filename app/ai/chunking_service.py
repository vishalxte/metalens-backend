from langchain_text_splitters import (
    RecursiveCharacterTextSplitter
)

from app.core.logging import logger
from app.core.timing import log_duration


class ChunkingService:

    @staticmethod
    def split_text(text: str):

        splitter = (
            RecursiveCharacterTextSplitter(
                chunk_size=1000,
                chunk_overlap=200
            )
        )

        with log_duration(logger, "chunk_splitting", input_chars=len(text)):
            chunks = splitter.split_text(text)

        logger.debug(f"chunk_splitting: {len(text)} chars -> {len(chunks)} chunks")

        return chunks
