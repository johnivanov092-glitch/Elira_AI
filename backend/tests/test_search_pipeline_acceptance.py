"""Fixed source-to-answer contracts through the real coordinator and executor."""
import json
import os
import re
import shlex
import subprocess
import sys
from types import SimpleNamespace

import pytest

from app.application.code_agent.agent_loop import stream_code_agent
from webskill.application.code_agent.answer_contracts import explicit_web_check_requested


URL = "https://example.org/reference"
FACT = "The selected option is Alpha with 64 GB of memory."


@pytest.mark.parametrize("user_text,expected", [
    ("Проверь официальную документацию и покажи пример.", True),
    ("Check the official documentation.", True),
    ("Найди данные в интернете.", True),
    ("Не нужно проверять в интернете.", False),
    ("Не проверяй сайты. Объясни по приложенному тексту.", False),
    ('Переведи: «Проверь официальную документацию».', False),
    ("Покажи пример вызова web_search.", False),
    ("Прочитай локальную документацию из docs/README.md и объясни настройку.", False),
    ("Проверь документацию из приложенного README, без интернета.", False),
    ("Проверь официальную документацию. Не используй интернет: документ приложен.", False),
    ("Прочитай документацию проекта.", False),
    ("Прочитай приложенный README, затем проверь данные на официальном сайте.", True),
])
def test_only_explicit_source_check_requires_execution(user_text, expected):
    assert explicit_web_check_requested(user_text) is expected






def call(name, **arguments):
    return {"function": {"name": name, "arguments": arguments}}
