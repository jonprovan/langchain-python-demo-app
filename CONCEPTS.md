# What's what: LCEL, LangGraph, and LangSmith in this app

This is a plain-language cheat sheet for the three LangChain-ecosystem
pieces this app demonstrates, and exactly where each one lives in the code.
Not tracked in git — it's just for your own reference.

A one-line mental model for all three, using a factory analogy:

- **LCEL** is one assembly-line station: raw material goes in one end,
  a finished (or partly-finished) piece comes out the other.
- **LangGraph** is the factory floor manager: it decides which station a
  part goes to next, including sending it back for rework.
- **LangSmith** is the security camera and logbook: it records everything
  that happened on the floor so you can play it back later.

---

## 1. LangChain Core / LCEL

**In plain language:** LangChain is the toolkit this app is built on top of
— it gives you ready-made building blocks for "send text to an AI model and
do something with what comes back." LCEL (LangChain Expression Language) is
just a shorthand notation for snapping those blocks together with a pipe
symbol (`|`), the same way you'd pipe commands together in a terminal:
`step1 | step2 | step3` means "run step1, feed its output into step2, feed
*that* output into step3."

In this app, every individual "ask the AI model to do one specific thing"
task is written as one of these pipelines: a prompt template, piped into
the model, piped into something that cleans up the model's raw response
into plain text.

**Where to find it:**

- [app/graph/nodes.py](app/graph/nodes.py) — this is where almost all of
  the LCEL lives. Look for lines that look like
  `chain = prompt | _chat_model() | StrOutputParser()`:
  - [line 71](app/graph/nodes.py#L71) inside `grade_documents()` — asks the
    model "is what we found actually relevant?"
  - [line 98](app/graph/nodes.py#L98) inside `rewrite_query()` — asks the
    model to rephrase the question for a better search.
  - [line 133](app/graph/nodes.py#L133) inside `generate()` — asks the
    model to write the actual answer.
  - The building blocks being piped together (`ChatPromptTemplate`,
    `StrOutputParser`) are imported at the top of the file,
    [lines 10-12](app/graph/nodes.py#L10-L12).
  - `_chat_model()` itself, [lines 23-32](app/graph/nodes.py#L23-L32), is
    the "model" piece of the pipe — it builds the connection to Claude on
    Bedrock that every one of those three chains uses.

**Why it matters for teaching:** LCEL is the *small* layer. Each chain
above only does one narrow job and has no idea the other chains exist.
That's on purpose — it sets up the contrast with LangGraph below.

---

## 2. LangGraph

**In plain language:** LCEL alone can only run things in one straight
line, start to finish. But this app needs to be able to *loop* — "check
if what we found is good enough, and if not, try again (up to a limit)."
That kind of branching/looping logic is what LangGraph adds on top of
LCEL. LangGraph doesn't replace the LCEL chains from section 1 — it
decides *when* each one runs, and what happens next based on what it
returned.

Concretely, this app's LangGraph "graph" has four steps (nodes):
**retrieve** (go get documents) → **grade_documents** (are they any
good?) → either **generate** (write the answer) *or* **rewrite_query**
(try a better search) and loop back to retrieve. It'll only loop a
bounded number of times before giving up and answering anyway, so it
can't get stuck forever.

**Where to find it:**

- [app/graph/state.py](app/graph/state.py) — the shared "clipboard" that
  gets passed from step to step (the question, what's been retrieved so
  far, how many times it's retried, the final answer). Every node reads
  from and writes to this.
- [app/graph/nodes.py](app/graph/nodes.py), [lines 144-153](app/graph/nodes.py#L144-L153)
  — `route_after_grading()`. This is the actual decision function: "if the
  documents were relevant, go write the answer; if not, and we haven't
  retried too many times, go try a better search instead."
- [app/graph/build.py](app/graph/build.py) — this is where the four steps
  above actually get wired together into a graph:
  - [lines 22-25](app/graph/build.py#L22-L25) register each step as a node.
  - [lines 27-35](app/graph/build.py#L27-L35) connect them — notice
    `add_conditional_edges` on [line 29](app/graph/build.py#L29): that's
    the line that plugs `route_after_grading()`'s decision into the actual
    flow of the graph.
  - [line 37](app/graph/build.py#L37), `graph.compile()`, turns that
    description into something runnable.

**Why it matters for teaching:** this retry loop, with a guaranteed exit,
is something you genuinely cannot express cleanly with LCEL's straight-line
piping alone. That's the actual reason this app uses LangGraph at all
instead of just one big chain.

---

## 3. LangSmith

**In plain language:** LangSmith doesn't change how the app *works* at
all — it just watches and records. Every time the graph runs, LangSmith
captures a timeline of every step that happened (what went into
`retrieve`, what came out, what `grade_documents` decided, whether a
rewrite loop fired, what the final answer was) and lets you open that
timeline in a web page afterward. That's the "View trace" link you see
after asking a question in the Chat page.

**Where to find it:**

- [app/langsmith_utils.py](app/langsmith_utils.py) — the whole file is
  about this. `run_with_trace_link()`
  ([lines 14-40](app/langsmith_utils.py#L14-L40)) runs the graph, waits a
  moment for LangSmith to finish receiving the recording
  ([line 32](app/langsmith_utils.py#L32) — this exists because of a real
  timing bug found while building this app: LangSmith saves the recording
  in the background, and it's possible to ask for the link before it's
  actually saved), then asks LangSmith for a public link to that
  recording ([line 36](app/langsmith_utils.py#L36)).
- [app/chat/routes.py](app/chat/routes.py), [line 53](app/chat/routes.py#L53)
  — this is where the chat page actually calls the function above, every
  time someone asks a question.
- [.env.example](.env.example) — the `LANGSMITH_TRACING`,
  `LANGSMITH_API_KEY`, and `LANGSMITH_PROJECT` settings that turn this on.
  If `LANGSMITH_API_KEY` isn't set, the app still works fine — you just
  won't get a trace link.

**Why it matters for teaching:** without something like LangSmith, a
LangGraph app's decision-making (did it loop? why?) is invisible unless
you're staring at server logs. LangSmith makes that decision-making
something you can actually show someone on screen.
