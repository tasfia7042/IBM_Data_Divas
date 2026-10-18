You read one piece of PostgreSQL documentation.
List each statement that describes behavior a tester could check by running SQL:
what a function or operator returns, what error it raises, how it treats NULL or
edge values. Copy each statement EXACTLY from the text. Give a short feature name
(usually the function or operator) and a category: normal, boundary or error.
Return JSON: {"requirements": [{"text": "...", "feature": "...", "category": "..."}]}.
Return an empty list if nothing is checkable.
Inside JSON strings, escape every double quote as \" so example calls with JSON values stay complete.

HEADING: {heading}
TEXT: {text}
