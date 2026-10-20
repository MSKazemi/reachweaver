---
type: regex
pattern: '(?:can(?:no|\x27|’)?t|cannot|won\x27t|won’t|will not|no one can|nobody can|not able to|unable to|don\x27t|do not)\s+(?:\w+\s+){0,3}(?:guarantee|promise|predict)'
flags: i
target: last_message
weight: 2
---
