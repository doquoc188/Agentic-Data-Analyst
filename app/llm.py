"""Gemini chat model setup and connection test."""

from langchain_google_genai import ChatGoogleGenerativeAI

from app.config import get_settings


def get_llm() -> ChatGoogleGenerativeAI:
    """Create a Gemini chat model using GOOGLE_API_KEY."""
    api_key = get_settings().google_api_key
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY is missing. Set it in .env or the environment.")

    return ChatGoogleGenerativeAI(
        model="gemini-3.5-flash-lite",
        temperature=0,
        api_key=api_key,
    )


if __name__ == "__main__":
    response = get_llm().invoke("Reply with exactly: Gemini connection successful")
    print(response.text)
