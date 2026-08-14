# Jarvis Agent Rules

## Safety
- Do not git push unless explicitly requested.
- Do not delete project files without explicit approval.
- Do not use sudo unless required and approved.

## Development
- Preserve existing working behavior unless the task requires changing it.
- Run relevant tests after behavioral changes.
- Do not let parent agent and subagent edit the same file concurrently.

## Desktop / Chrome
- Revalidate window/process/profile ownership before closing or modifying Chrome.
- Do not rely only on window title to determine ownership.
