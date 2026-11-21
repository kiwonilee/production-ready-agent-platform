SESSION_SERVICE_URI = "shared://session"
ARTIFACT_SERVICE_URI = "shared://artifact"


def get_session_service():
    from google.adk.sessions.in_memory_session_service import InMemorySessionService
    return InMemorySessionService()
