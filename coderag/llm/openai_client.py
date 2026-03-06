"""OpenAI client wrapper for answer generation and validation."""

import re
import unicodedata

from openai import OpenAI

from coderag.core.settings import get_settings
from coderag.llm.prompts import (
    SYSTEM_PROMPT,
    build_answer_prompt,
    build_verify_prompt,
)


def _normalize_verifier_result(value: str) -> str:
    """Normalize verifier text for robust verdict parsing."""
    lowered = value.strip().lower()
    decomposed = unicodedata.normalize("NFD", lowered)
    without_marks = "".join(
        char for char in decomposed if unicodedata.category(char) != "Mn"
    )
    return re.sub(r"\s+", " ", without_marks)


def _is_verifier_result_valid(value: str) -> bool:
    """Interpret verifier verdict from normalized free-text output."""
    normalized = _normalize_verifier_result(value)
    if not normalized:
        return False

    if re.search(r"\b(invalido|invalid|hallucination|hallucinated)\b", normalized):
        return False

    if re.search(r"\b(valido|valid)\b", normalized):
        return True

    return False


class AnswerClient:
    """Service that calls OpenAI Responses API with safe fallbacks."""

    def __init__(self) -> None:
        """Initialize OpenAI client from environment."""
        settings = get_settings()
        self.api_key = settings.openai_api_key
        self.answer_model = settings.openai_answer_model
        self.verifier_model = settings.openai_verifier_model
        self.client = OpenAI(api_key=self.api_key) if self.api_key else None

    def _call(self, model: str, prompt: str) -> str:
        """Execute Responses API call and return plain text output."""
        if self.client is None:
            return "No se encontró información en el repositorio."

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        if hasattr(self.client, "responses"):
            response = self.client.responses.create(model=model, input=messages)
            return (response.output_text or "").strip()

        completion = self.client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0,
        )
        content = completion.choices[0].message.content
        return (content or "").strip()

    def answer(self, query: str, context: str) -> str:
        """Generate context-grounded answer for a user question."""
        prompt = build_answer_prompt(query=query, context=context)
        return self._call(self.answer_model, prompt)

    @property
    def enabled(self) -> bool:
        """Return whether OpenAI-backed generation is enabled."""
        return self.client is not None

    def verify(self, answer: str, context: str) -> bool:
        """Validate whether answer is grounded in provided context."""
        if self.client is None:
            return True

        prompt = build_verify_prompt(answer=answer, context=context)
        result = self._call(self.verifier_model, prompt)
        return _is_verifier_result_valid(result)
