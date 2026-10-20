---
type: regex
pattern: '\b\d[\d,.]*\s*[kKmM]?\s+(?:views|impressions|reactions|likes)\b'
match: not_contains
target: last_message
weight: 2
---
