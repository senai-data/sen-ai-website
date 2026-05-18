"""Agent (chatbot) package for Sprint 4 in-site assistant.

Public surface :
  - tools.TOOLS         registry of read-only tools the LLM can call
  - tools.run_tool      dispatcher invoked from the chat endpoint
  - tools.tool_schemas  list of Anthropic tool definitions for messages.create

The chat endpoint (api/routers/agent.py) wires this together with the
Anthropic SDK tool-loop. See project_phase_nw_natural_writing.md and the
revised roadmap for the full Sprint 4 plan.
"""
