import re

from openai import OpenAI

from app.core.config import settings
from app.core.logging import logger
from app.core.timing import log_duration


LLM_MODEL = "gpt-4o-mini"


SYSTEM_PROMPT = """You are a precise and helpful knowledge base assistant.
You answer questions strictly using the context documents provided to you.

Rules you must always follow:
1. Answer ONLY from the provided context. Do not use outside knowledge.
2. Never mention, reference, or cite any document name, filename, or file identifier anywhere in your answer. Speak only about the content itself, as if you simply know the information — never reveal where it came from.
3. If the answer is not found in the context, respond with exactly:
   "I don't have information about that in my knowledge base."
4. Never guess, invent facts, or fill gaps with assumptions.
5. If multiple documents are relevant, combine the information naturally.
6. Format your answer using Markdown where appropriate (e.g., use bullet points for lists, bold text for emphasis, and Markdown tables for tabular data). Keep it clean and clear.
7. When the context contains specific facts relevant to the question — dates, deadlines, times, amounts, percentages, or rows from a table — you MUST include those exact values verbatim in your answer, not just a narrative paraphrase of the surrounding process. If the context includes a schedule or table with dates/times, present it as a Markdown table (or list) with the exact values, even if the question only asks generally for "details". Never summarize away concrete data points that are present in the context.
"""


CONDENSE_QUESTION_PROMPT = """Rewrite the follow-up question into a standalone question ONLY if it depends on the conversation history to be understood (e.g. it uses a pronoun like "it/that/those/this", or omits a subject implied earlier).

If the follow-up question already names its own subject clearly and would make complete sense to someone who has never seen the conversation history, return it EXACTLY unchanged.

STRICT OUTPUT FORMAT:
- Output ONLY the question text itself, on a single line.
- Do NOT include the word "Standalone", quotation marks, explanations, notes, or any commentary about whether or why it was changed.
- Do NOT add extra topics, filenames, or framing that the question did not ask for, even if they appear in the history.

Example 1 — needs rewriting:
History:
User: What are the retrieval tokens for the GPT contradiction analyzer paper?
Assistant: contradiction, analysis, patent, gpt, analyzer
Follow-up: What about the Function Oriented Search one?
Correct output: What are the retrieval tokens for the Function Oriented Search paper?

Example 2 — already standalone, must be returned unchanged with no added commentary:
History:
User: When should the Faer stratagem bank be activated?
Assistant: It should be activated only when the task is clearly nontechnical...
Follow-up: Who published the ITC2025 MATRIZ proceedings?
Correct output: Who published the ITC2025 MATRIZ proceedings?

Now do the same for this conversation. Remember: output ONLY the question text, nothing else.

Conversation History:
{history}

Follow-up Question: {question}

Output:"""


# Pronouns/reference words that signal a question likely depends on prior
# context. Used as a cheap pre-check so we don't waste an LLM call (and risk
# it over-rewriting) on questions that are obviously already standalone.
_REFERENCE_SIGNALS = re.compile(
    r"\b(it|its|that|those|this|these|they|them|their|"
    r"he|him|his|she|her|the same|the above|"
    r"what about|and what|and who|and how|and where|and when)\b",
    re.IGNORECASE
)


class LLMService:

    def __init__(self):

        self.client = OpenAI(
            api_key=settings.OPENAI_API_KEY
        )

    def _needs_condensing(self, question: str) -> bool:
        """
        Cheap heuristic gate: only bother calling the LLM to rewrite the
        question if it actually looks like it depends on prior context.
        Short questions with no named subject are also treated as suspect.
        """
        if _REFERENCE_SIGNALS.search(question):
            return True

        word_count = len(question.strip().split())
        if word_count <= 3:
            return True

        return False

    def condense_question(
        self,
        chat_history: list,
        question: str
    ) -> str:
        """
        Rewrites a follow-up question into a standalone question using
        recent conversation history, so that retrieval/caching downstream
        works on a self-contained query instead of a context-dependent one
        like "what about its price?".

        chat_history: list of dicts like {"role": "user"/"assistant", "content": "..."}
        Returns the original question unchanged if there is no history, or
        if the question doesn't show signs of depending on prior context.
        """

        if not chat_history:
            return question

        if not self._needs_condensing(question):
            logger.info(
                f"Skipping condensing for '{question}' — looks standalone already"
            )
            return question

        history_text = "\n".join(
            f"{turn['role'].capitalize()}: {turn['content']}"
            for turn in chat_history
        )

        prompt = CONDENSE_QUESTION_PROMPT.format(
            history=history_text,
            question=question
        )

        logger.debug(
            f"LLM request starting: condense_question — model={LLM_MODEL}, "
            f"prompt_chars={len(prompt)}, history_turns={len(chat_history)}"
        )

        try:
            with log_duration(logger, "llm_condense_question", model=LLM_MODEL):
                response = self.client.chat.completions.create(
                    model=LLM_MODEL,
                    messages=[
                        {
                            "role": "user",
                            "content": prompt
                        }
                    ],
                    temperature=0,
                    max_tokens=150
                )

            raw_output = (
                response
                .choices[0]
                .message
                .content
                .strip()
                .strip('"')
            )

            # Safety net: even with the strict format instructions, take only
            # the first non-empty line. This guarantees any stray commentary
            # the model adds on a second line/paragraph never leaks into the
            # actual query used for embeddings/caching/retrieval.
            first_line = next(
                (line.strip() for line in raw_output.splitlines() if line.strip()),
                ""
            )

            standalone_question = first_line if first_line else question

            logger.info(
                f"Condensed follow-up question: '{question}' -> '{standalone_question}'"
            )

            return standalone_question

        except Exception as e:
            # If condensing fails for any reason, fall back to the raw
            # question rather than breaking the whole chat flow.
            logger.error(f"Failed to condense follow-up question: {str(e)}", exc_info=True)
            return question

    def generate_answer(
        self,
        context: str,
        question: str
    ) -> dict:

        user_message = f"""Here are the relevant documents from the knowledge base:

{context}

---

Question: {question}

Answer using only the documents above. Use Markdown formatting for tables and lists if it makes the answer clearer. If any of the documents above contain specific dates, times, amounts, or a schedule/table relevant to this question, include those exact values — do not leave them out in favor of a general narrative summary. Do not mention filenames or document names in your answer."""

        # Prompt execution details logged at DEBUG only, and only a length
        # (not the full context) — the context is customer document
        # content, so logging it verbatim at INFO/production log levels
        # would leak potentially sensitive tenant data into the logs.
        logger.debug(
            f"LLM request starting: generate_answer — model={LLM_MODEL}, "
            f"context_chars={len(context)}, question_chars={len(question)}"
        )

        with log_duration(logger, "llm_generate_answer", model=LLM_MODEL):
            response = self.client.chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": SYSTEM_PROMPT
                    },
                    {
                        "role": "user",
                        "content": user_message
                    }
                ],
                temperature=0.2,
                max_tokens=1024
            )

        raw = (
            response
            .choices[0]
            .message
            .content
        )

        # Safety net only: with filenames no longer present in the context,
        # the model has nothing to cite, so this should rarely match
        # anything now. Left in place in case the model ever adds a
        # "Sources:"-style line on its own despite the prompt instructions.
        without_sources = re.sub(
            r"\n*\s*Sources:\s*[^\n]*\s*$",
            "",
            raw,
            flags=re.IGNORECASE
        ).strip()

        clean = re.sub(r"\n{3,}", "\n\n", without_sources).strip()

        usage = response.usage

        token_usage = {
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens
        }

        logger.info(
            f"LLM response completed — prompt: {token_usage['prompt_tokens']}, "
            f"completion: {token_usage['completion_tokens']}, "
            f"total: {token_usage['total_tokens']}",
            extra={"event": "llm_response_completed", "model": LLM_MODEL, **token_usage}
        )

        return {
            "answer": clean,
            "token_usage": token_usage
        }