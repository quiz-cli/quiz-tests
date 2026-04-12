# quiz-tests

Integration and end-to-end tests for the quiz-cli project. Tests exercise the full
WebSocket communication between quiz-server, quiz-admin and quiz-client(s).

## Goals

- **Realistic multi-party testing** — run the FastAPI app in-process and connect
  WebSocket clients (admin + players), verifying the complete message flow.
- **Test-driven** — each test case is a YAML file describing the sequence of
  actions for all parties, making tests readable and easy to author.
- **Separation of framework and test cases** — the test runner/framework is
  independent of individual test cases.
- **Dual mode** — interactive with verbose output for local development, fully
  automated for GitHub Actions.

## Recommended Python Stack

| Library | Role | Why |
|---|---|---|
| **pytest** | Test runner and discovery | De facto standard; rich plugin ecosystem; parametrize support for loading YAML test cases as individual tests. |
| **pytest-asyncio** | Async test support | Allows `async def` test functions and async fixtures; needed because all WebSocket communication is async. |
| **httpx** | HTTP/ASGI client | `httpx.AsyncClient` with `ASGIWebSocketTransport` calls the FastAPI app directly in-process — no server process, no network, no ports. |
| **httpx-ws** | WebSocket client for ASGI | Provides `ASGIWebSocketTransport` and `aconnect_ws()` — the key to opening multiple WebSocket connections against the FastAPI app without starting uvicorn. Each connection gets an `AsyncWebSocketSession` with `send_text()`, `send_json()`, `receive_text()`, `receive_json()`, and `close()`. |
| **ruamel.yaml** | YAML parsing | Already used across the project; parses both quiz data and test YAML files. |
| **pydantic** | Test validation | Validate the YAML test structure at load time so malformed tests fail fast with clear errors. |
| **pytest-timeout** | Hang prevention | Sets a hard wall-clock timeout per test; essential for WebSocket tests that could block forever on `receive_*()`. |

### Why httpx-ws instead of uvicorn + websockets?

The `httpx-ws` library provides `ASGIWebSocketTransport` — a custom HTTPX transport
that calls the ASGI application directly, in the same process and event loop:

```python
from httpx_ws import aconnect_ws
from httpx_ws.transport import ASGIWebSocketTransport

async with httpx.AsyncClient(transport=ASGIWebSocketTransport(app)) as client:
    async with aconnect_ws("http://server/admin", client) as ws:
        await ws.send_json(quiz_data)
        response = await ws.receive_text()
```

Benefits over the uvicorn + websockets approach:

- **No server process** — no port allocation, no startup delays, no cleanup.
- **Deterministic** — everything runs in one event loop; no network timing issues.
- **Simpler CI** — no need to wait for a port to become available; no firewall or
  port-conflict concerns in GitHub Actions runners.
- **Faster** — zero I/O overhead; messages pass through in-memory ASGI calls.
- **Multiple simultaneous connections** — each `aconnect_ws()` opens an independent
  WebSocket session against the same `app` instance, sharing `app.state` exactly
  as they would in production.

### Optional / nice-to-have

| Library | Role |
|---|---|
| **pytest-xdist** | Parallel test execution (`-n auto`). Each test case resets shared `app` state independently, so tests can run in parallel. |
| **rich** | Coloured step-by-step output for interactive local runs. |

## Where Do quiz-admin and quiz-client Fit?

**They are NOT dependencies of quiz-tests.** The test framework does not import or
run quiz-admin or quiz-client code. Instead, the framework's actor classes
replicate the same WebSocket protocol directly using `httpx-ws`:

| Real component | What it does over WebSocket | Test actor equivalent |
|---|---|---|
| **quiz-admin** | Connects to `/admin`, sends quiz JSON immediately after connect, sends `"y"` to proceed, receives question previews and results | `AdminActor` — calls `aconnect_ws("http://server/admin", client)`, sends quiz JSON on connect, then `send_text()` / `receive_*()` |
| **quiz-client** | Connects to `/connect/{name}`, receives questions, sends `{"client_id": ..., "answer": ...}` | `ClientActor` — calls `aconnect_ws("http://server/connect/{name}", client)`, then `send_json()` / `receive_*()` |

The protocol is simple enough (JSON messages over WebSocket) that thin actor
wrappers are sufficient. This keeps quiz-tests decoupled — it tests the **server's
contract**, not the client implementations. If quiz-admin or quiz-client have bugs
in their own message formatting, those are caught by their own unit tests; if the
server mishandles a valid protocol sequence, quiz-tests catches it.

The only shared dependency is **quiz-common** (for the `Quiz` / `Question` /
`Option` Pydantic models used to load and validate quiz YAML data) and
**quiz-server** (to import the FastAPI `app` object).

## Architecture

```
quiz-tests/
├── pyproject.toml
├── README.md
├── LICENSE
├── test_run_tests.py        # pytest entry point; discovers and parametrizes test cases
├── framework/
│   ├── __init__.py
│   ├── app.py               # Reset shared app state for each test
│   ├── actors.py            # WebSocket client wrapper (Actor)
│   ├── runner.py            # Test step interpreter (the core engine)
│   ├── models.py            # Pydantic models for test YAML validation
│   └── assertions.py        # Helpers for message matching / comparison
└── tests/
    ├── basic_flow/
    │   ├── quiz.yaml         # Quiz data for this test
    │   └── test.yaml         # Sequence of actions
    ├── player_disconnect/
    │   ├── quiz.yaml
    │   └── test.yaml
    ├── two_players_answer/
    │   ├── quiz.yaml
    │   └── test.yaml
    └── ...
```

### Framework components

**`framework/app.py`** — Resets shared `ClassVar` state on the quiz-server FastAPI
`app` before each test. `Players._players` and `Results._results` are cleared in-place,
and `app.state.quiz` / `app.state.admin` are deleted, ensuring each test starts
clean. The same `app` object is reused (avoiding route re-registration) and passed
to `ASGIWebSocketTransport`.

**`framework/actors.py`** — A single `Actor` class wrapping `httpx-ws`'s
`AsyncWebSocketSession`. Each actor holds a reference to the shared
`httpx.AsyncClient` (with `ASGIWebSocketTransport`) and manages its own WebSocket
connection lifecycle. Methods: `connect()`, `send()`, `receive_raw()`,
`expect_nothing()`, and `disconnect()`.

`Actor.connect()` routes to `/admin` for `role: admin` actors (sending quiz data
as the first JSON payload immediately after the handshake) or to `/connect/{name}`
for `role: client` actors.

Connection lifecycle is managed via `contextlib.AsyncExitStack` — each
`aconnect_ws()` context manager is entered when the `connect` step runs and
cleaned up either by an explicit `disconnect` step or at the end of the test.
A `_safe_aconnect_ws` wrapper suppresses benign `EndOfStream` / `WebSocketDisconnect`
exceptions that arise when the server or test closes the connection.

**`framework/runner.py`** — The core test engine. Takes a parsed `TestCase`
(list of steps) and executes them sequentially via `run_test(test_dir)`. Each
step is dispatched to the appropriate actor. This is the single place that
understands the step vocabulary; adding a new action type means adding one
handler here.

**`framework/models.py`** — Pydantic models (`TestCase`, `ActorDef`, `Step`) that
validate test YAML files at load time. A malformed test produces a clear
validation error instead of a cryptic runtime failure.

**`framework/assertions.py`** — Helpers for matching received WebSocket messages:
exact match, substring, JSON structure match, regex, and field-level comparisons.

**`test_run_tests.py`** — Discovers all `tests/*/test.yaml` directories and
parametrizes them into individual pytest test cases via
`@pytest.mark.parametrize`. Each test case calls `run_test(test_dir)`.

## Test YAML Format

A test is a YAML file describing the full interaction between all parties. The
runner processes steps **sequentially from top to bottom** — this is what makes
multi-party choreography explicit and deterministic.

### Top-level structure

```yaml
---
name: "Basic quiz flow with two players"
description: >
  Admin starts the quiz, two players connect, answer both questions,
  quiz ends normally.

quiz_file: quiz.yaml          # path relative to the test directory
# OR inline:
# quiz:
#   name: Inline example quiz
#   questions:
#     - text: "What is 1+1?"
#       options:
#         - answer: "2"
#           correct: true
#         - answer: "3"
#           correct: false

actors:
  admin:
    role: admin                # connects to /admin — exactly one per test
  alice:
    role: client               # connects to /connect/alice
  bob:
    role: client               # connects to /connect/bob
```

### How actor roles work

The `role` field explicitly tells the framework **which WebSocket endpoint** to
use and **which actor class** to instantiate:

| Role | Endpoint | Behaviour on `connect` |
|---|---|---|
| `admin` | `/admin` | Opens WebSocket to `/admin`. On `connect`, automatically sends the quiz data as the first JSON message (matching real quiz-admin behaviour). |
| `client` | `/connect/{name}` | Opens WebSocket to `/connect/{actor_name}`, where `actor_name` is the key in the `actors` block (e.g. `alice`, `bob`). |

This removes any ambiguity — you can name actors freely (e.g. `teacher`, `host`,
`player_1`, `student_a`) as long as the `role` field is set correctly:

```yaml
actors:
  host:
    role: admin
  student_a:
    role: client
  student_b:
    role: client
```

Typically one actor with `role: admin` and one or more with `role: client` are
used. However, **multiple admins are allowed** — the server does not officially
support this, but we need to test how it handles a second admin connecting
(e.g. does it crash? does it overwrite the quiz? does the first admin get
disconnected?). Having this flexibility in the test format lets us write
adversarial test cases without any framework changes.

### Step vocabulary

Each step has an `actor`, an `action`, and action-specific fields.

#### `connect`

Open a WebSocket connection to the server.

```yaml
- actor: admin
  action: connect

- actor: alice
  action: connect
```

For `role: admin` actors, `connect` also **sends the quiz data** as the first
JSON message immediately after the WebSocket handshake. The quiz data is loaded
from `quiz_file` (or the inline `quiz` block) and serialised via the
`Quiz` Pydantic model. This mirrors the real quiz-admin client, which sends the
quiz payload right after connecting.

For `role: client` actors, `connect` opens a WebSocket to `/connect/{name}`,
where `{name}` is the actor key from the `actors:` block.

#### `send`

Send a raw text or JSON message.

```yaml
# Admin sends "y" to proceed to next question
- actor: admin
  action: send
  data: "y"

# Player sends an answer
- actor: alice
  action: send
  data:
    client_id: alice
    answer: "a"
```

#### `expect`

Wait for the next message and assert its content. Blocks until a message arrives
(subject to the per-step or global timeout).

```yaml
# Expect exact text
- actor: admin
  action: expect
  text: 'Admin for the quiz "Simple example quiz to show how does it work"'

# Expect JSON with specific fields
- actor: alice
  action: expect
  json:
    type: question
    text: "This question has one correct answer which is A"

# Expect a substring in the message
- actor: admin
  action: expect
  contains: "player alice connected"

# Expect a message matching a regex
- actor: admin
  action: expect
  matches: "\\d+\\. player \\w+ connected"
```

#### `expect_nothing`

Assert that no message arrives within a given time. Useful for verifying that
a blocked player does not receive a duplicate response.

```yaml
- actor: bob
  action: expect_nothing
  timeout: 0.5
```

#### `disconnect`

Close the WebSocket connection. The framework does NOT automatically expect any
message from other actors — you write explicit `expect` steps for the other
parties to verify they handle the disconnection.

```yaml
- actor: bob
  action: disconnect

# Verify the server notifies admin about the disconnect
- actor: admin
  action: expect
  contains: "Player bob disconnected"
```

#### `sleep`

Wait for a specified duration. Use sparingly — prefer `expect` with timeouts.

```yaml
- actor: admin
  action: sleep
  seconds: 0.5
```

#### `comment`

No-op annotation for readability. Printed in verbose mode.

```yaml
- action: comment
  text: "--- Round 1: first question ---"
```

### Full example test

```yaml
---
name: "Two players, one disconnects mid-quiz"
description: >
  Admin starts a 2-question quiz. Two players connect. Bob disconnects
  after the first question. Alice finishes alone.

quiz_file: quiz.yaml

actors:
  admin:
    role: admin
  alice:
    role: client
  bob:
    role: client

steps:
  # --- Setup (connect sends quiz data automatically for admin) ---
  - actor: admin
    action: connect
  - actor: admin
    action: expect
    contains: "Admin for the quiz"

  - actor: alice
    action: connect
  - actor: alice
    action: expect
    json:
      text: "Simple example quiz to show how does it work"
  - actor: alice
    action: expect
    contains: "Check your name"

  - actor: admin
    action: expect
    contains: "player alice connected"

  - actor: bob
    action: connect
  - actor: bob
    action: expect
    json:
      text: "Simple example quiz to show how does it work"
  - actor: bob
    action: expect
    contains: "Check your name"

  - actor: admin
    action: expect
    contains: "player bob connected"

  # --- Question 1 ---
  - action: comment
    text: "Admin proceeds to question 1"
  - actor: admin
    action: send
    data: "y"

  - actor: alice
    action: expect
    json:
      type: question
  - actor: bob
    action: expect
    json:
      type: question
  - actor: admin
    action: expect
    json:
      type: question

  - actor: alice
    action: send
    data:
      client_id: alice
      answer: "a"
  - actor: alice
    action: expect
    json:
      type: repeat

  # --- Bob disconnects ---
  - actor: bob
    action: disconnect
  - actor: admin
    action: expect
    contains: "Player bob disconnected"

  # --- Question 2 (only Alice) ---
  - actor: admin
    action: send
    data: "y"

  - actor: alice
    action: expect
    json:
      type: question
  - actor: admin
    action: expect
    json:
      type: question

  - actor: alice
    action: send
    data:
      client_id: alice
      answer: "c"
  - actor: alice
    action: expect
    json:
      type: repeat

  # --- Quiz ends ---
  - actor: admin
    action: send
    data: "y"
  - actor: admin
    action: expect
    json: []              # results list
```

## How It Works at Runtime

1. **pytest collects tests** — `test_run_tests.py` globs `tests/*/test.yaml`,
   parametrizes a single `test_case` function with `ids=lambda d: d.name`, so
   each directory becomes a named test (e.g. `test_case[basic_flow]`).

2. **State reset** — `reset_app()` clears `Players._players`, `Results._results`,
   and deletes `app.state.quiz` / `app.state.admin` on the shared FastAPI `app`
   object. The cleaned app is wrapped in `ASGIWebSocketTransport(app)` and an
   `httpx.AsyncClient` is created with that transport. All actor connections go
   through this single client.

3. **Actors are created** — For each entry in `actors:`, the framework reads the
   `role` field and instantiates an `Actor`. Each actor holds a reference to the
   shared `httpx.AsyncClient` but is not yet connected. An `AsyncExitStack`
   manages the lifecycle of all `aconnect_ws()` context managers.

4. **Steps execute sequentially** — The runner iterates through `steps:` and
   dispatches each action to the actor:
   - `connect` → calls `aconnect_ws("http://test/{endpoint}", client)` via the
     exit stack and stores the resulting `AsyncWebSocketSession` on the actor.
     Admin actors additionally send quiz JSON immediately after connecting.
   - `send` → calls `ws.send_text()` or `ws.send_json()`.
   - `expect` → calls `ws.receive_text(timeout=...)` and runs assertion logic.
   - `disconnect` → calls `ws.close()`, which triggers `WebSocketDisconnect` in the
     server handler (same event loop, same process).
   - `expect_nothing` → calls `ws.receive_text(timeout=...)` and expects `TimeoutError`.

5. **Assertions fire inline** — `expect` steps call `pytest.fail()` if the received
   message does not match, giving you a clear diff and the step number.

6. **Cleanup** — The `AsyncExitStack` closes all remaining WebSocket sessions.
   The `httpx.AsyncClient` is closed. No ports or processes to release.

## Running Locally

### Prerequisites

```bash
# From the quiz-tests directory
uv sync
```

### Run all tests

```bash
uv run pytest
```

### Run a single test

```bash
uv run pytest -k "basic_flow"
```

### Verbose / interactive mode

Prints every step as it executes, including sent/received messages:

```bash
uv run pytest -v -s --tb=short
```

### Step-through mode (debugging)

For truly interactive debugging, run a specific test with maximum verbosity
and `--timeout=0` (no timeout) so you can inspect state at your own pace:

```bash
uv run pytest -k "player_disconnect" -v -s --timeout=0
```

## GitHub Actions

```yaml
name: Integration tests

on:
  push:
    branches: [main]
  pull_request:

jobs:
  integration:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v6

      - name: Set up Python
        run: uv python install

      - name: Install dependencies
        working-directory: quiz-tests
        run: uv sync

      - name: Run integration tests
        working-directory: quiz-tests
        run: uv run pytest --timeout=30 --tb=short -q
```

No special configuration is needed — there is no external server to provision.
The tests run the FastAPI app in-process via `ASGIWebSocketTransport`. Each
test runs against a freshly reset `app`, so tests can run in parallel with
`pytest-xdist` if desired:

```yaml
      - name: Run integration tests (parallel)
        working-directory: quiz-tests
        run: uv run pytest --timeout=30 -n auto -q
```

## Writing a New Test Case

1. Create a new directory under `tests/`:

   ```
   tests/my_new_test/
   ```

2. Add or symlink a quiz YAML file:

   ```
   tests/my_new_test/quiz.yaml
   ```

3. Write the test:

   ```
   tests/my_new_test/test.yaml
   ```

4. Run it:

   ```bash
   uv run pytest -k "my_new_test" -v -s
   ```

No Python code needed for a new test case — only YAML.

## Test Cases

| Test case | Status | What it verifies |
|---|---|---|
| `basic_flow` | ✅ implemented | Admin starts quiz, one player connects, answers all questions, quiz ends normally. |
| `two_players_answer` | ✅ implemented | Two players connect and answer; verify both receive questions and their answers are recorded. |
| `player_disconnect` | ✅ implemented | A player disconnects mid-quiz; admin is notified, remaining player continues. |
| `no_players` | ✅ implemented | Admin runs through the entire quiz with no players connected. |
| `duplicate_answer` | ✅ implemented | A player tries to send two answers for the same question; only the first is accepted. |
| `connect_before_quiz` | ✅ implemented | A player tries to connect before the admin has started the quiz; verify the connection is rejected with reason. |
| `admin_disconnect` | planned | Admin disconnects unexpectedly; verify server and players handle it. |
| `two_admins` | planned | A second admin connects while first is active; verify server behaviour (unsupported but must not crash). |
| `late_join` | planned | A player connects after the first question has already been sent. |
| `invalid_message` | planned | A player sends malformed JSON; verify the server does not crash. |
| `many_players` | planned | Stress test with 10+ concurrent players to verify broadcast and result collection. |

## Dependencies

```toml
# pyproject.toml (quiz-tests)
[project]
name = "quiz-tests"
requires-python = ">=3.13"
dependencies = [
    "pytest>=8.0",
    "pytest-asyncio>=0.25",
    "pytest-timeout>=2.3",
    "httpx>=0.28",
    "httpx-ws>=0.7",
    "ruamel-yaml>=0.18",
    "pydantic>=2.12",
    "quiz-common",
    "quiz-server",
]

[tool.uv.sources]
quiz-common = { path = "../quiz-common" }
quiz-server = { path = "../quiz-server" }

[dependency-groups]
dev = [
    "ruff>=0.14",
    "ty>=0.0.29",
]
```

**quiz-server** is a dependency so the framework can import the FastAPI `app`
object directly (`from main import app`) and pass it to `ASGIWebSocketTransport` —
no subprocess, no uvicorn, no network.

**quiz-admin and quiz-client are NOT dependencies.** The test actors replicate
the WebSocket protocol directly. This keeps quiz-tests decoupled from client
implementation details.
