# AI Agent Guidelines

## Context
You are an expert Python developer working on an R&D project. Your goal is to write clean, efficient, and maintainable code that strictly adheres to the company's internal guidelines.

## Design Philosophy
* **YAGNI (You Aren't Gonna Need It):** Do not over-engineer or add functionality/generalizations until explicitly required. Write simple code now; refactor later.
* **KISS (Keep It Simple, Stupid):** Prioritize simple, readable code over fancy, complex, or "write-only" one-liners.
* **DRY (Don't Repeat Yourself):** Ensure single, unambiguous representations of logic.
* **TDD Mindset:** Write code with easily testable interfaces. Generate tests for new code covering as many edge cases as possible.

## Code Style & Formatting
* **Standard:** Strictly follow PEP8.
* **File Headers:** Every Python file MUST start with the following header:
  `# Copyright 2024 Pexeso Inc. All rights reserved.` *(Note: Update year to current year if writing new files)*
* **Imports:** * Use absolute imports. Never use wildcard imports (`from module import *`).
  * Group imports strictly in this order, separated by a blank line:
    1. Standard library imports
    2. Third-party imports
    3. Local application/library imports
* **Strings:** Use double quotes (`"`) for human-readable strings, and single quotes (`'`) for programmatic strings (e.g., dictionary keys, regex).

## Documentation & Typing
* **Docstrings:** Always use **reStructuredText (reST)** markup syntax for docstrings.
* **Type Hints:** Fully type-hint all function arguments and return values.
* **Example Format:**
  ```python
  def divide(self, divisor: float) -> float:
      """
      Divide the current value by a number.

      :param divisor: The number to divide by.
      :raises ValueError: If divisor is zero.
      :return: The new current value after division.
      """
  ```

## Pythonic Idioms & Best Practices
* **Resource Management:** Always use context managers (`with`) for files and directories. Never use bare `open(filename)`.
  * *Correct:* `with open(filename) as fr:`
  * *Temp files:* `with tempfile.TemporaryDirectory() as tmpdirname:`
* **Numpy Consistency:** Do not mix built-in Python math functions with Numpy arrays, as broadcasting can cause magical/unintended behavior. Always use numpy equivalents when available.
  * *Incorrect:* `np.argmax(abs(x1 - x2))`
  * *Correct:* `np.argmax(np.abs(x1 - x2))`

## Error Handling, Exceptions & Assertions
* **Exceptions (Be Lazy):** Do not wrap standard operations in `try/except` blocks just to print a message and exit. Let the code fail loudly with the native exception (e.g., `FileNotFoundError`) so the traceback is visible.
* **Specific Exceptions:** When catching exceptions, always catch the most specific exception possible. Never use a bare `except:` or `except Exception:` unless explicitly creating a top-level crash handler.
* **Custom Domain Exceptions:** When building core business logic, generate custom exception classes inheriting from `Exception` to represent domain-specific errors.
* **Assertions:** Avoid `assert` in Production Code. Because asserts are ignored in Python's optimized mode (`-O`), do not use them for data validation or standard logic. Use standard `if` statements raising explicit exceptions instead:
  * *Incorrect:* `assert x > 0, "x must be positive"`
  * *Correct:* `if x <= 0: raise ValueError("x must be positive")`
  * *Exception:* `assert` statements are perfectly fine and encouraged inside test files.

## Storage & File I/O Optimization (Cost/Performance Guardrails)
* **Never use `os.path.exists()` checks inside loops** when processing large numbers of files (e.g., mounted network drives). This doubles the I/O operations and incurs unnecessary cloud costs.
* **Pre-create directories:** If saving multiple files, pre-create the parent directory once outside the loop.
* **Do not use `os.makedirs()` for output files inside functions.** Assume the caller has prepared the output directory. If it fails, let it fail (it often indicates a typo in the path).

## Testing & Benchmarking
* **Testing Framework:** Always use `pytest` for generating tests.
* **Mocking:** When testing code that hits external APIs, databases, or file systems, always use `pytest-mock` (or `unittest.mock`) to isolate the logic.
* **Parametrization:** When testing multiple inputs/outputs, use `@pytest.mark.parametrize` rather than writing loops inside tests or duplicating test functions.
* **Benchmarking:** When instructed to write performance tests, use the `pytest-benchmark` fixture to measure execution time.

## Continuous Integration & Deployment
* **CI Workflows:** When generating GitHub Actions or CI/CD pipelines, always include steps for code quality: `ruff` (for linting/formatting), `mypy` (for strict type checking), and `pytest` (for unit testing).
* **Versioning:** The project uses Semantic Versioning (X.Y.Z). When writing release scripts or updating metadata, assume `X` (Major API changes), `Y` (Backward-compatible features), and `Z` (Bug fixes).
* **READMEs:** Do not generate standard setup instructions (like `python -m venv venv` or `pip install -r requirements.txt`) in `README.md` files unless explicitly asked. Assume developers know standard Python setups.

## Git & Dependencies
* **Commits/PRs:** Expect at least one reviewer.
* **LFS:** Assume large binary files are tracked via Git LFS. Never commit large `.csv`, `.mp4`, or `.bin` files directly.
