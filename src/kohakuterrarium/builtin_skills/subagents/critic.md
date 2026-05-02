---
name: critic
description: Review and critique code, plans, or outputs
category: subagent
tags: [review, critique, quality, validation]
---

# critic

Sub-agent for reviewing and self-critiquing code changes, plans, or outputs.

## WHEN TO USE

- Before committing a complex code change that needs a second look
- Reviewing a generated plan for completeness and correctness
- Validating output quality before sending to the user
- Checking for edge cases, security issues, or missed requirements
- Self-critique loop to improve quality of prior work

## WHEN NOT TO USE

- Simple, obvious changes that don't need review
- When you need to actually fix issues (use a coding sub-agent instead)
- For exploring code (use explore instead)
- When speed matters more than thoroughness

## HOW TO USE

```
tool call: critic(
Review the following change and check for issues:

<content to review>
)
```

## Arguments

| Arg  | Type    | Description                                           |
| ---- | ------- | ----------------------------------------------------- |
| body | content | Description of what to review plus the content itself |

## Examples

```
tool call: critic(
Review this function for correctness and edge cases:

def divide(a: float, b: float) -> float:
    return a / b
)
```

```
tool call: critic(
Check if this implementation plan covers all requirements:

1. Add user authentication endpoint
2. Store sessions in Redis
3. Return JWT tokens
)
```

```
tool call: critic(
Review the changes in src/core/controller.py for potential issues.
Focus on error handling and concurrency safety.
)
```

```
tool call: critic(
Evaluate whether this output is clear and complete for the user:

"The build failed because of a missing dependency. Run pip install foo."
)
```

## CAPABILITIES

The critic sub-agent has access to:

- `read` - Read file contents for context
- `grep` - Search for patterns to verify claims
- `glob` - Find related files to check consistency

It will:

1. Analyze the provided content against its intended purpose
2. Check for correctness, quality, and completeness
3. Rate issues by severity (high/medium/low)
4. Provide a PASS or FAIL verdict with actionable suggestions

## OUTPUT

Returns a structured review containing:

- **Verdict** - PASS or FAIL
- **Issues Found** - Severity-rated list of problems
- **Suggestions** - Actionable improvement recommendations
- **Summary** - Brief overall assessment

## LIMITATIONS

- Read-only (cannot fix issues, only report them)
- Limited turns (max 5) for focused review
- Returns text feedback (controller must act on suggestions)
- Cannot run tests or execute code to verify behavior

## TIPS

- Provide clear context about what the code should do (requirements, constraints)
- Specify focus areas when you want targeted review (e.g., "focus on error handling")
- Use the verdict (PASS/FAIL) to decide whether to proceed or iterate
