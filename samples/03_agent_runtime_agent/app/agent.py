from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.genai import types

MODEL = "gemini-2.5-flash"


def get_weather(query: str) -> str:
    return "It's 60 degrees and foggy."


root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction="You are a helpful enterprise assistant.",
    tools=[get_weather],
)

app = App(
    root_agent=root_agent,
    name="app",
)
