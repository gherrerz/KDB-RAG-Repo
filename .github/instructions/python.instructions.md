---
description: 'Python coding conventions and guidelines'
applyTo: '**/*.py'
---

# Python Coding Conventions

## Python Instructions

- Document public functions, classes, and modules with clear docstrings.
- Use inline comments sparingly to explain non-obvious intent,
    constraints, or tradeoffs.
- Ensure functions have descriptive names and include type hints.
- Provide docstrings following PEP 257 conventions.
- Use modern type annotations and prefer built-in generics such as
    `list[str]` and `dict[str, int]`. Use `typing` tools like `Protocol`,
    `TypedDict`, and `TypeAlias` when they improve clarity.
- Break down complex functions into smaller, more manageable functions.

## Design Principles and SOLID

- Design classes, modules, and services with a single responsibility and
    a single reason to change.
- Separate business rules from I/O, persistence, serialization,
    framework code, and presentation concerns.
- Prefer composition over inheritance unless inheritance models a true
    is-a relationship.
- Keep code open for extension and closed for modification by using
    strategies, adapters, registries, and other small extension points.
- When using inheritance, ensure subclasses preserve the behavior,
    invariants, exceptions, and expectations of their base types.
- Prefer small, focused interfaces using `Protocol` or ABC classes. Do
    not force clients to depend on methods they do not use.
- Depend on abstractions rather than concrete implementations. Inject
    collaborators through constructors or function parameters instead of
    instantiating them inside business logic.
- Write contract tests for shared abstractions when multiple
    implementations exist.
- Avoid classes or functions that mix orchestration, validation, domain
    logic, persistence, and transport concerns in the same unit.

## General Instructions

- Always prioritize readability and clarity.
- For algorithm-related code, include explanations of the approach used.
- Write code with good maintainability practices, including brief
    explanations of why non-obvious design decisions were made.
- Handle edge cases and write clear exception handling.
- For libraries or external dependencies, document their usage and
    purpose when it affects architecture, behavior, or deployment.
- Use consistent naming conventions and follow language-specific best practices.
- Write concise, efficient, and idiomatic code that is also easily understandable.

## Code Style and Formatting

- Follow the **PEP 8** style guide for Python.
- Maintain proper indentation (use 4 spaces for each level of indentation).
- Ensure lines do not exceed 79 characters.
- Place function and class docstrings immediately after the `def` or `class` keyword.
- Use blank lines to separate functions, classes, and code blocks where appropriate.

## Edge Cases and Testing

- Always include test cases for critical paths of the application.
- Account for common edge cases like empty inputs, invalid data types, and large datasets.
- Include comments for edge cases and the expected behavior in those cases.
- Write unit tests for functions and document them with docstrings explaining the test cases.
- Add contract or substitution tests when multiple implementations share
    the same abstraction.

## Example of Proper Documentation

```python
def calculate_area(radius: float) -> float:
    """
    Calculate the area of a circle given the radius.
    
    Parameters:
    radius (float): The radius of the circle.
    
    Returns:
    float: The area of the circle, calculated as π * radius^2.
    """
    import math
    return math.pi * radius ** 2
```
