"""Tool: get_user_profile"""

from tt.brain.handlers.tools_plug import tool


@tool(description="Return the user's profile info.")
def get_user_profile():
    return {
        "name": "Alex Rivera",
        "location": "San Francisco",
        "role": "Product Manager",
    }

