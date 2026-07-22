# Trainer Walkthrough

A facilitator's script for demoing this app live. Written for someone who
has read the README but hasn't necessarily read every line of code — it
tells you what to say, what to show, and what to expect on screen.

**Audience:** SkillStorm trainees with basic Python/Flask familiarity, being
introduced to LangChain Core (LCEL), LangGraph, LangSmith, and Bedrock
Knowledge Bases.

**Suggested length:** 30-40 minutes (15 min code tour, 15-20 min live demo,
5 min Q&A).

---

## 0. Before you present

- [ ] `scripts/provision_kb.py` has been run and `.env` has
      `KNOWLEDGE_BASE_ID`, `DATA_SOURCE_ID`, `KB_DATA_BUCKET` filled in.
- [ ] Bedrock model access for Anthropic Claude models is enabled in the
      target AWS account (Bedrock console -> Model access). If you set this
      up recently, allow up to ~15 minutes for it to propagate — see the
      Troubleshooting appendix if a call fails with "Model use case details
      have not been submitted."
- [ ] `LANGSMITH_API_KEY` is set in `.env` (get one at
      [smith.langchain.com](https://smith.langchain.com) -> Settings -> API
      Keys) and `LANGSMITH_TRACING=true`.
- [ ] `flask --app wsgi run` starts cleanly and `/documents/upload` does
      **not** show the "Knowledge Base is not configured" warning.
- [ ] Have `samples/policy_fact.txt` handy (already in the repo) — this is
      the demo document with a fabricated fact no LLM could already know.
- [ ] Have a second browser tab open to [smith.langchain.com](https://smith.langchain.com)
      logged into the account whose API key is in `.env`, so you can show
      the trace dashboard alongside the public share link.

---

## 1. Frame the problem (2 min, no screen)

Say something like:

> "We're building one RAG chatbot, but we're going to use it to show four
> different pieces of the LangChain ecosystem that normally get taught in
> isolation: LCEL, LangGraph, LangSmith, and a fully-managed AWS RAG
> backend. By the end, you should be able to say *why* each one exists,
> not just what it does."

---

## 2. Code tour

Open the repo and walk through in this order. Don't read code line by
line — point at the shape of things and let the docstrings fill in detail.

### 2a. Architecture (show `README.md`'s diagram)

```
retrieve -> grade_documents -> [relevant] -> generate -> END
                             -> [irrelevant, retries left] -> rewrite_query -> retrieve (loop)
                             -> [irrelevant, retries exhausted] -> generate (low-confidence) -> END
```

Teaching point: this is **Corrective RAG**. A naive RAG chain retrieves once
and generates — if retrieval was bad, you get a confident wrong answer. This
graph grades what it retrieved first and can retry with a rewritten query.

### 2b. `app/graph/state.py`

The `GraphState` TypedDict is the contract every node reads/writes. Point
out `rewrite_count` specifically — ask the group: "why do we need a
counter here at all?" (Answer: to guarantee the graph terminates instead of
looping forever on a question the KB genuinely can't answer.)

### 2c. `app/graph/nodes.py` — the LCEL/LangGraph split

This is the core teaching moment. Show `generate()` or `grade_documents()`
and point at:

```python
chain = prompt | _chat_model() | StrOutputParser()
```

Say: "This one line — prompt piped into a model piped into a parser — is
LCEL. It's just function composition. Notice every node builds its own
tiny LCEL chain. **LangGraph doesn't replace LCEL — it orchestrates it.**
LangGraph owns the *control flow* (which node runs next, the retry loop);
LCEL owns each individual model call."

Then show `route_after_grading()` — this is the conditional-edge function
LangGraph uses to decide whether to move to `generate` or loop back through
`rewrite_query`. This branching logic is exactly what LCEL can't express
cleanly on its own — there's no clean way to say "loop until X or N tries"
in a linear chain.

### 2d. `app/graph/build.py`

Show `graph.add_conditional_edges(...)` wiring `route_after_grading`'s
return value to actual node names. Mention `get_graph().get_graph().draw_mermaid()`
(available in a Python shell) as a way to generate the diagram in section
2a directly from the compiled graph — useful for keeping docs in sync with
code.

### 2e. `app/langsmith_utils.py`

Short one: every chat request gets a fresh `run_id`, the graph is invoked
with it, then `share_run(run_id)` gets a public URL — no LangSmith login
needed for whoever you send the link to. Worth calling out the comment
about `wait_for_all_tracers()`: LangChain ships traces to LangSmith on a
background thread, so without that call the share link would frequently
come back broken because the run hadn't landed on LangSmith's servers yet.
That's a real bug this app hit during development, not a hypothetical.

### 2f. `app/bedrock_clients.py` and the auth story

One sentence: everything — chat calls *and* Knowledge Base retrieval calls
— goes through the same IAM credential chain. Worth mentioning Bedrock API
keys (the bearer-token shortcut) don't cover Knowledge Base retrieval calls,
only `bedrock-runtime`, which is why this app doesn't use them.

---

## 3. Live demo

Switch to the running app (`flask --app wsgi run`) in a browser.

### 3a. Upload the demo document

1. Go to **Upload**.
2. Upload `samples/policy_fact.txt`.
3. While it's ingesting, explain: "Bedrock is chunking, embedding, and
   indexing this into the OpenSearch Serverless vector store right now —
   we didn't write any of that logic, it's fully managed."
4. Wait for the page to report ingestion complete (usually under a minute).
   If you (or a trainee) jump to the Chat page early, `app/kb_status.py`
   checks the most recent ingestion job's status and blocks the question
   with a clear "still being ingested" message instead of silently
   answering against a stale index — worth pointing out as a real
   async-consistency issue this app had to account for, not just a Flask
   nicety.

### 3b. Ask the question only this document can answer

Go to **Chat** and ask:

> What is the SkillStorm training lab access policy number?

Expected result:
- Answer states **TRN-88421-Q**.
- Citations list shows the S3 source file (`policy_fact.txt`).
- "View trace" link appears.

Click **View trace**. In the LangSmith UI, point out the span tree:
`retrieve -> grade_documents -> generate`, and that `grade_documents`
graded the retrieval "relevant" so no rewrite happened. This is the trace
proving the answer came from retrieval, not the model's own training data
(a fabricated policy number like this can't be in any base model's
knowledge).

### 3c. Ask something the document can't answer (the correction loop)

Still on **Chat**, ask:

> What is the boiling point of mercury in Celsius?

Expected result:
- The answer explicitly says the document doesn't cover this, then
  (correctly) hedges into general knowledge for the actual number.
- The response's `rewrite_count` will be 2 — the graph tried rewriting the
  query twice, hit `MAX_REWRITES`, and generated anyway with the
  low-confidence framing instead of looping forever.

Open this trace too and show the longer span tree: two full
`retrieve -> grade_documents -> rewrite_query` cycles before the final
`generate`. This is the moment to say: "this loop, with a hard stop, is
the specific thing LangGraph gives you that a linear LCEL chain can't."

---

## 4. Wrap-up discussion prompts

Use whichever fit your remaining time:

- "What would change in this graph if we wanted to grade *individual*
  retrieved chunks instead of the whole batch?"
- "Why does `generate()` get a different system prompt when
  `rewrite_count >= MAX_REWRITES`? What would happen to trust in this tool
  if it didn't?"
- "This app uses a single IAM identity for everything. When would you
  split that into separate roles?"
- "The vector index and embedding dimension are hardcoded to match in two
  separate files (`scripts/provision_kb.py`'s `EMBEDDING_DIMENSION` and the
  Knowledge Base config it creates). What's a way to make that mismatch
  impossible instead of just documented?"

---

## Troubleshooting appendix

Real issues hit while building/testing this app — worth knowing before a
live session so a hiccup doesn't derail you.

**`ResourceNotFoundException: Model use case details have not been
submitted for this account.`**
Bedrock model access for Anthropic models needs to be requested/confirmed
in the Bedrock console (Model access page) before `ChatBedrockConverse`
calls work. If you've already done this, it can take up to ~15 minutes to
propagate — and because the default model is a *cross-region* inference
profile, different requests can route to different underlying regions, so
you may see it succeed on one call and fail on the very next one while
propagation is still in progress. Not a code bug; just wait it out.

**`CreateKnowledgeBase` fails with `ValidationException: ... no such index`**
during `scripts/provision_kb.py`. OpenSearch Serverless's data plane and
Bedrock's control plane have an eventual-consistency gap right after index
creation. The script already waits ~60s and retries on this specific error,
but if you hit it anyway, just re-run the script — everything up to that
point is idempotent and will be skipped.

**Trace link is `null` / "View trace" never appears.**
Check `LANGSMITH_API_KEY` is actually set (not just `LANGSMITH_TRACING`) —
without a valid key, LangChain's background trace uploads fail with a
401 that's easy to miss in server logs. If the key is set and it's still
null, this app already guards against the other known cause (the
`wait_for_all_tracers()` race described in section 2e), so check the
Flask server's console output for a stack trace.

**A deleted document is still answerable, or a just-uploaded one still
comes back "not found" for a few extra seconds after ingestion reports
`COMPLETE`.** A Bedrock Knowledge Base with an S3 data source doesn't watch
the bucket in real time — additions and deletions only take effect on the
*next* ingestion job, which is why deleting from the Upload page (not just
the S3 console) matters: it deletes the object *and* re-triggers ingestion
so the removal actually propagates (`app/documents/routes.py`,
`_start_ingestion_job` / `delete_document`). Separately, even a `COMPLETE`
ingestion job doesn't guarantee the change is *instantly* searchable —
OpenSearch Serverless has a short near-real-time indexing delay (seen
during testing: a few seconds to under a minute) with no separate status
to poll for it. If a demo answer looks stale right after an upload or
delete, this is almost always why — wait a few seconds and ask again
before assuming something's broken.
