"""Tests for the hot-pluggable skill registry."""

from my_agent_os.skills_layer.tools import get_tool, list_tools
from my_agent_os.skills_layer.tools.email_handler import EmailHandler


def test_email_handler_registered():
    assert "email" in list_tools()


def test_email_handler_execute():
    tool = get_tool("email")
    result = tool.execute({"to": "ceo@acme.com", "subject": "Q3 Report", "body": "Attached."})
    assert result["success"] is True


def test_email_handler_missing_fields():
    tool = get_tool("email")
    result = tool.execute({"body": "no recipient"})
    assert result["success"] is False
