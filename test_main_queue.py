import asyncio
from types import SimpleNamespace

import main as bot_main


class FakeWorkerManager:
    def __init__(self):
        self.submissions = []

    async def submit(self, queue, handler, payload):
        self.submissions.append((queue, handler, payload))

    async def counts(self, _user_id=None):
        return {"pending": 1, "running": 0}


class FakeStatusMessage:
    id = 50

    async def edit_text(self, _text):
        pass


class FakeMessage:
    def __init__(self, text):
        self.text = text
        self.id = 10
        self.from_user = SimpleNamespace(id=123)
        self.chat = SimpleNamespace(id=123)

    async def reply_text(self, _text):
        return FakeStatusMessage()


def test_single_link_queues_without_asking_for_destination(monkeypatch):
    manager = FakeWorkerManager()
    message = FakeMessage("https://t.me/source/10")

    monkeypatch.setattr(bot_main, "get_message_link", lambda _message: "https://t.me/source/10")
    monkeypatch.setattr(bot_main, "get_worker_manager", lambda: manager)

    async def unexpected_question(*_args, **_kwargs):
        raise AssertionError("Single-link processing must not ask for a destination.")

    monkeypatch.setattr(bot_main.app, "ask", unexpected_question)

    asyncio.run(bot_main.single_link(None, message))

    assert manager.submissions[0][0:2] == ("downloads", "single_link")
    assert manager.submissions[0][2]["link"] == "https://t.me/source/10"


def test_batch_no_uses_configured_default_destination(monkeypatch):
    manager = FakeWorkerManager()
    message = FakeMessage("/batch https://t.me/source/10 https://t.me/source/11")
    questions = []
    sent_messages = []
    destination_id = -100987

    async def ask(_chat_id, text, **_kwargs):
        questions.append(text)
        return SimpleNamespace(text="no")

    async def send_message(_chat_id, text):
        sent_messages.append(text)
        return FakeStatusMessage()

    async def get_chat(chat_id):
        return SimpleNamespace(id=chat_id, type="supergroup", title="Destination")

    async def get_chat_member(_chat_id, _user_id):
        return SimpleNamespace(status="member")

    monkeypatch.setattr(bot_main, "get_link", lambda link: link)
    monkeypatch.setattr(bot_main, "get_user_chat_id", lambda _user_id, default: default)
    monkeypatch.setattr(bot_main, "DEFAULT_DESTINATION_ID", destination_id)
    monkeypatch.setattr(bot_main, "get_worker_manager", lambda: manager)
    monkeypatch.setattr(bot_main.app, "ask", ask)
    monkeypatch.setattr(bot_main.app, "send_message", send_message)
    monkeypatch.setattr(bot_main.app, "get_chat", get_chat)
    monkeypatch.setattr(bot_main.app, "get_me", lambda: asyncio.sleep(0, result=SimpleNamespace(id=1)))
    monkeypatch.setattr(bot_main.app, "get_chat_member", get_chat_member)

    asyncio.run(bot_main.batch_link(None, message))

    assert len(questions) == 1
    assert manager.submissions[0][2]["destination_chat_id"] == destination_id


def test_batch_yes_verifies_destination_and_confirms_forum_topic(monkeypatch):
    manager = FakeWorkerManager()
    message = FakeMessage("/batch https://t.me/source/10 https://t.me/source/11")
    answers = iter(("yes", "https://t.me/destination/42", "yes"))
    questions = []
    destination_checks = []

    async def ask(_chat_id, text, **_kwargs):
        questions.append(text)
        return SimpleNamespace(text=next(answers))

    async def send_message(_chat_id, _text):
        return FakeStatusMessage()

    async def get_chat(chat_id):
        destination_checks.append(chat_id)
        return SimpleNamespace(id=-100654, type="supergroup", title="Destination")

    async def get_chat_member(chat_id, user_id):
        assert chat_id == -100654
        assert user_id == 1
        return SimpleNamespace(status="member")

    monkeypatch.setattr(
        bot_main,
        "_get_forum_topics",
        lambda _chat_id: asyncio.sleep(
            0,
            result=[SimpleNamespace(id=42, title="Physics")],
        ),
    )
    monkeypatch.setattr(bot_main, "get_link", lambda link: link)
    monkeypatch.setattr(bot_main, "get_worker_manager", lambda: manager)
    monkeypatch.setattr(bot_main.app, "ask", ask)
    monkeypatch.setattr(bot_main.app, "send_message", send_message)
    monkeypatch.setattr(bot_main.app, "get_chat", get_chat)
    monkeypatch.setattr(bot_main.app, "get_me", lambda: asyncio.sleep(0, result=SimpleNamespace(id=1)))
    monkeypatch.setattr(bot_main.app, "get_chat_member", get_chat_member)

    asyncio.run(bot_main.batch_link(None, message))

    assert len(questions) == 3
    assert destination_checks == ["destination"]
    assert manager.submissions[0][2]["destination_chat_id"] == -100654
    assert manager.submissions[0][2]["destination_thread_id"] == 42
    assert manager.submissions[0][2]["destination_folder"] == "Physics"


def test_batch_forum_root_link_requires_and_uses_a_topic_choice(monkeypatch):
    manager = FakeWorkerManager()
    message = FakeMessage("/batch https://t.me/source/10 https://t.me/source/11")
    answers = iter(("yes", "https://t.me/destination", "Physics", "yes"))
    questions = []
    topics = [
        SimpleNamespace(id=42, title="Physics"),
        SimpleNamespace(id=55, title="Chemistry"),
    ]

    async def ask(_chat_id, text, **_kwargs):
        questions.append(text)
        return SimpleNamespace(text=next(answers))

    async def send_message(_chat_id, _text):
        return FakeStatusMessage()

    async def get_chat(chat_id):
        assert chat_id == "destination"
        return SimpleNamespace(id=-100654, type="supergroup", title="Destination")

    async def get_chat_member(_chat_id, _user_id):
        return SimpleNamespace(status="member")

    async def get_forum_topics(_chat_id):
        return topics

    monkeypatch.setattr(bot_main, "get_link", lambda link: link)
    monkeypatch.setattr(bot_main, "get_worker_manager", lambda: manager)
    monkeypatch.setattr(bot_main.app, "ask", ask)
    monkeypatch.setattr(bot_main.app, "send_message", send_message)
    monkeypatch.setattr(bot_main.app, "get_chat", get_chat)
    monkeypatch.setattr(
        bot_main.app,
        "get_me",
        lambda: asyncio.sleep(0, result=SimpleNamespace(id=1)),
    )
    monkeypatch.setattr(bot_main.app, "get_chat_member", get_chat_member)
    monkeypatch.setattr(bot_main, "_get_forum_topics", get_forum_topics)

    asyncio.run(bot_main.batch_link(None, message))

    assert len(questions) == 4
    assert "Physics" in questions[2]
    assert manager.submissions[0][2]["destination_thread_id"] == 42
    assert manager.submissions[0][2]["destination_folder"] == "Physics"


def test_forum_topic_listing_uses_pyrogram_raw_topics_request(monkeypatch):
    topics = [SimpleNamespace(id=42, title="Physics")]

    async def resolve_peer(chat_id):
        assert chat_id == -100654
        return SimpleNamespace(channel_id=654, access_hash=789)

    async def invoke(request):
        assert isinstance(request, bot_main.raw.functions.channels.GetForumTopics)
        assert request.channel.channel_id == 654
        return SimpleNamespace(topics=topics)

    monkeypatch.setattr(bot_main.app, "resolve_peer", resolve_peer)
    monkeypatch.setattr(bot_main.app, "invoke", invoke)

    result = asyncio.run(bot_main._get_forum_topics(-100654))

    assert result == topics
