from openai import OpenAI

from app.core.config import settings
from app.core.logging import logger
from app.core.timing import log_duration

EMBEDDING_MODEL = "text-embedding-3-small"

client = OpenAI(
    api_key=settings.OPENAI_API_KEY
)


# OpenAI accepts up to 2048 inputs per embeddings request, but we stay
# well under that so a single batch never gets anywhere near the
# per-request token ceiling either — large chunks (near chunk_size=1000
# chars) at 100-per-batch is a safe, comfortable margin.
BATCH_SIZE = 100


class EmbeddingService:

    def generate_embedding(
        self,
        text: str
    ):
        logger.debug(f"OpenAI embeddings call starting — model={EMBEDDING_MODEL}, chars={len(text)}")

        with log_duration(logger, "openai_embeddings_call", model=EMBEDDING_MODEL, batch_size=1):
            response = client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=text
            )

        return response.data[0].embedding

    def generate_embeddings_batch(
        self,
        texts: list
    ):
        """
        Embeds many chunks in a handful of API calls instead of one call
        per chunk. For a large document (e.g. an 85-page tender PDF with
        150+ chunks), calling generate_embedding() in a loop meant 150+
        sequential OpenAI requests — slow, and one dropped/rate-limited
        request partway through silently left the document with only
        partial embeddings while it was still marked "COMPLETED".
        Batching cuts that down to a couple of requests total and makes
        a partial failure much less likely in the first place.

        Returns a list of embedding vectors in the same order as `texts`.
        """
        vectors = []
        total_batches = (len(texts) + BATCH_SIZE - 1) // BATCH_SIZE

        for batch_num, start in enumerate(range(0, len(texts), BATCH_SIZE), start=1):
            batch = texts[start:start + BATCH_SIZE]

            logger.debug(
                f"OpenAI embeddings batch {batch_num}/{total_batches} starting "
                f"— model={EMBEDDING_MODEL}, batch_size={len(batch)}"
            )

            with log_duration(
                logger, "openai_embeddings_call",
                model=EMBEDDING_MODEL, batch_size=len(batch),
                batch_num=batch_num, total_batches=total_batches
            ):
                response = client.embeddings.create(
                    model=EMBEDDING_MODEL,
                    input=batch
                )

            # OpenAI guarantees response.data is returned in the same
            # order the inputs were sent in.
            vectors.extend(
                item.embedding
                for item in response.data
            )

        return vectors


embedding_service = EmbeddingService()
