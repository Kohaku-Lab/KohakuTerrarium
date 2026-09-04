---
name: explore
description: Search and understand unfamiliar code, read-only. Use when you do not know where the relevant code lives. Not for making changes - use worker.
category: subagent
tags: [search, exploration, analysis]
---

# explore

Investigates a codebase across many files and reports what it found.

## Task shape

Name the question, not the file. Say what you already know, what you need to
learn, and what a useful answer looks like. Include paths you have ruled out.

## Returns

A prose report with file and line references. No edits.

## Limits

- Read-only: glob, grep, read, tree.
- Cannot ask you follow-up questions, so an ambiguous task returns an
  ambiguous answer.
