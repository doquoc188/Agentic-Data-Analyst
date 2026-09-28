"""Gemini chat model setup and connection test."""

import os

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI


def get_llm() -> ChatGoogleGenerativeAI:
    """Create a Gemini chat model using GOOGLE_API_KEY."""
    load_dotenv()
    api_key = os.getenv("GOOGLE_API_KEY")
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
