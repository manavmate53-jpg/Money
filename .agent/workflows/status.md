---
description: Sync repository and output the 5 structured Markdown bot status tables
---
# `/status` Workflow

1. Pull the latest automated GitHub Actions state and generate the 5-table telemetry report:
```bash
git pull --ff-only origin main && python main.py --cycle status
```
2. Print all 5 resulting Markdown tables directly in the chat response without turning them into bullet lists or prose paragraphs. Keep any footer note to at most 1–2 concise lines only if a critical alert exists.
