# Custom plugins directory
# Place your .py plugin files here.
# Each plugin must export:
#   TOOLS: list[dict] — Claude API tool schemas
#   execute(name, args) → str — async function to handle tool calls
