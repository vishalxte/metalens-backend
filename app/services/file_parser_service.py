import json
import re
from collections import Counter

from pypdf import PdfReader

from app.core.logging import logger
from app.core.timing import log_duration


class FileParserService:

    @staticmethod
    def parse_md(file_path: str):
        with log_duration(logger, "parse_md", file_path=file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                text = f.read()
            logger.debug(f"parse_md: read {len(text)} chars from {file_path}")
            return text

    @staticmethod
    def parse_json(file_path: str):
        with log_duration(logger, "parse_json", file_path=file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)  # validates it's actually valid JSON

            # Pretty-print back to text so it's readable & chunkable
            text = json.dumps(data, indent=2, ensure_ascii=False)
            logger.debug(f"parse_json: parsed + pretty-printed {len(text)} chars from {file_path}")
            return text

    @staticmethod
    def _strip_repeated_boilerplate(pages_text: list) -> list:
        """
        Multi-page PDFs (reports, tenders, RFPs) almost always repeat a
        header and/or footer line on every single page — company name,
        page number, document title. Left in, that line gets duplicated
        into dozens of chunks once the document is split, diluting the
        real content's signal for both keyword and vector retrieval, and
        for the LLM directly if a large slice of the document is ever
        sent as context at once. This detects lines that recur across a
        large fraction of pages (near the top or bottom, where
        headers/footers live) and strips them — generically, without
        hardcoding any specific company name or document title, so it
        works the same way for any customer's PDFs.
        """
        if len(pages_text) < 4:
            # Too few pages for "recurs across most pages" to mean anything
            return pages_text

        EDGE_LINES = 3  # only look at the first/last few lines of each page

        def normalize(line):
            # Headers/footers almost always embed a page number ("...
            # CORPORATION 24", "Page 3 of 85"), which makes every page's
            # header a technically-different string. Collapsing digit
            # runs to a placeholder lets "...CORPORATION 24" and
            # "...CORPORATION 45" count as the same recurring line.
            return re.sub(r"\d+", "#", line.strip())

        def candidate_lines(page_text):
            lines = [
                line.strip()
                for line in page_text.splitlines()
                if line.strip()
            ]
            return lines[:EDGE_LINES] + lines[-EDGE_LINES:]

        line_counts = Counter()
        for page_text in pages_text:
            # Count each normalized line once per page (a set, not the
            # raw list) so a line appearing near both the top and bottom
            # of one page doesn't inflate its own frequency.
            for line in set(candidate_lines(page_text)):
                line_counts[normalize(line)] += 1

        total_pages = len(pages_text)
        boilerplate_patterns = {
            pattern
            for pattern, count in line_counts.items()
            if count >= max(3, int(total_pages * 0.4))
            and len(pattern) < 120  # headers/footers are short; never strip real paragraphs
        }

        if not boilerplate_patterns:
            return pages_text

        cleaned_pages = []
        for page_text in pages_text:
            kept_lines = [
                line
                for line in page_text.splitlines()
                if normalize(line) not in boilerplate_patterns
            ]
            cleaned_pages.append("\n".join(kept_lines))

        return cleaned_pages

    @staticmethod
    def parse_pdf(file_path: str):
        with log_duration(logger, "parse_pdf", file_path=file_path):
            reader = PdfReader(file_path)

            if reader.is_encrypted:
                # Some PDFs are "encrypted" with an empty owner password just to
                # set permissions — this unlocks those; a real password-protected
                # PDF will still fail to decrypt and raise below.
                logger.debug(f"parse_pdf: {file_path} is encrypted, attempting empty-password decrypt")
                reader.decrypt("")

            pages_text = []
            for page in reader.pages:
                text = page.extract_text() or ""
                if text.strip():
                    pages_text.append(text)

            logger.debug(
                f"parse_pdf: extracted text from {len(pages_text)}/{len(reader.pages)} pages of {file_path}"
            )

            if not pages_text:
                logger.error(f"parse_pdf: no extractable text in {file_path} (possibly scanned image PDF)")
                raise ValueError(
                    "No extractable text found in this PDF (it may be a scanned "
                    "image without OCR)."
                )

            before_boilerplate_len = sum(len(p) for p in pages_text)
            pages_text = FileParserService._strip_repeated_boilerplate(pages_text)
            after_boilerplate_len = sum(len(p) for p in pages_text)

            if after_boilerplate_len != before_boilerplate_len:
                logger.debug(
                    f"parse_pdf: boilerplate stripping removed "
                    f"{before_boilerplate_len - after_boilerplate_len} chars from {file_path}"
                )

            extracted_text = "\n\n".join(pages_text).strip()

            if not extracted_text:
                logger.error(f"parse_pdf: extracted text was empty after cleanup for {file_path}")
                raise ValueError(
                    "No extractable text found in this PDF (it may be a scanned "
                    "image without OCR)."
                )

            return extracted_text
