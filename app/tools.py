"""Standalone tools for the project."""

from langchain.tools import tool


@tool("calculator")
def calculator(operation: str, a: float, b: float) -> float:
    """Calculate a + b, a - b, a * b, or a / b. Use operation: add, subtract, multiply, or divide."""
    if operation == "add":
        return a + b
    if operation == "subtract":
        return a - b
    if operation == "multiply":
        return a * b
    if operation == "divide":
        if b == 0:
            raise ZeroDivisionError("Cannot divide by zero.")
        return a / b
    raise ValueError(f"Unknown operation: {operation}")
