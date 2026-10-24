import pytest

from minibot.config import BotConfig
from minibot.ollama_client import OllamaClient
from minibot.sandbox import Sandbox
from minibot.state import BotStore
from minibot.tools import build_toolset


@pytest.fixture
def config(tmp_path, monkeypatch) -> BotConfig:
    for var in ("OLLAMA_HOST", "MINIBOT_OLLAMA_URL", "MINIBOT_MODEL", "MINIBOT_SANDBOX"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MINIBOT_HOME", str(tmp_path / "home"))
    return BotConfig(
        sandbox_path=str(tmp_path / "sandbox"),
        state_path=str(tmp_path / "state.json"),
        log_path=str(tmp_path / "minibot.log"),
        ping_timeout_s=0.5,
        request_timeout_s=5.0,
    )


@pytest.fixture
def store(config) -> BotStore:
    return BotStore(config.state_path, config.decay, default_name=config.bot_name)


@pytest.fixture
def sandbox(config) -> Sandbox:
    return Sandbox(config.sandbox_path, max_file_bytes=config.max_file_bytes)


@pytest.fixture
def toolset(store, sandbox):
    return build_toolset(store, sandbox)


@pytest.fixture
def unused_port() -> int:
    """A localhost port that is (very likely) closed — for unreachable-URL tests."""
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def client(config, unused_port) -> OllamaClient:
    return OllamaClient(
        f"http://127.0.0.1:{unused_port}", config.model, timeout=5.0, ping_timeout=0.5
    )
