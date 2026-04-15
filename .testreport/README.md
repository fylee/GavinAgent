# Test Reports

This directory stores test execution results for each spec.

## Naming Convention

| Spec file | Report file |
|-----------|-------------|
| `.spec/027-llm-call-resilience.md` | `.testreport/027-llm-call-resilience.md` |
| `.spec/021.1-skill-authoring-compliance.md` | `.testreport/021.1-skill-authoring-compliance.md` |

The leading number must match the corresponding spec number exactly.

## Report Format

Each report contains:
- **Run date/time**
- **Command used**
- **Summary**: total pass / fail / skip counts
- **Per-test result table**

Reports are replaced in-place on each subsequent run.
