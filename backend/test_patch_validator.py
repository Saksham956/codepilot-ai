
from patch_validator import validate_patch


original = """def add(a, b):
    return a - b
"""

old_code = "return a - b"
new_code = "return a + b"

result = validate_patch(
    original,
    old_code,
    new_code
)

print("Valid:", result["valid"])
print("Syntax valid:", result.get("syntax_valid"))
print("Diff:")
print(result.get("diff"))
