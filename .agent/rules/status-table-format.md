---
trigger: always_on
---
# Mandatory `Status` / `status` Response Format
When the user sends a message matching `Status` or `status` (case-insensitive, with or without trailing punctuation):
1. Immediately run `git pull --ff-only origin main && python main.py --cycle status` in the workspace terminal.
2. Output the resulting 5 Markdown tables directly in your response.
3. Do NOT convert the tables back into bullet lists or long prose paragraphs. Keep any commentary to at most 1–2 concise lines below the tables only if a critical alert or action item exists.
