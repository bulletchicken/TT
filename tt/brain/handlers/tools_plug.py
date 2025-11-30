"""
Centralized tool registry.

To add a new tool, create a file in tt/brain/tools/ and decorate a function with @tool():

    from tt.brain.handlers.tools_plug import tool

    @tool(
        description="Return the current weather for a city.",
        parameters={
            "city": {"type": "string", "description": "City name", "required": True}
        }
    )
    def get_weather(city: str):
        return {"city": city, "temp": 72}

The tool is automatically available to both OpenAI and ElevenLabs.
"""

# Global registry: name -> {"fn": callable, "definition": dict}
_REGISTRY = {}


def tool(description: str, parameters: dict | None = None):
    """
    Decorator to register a tool function.
    
    Args:
        description: What the tool does (shown to the AI).
        parameters: Dict of param_name -> {"type": str, "description": str, "required": bool}
                   If None, the tool takes no parameters.
    """
    def decorator(fn):
        name = fn.__name__
        
        # Build OpenAI-compatible parameter schema
        props = {}
        required = []
        for param_name, param_info in (parameters or {}).items():
            props[param_name] = {
                "type": param_info.get("type", "string"),
                "description": param_info.get("description", ""),
            }
            if param_info.get("required"):
                required.append(param_name)
        
        definition = {
            "type": "function",
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": props,
                **({"required": required} if required else {}),
            },
        }
        
        _REGISTRY[name] = {"fn": fn, "definition": definition}
        return fn
    
    return decorator


# -------------------------------------------------------------------
# API for consumers (OpenAI, ElevenLabs, etc.)
# -------------------------------------------------------------------

def get_tool_definitions():
    """Get all tool definitions in OpenAI format."""
    return [entry["definition"] for entry in _REGISTRY.values()]


def register_with_elevenlabs(client_tools):
    """Register all tools with an ElevenLabs ClientTools instance."""
    for name, entry in _REGISTRY.items():
        client_tools.register(name, entry["fn"])


def run_tool(tool_name: str, args: dict):
    """Execute a tool by name."""
    entry = _REGISTRY.get(tool_name)
    if not entry:
        raise ValueError(f"Unknown tool: {tool_name}")
    return entry["fn"](**(args or {}))

