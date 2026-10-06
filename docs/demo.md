# Live demo walkthrough

Open [Agentic Data Analyst](https://agentic-data-analyst-nine.vercel.app).
The demo uses synthetic data and read-only PostgreSQL access.

## Try a question

1. Select **Sales Analytics** or **SaaS Analytics**.
2. Choose an example to fill the form, or enter one of the questions below.
3. Click **Ask Agent**, or use Ctrl/Cmd+Enter. Enter alone adds a newline.
4. Read the grounded answer; expand **Run details** to see its run ID. Switch
   datasets for a new question.

Examples do not auto-submit. Each submission is an independent request and calls
Gemini through the backend; no conversational memory is retained between questions.

## Sales questions

- Which city generated the most revenue from completed orders?
- Which product category has the highest completed-order revenue?
- Show the top five products by revenue from completed orders.

## SaaS questions

- How many subscriptions are there?
- Which plan has the highest current MRR?
- Which accounts generated the most paid invoice revenue?
- How many unresolved high-priority support tickets are there?

These are suggested questions, not guaranteed answers or a fixed tool script.
No expected values are supplied here.

## What happens behind the UI

FastAPI resolves the selected profile to its Sales or SaaS Neon database.
Gemini chooses tools to discover tables, inspect relationships and semantic
comments, and execute guarded read-only SQL. Python returns results as matching
ToolMessage observations; Gemini uses them to produce a concise answer. The
manual loop can correct SQL or tool arguments and stops after at most eight
model responses. It is not a LangGraph agent.

The UI displays answers and safe status information, not hidden reasoning, SQL
execution traces, raw provider errors, server trace paths, or credentials.

## If the demo takes a moment

The Render Free backend can cold start after inactivity
([service lifecycle](https://render.com/docs/free)). Wait for it to wake, then
refresh the connection check or manually retry if needed. Model quota/rate limits
and provider failures can also interrupt a request. The UI does not automatically
retry submitted questions.

For process/configuration checks:

- [Backend health](https://agentic-data-analyst-api.onrender.com/health)
- [Backend readiness](https://agentic-data-analyst-api.onrender.com/ready)

These endpoints do not call Gemini or PostgreSQL. Health proves the process is
serving requests; readiness checks configuration, not database connectivity or
model quota. Use the frontend to ask questions rather than the backend root URL.

## Scope of the demonstration

Runtime database access uses the read-only `analyst_agent` role, READ ONLY
transactions for analytical queries, a five-second statement timeout, and at
most 100 returned rows. No data writes or arbitrary database connections are
supported. The official synthetic-data benchmarks are Sales **22/24 (91.67%)**
and unseen SaaS **14/16 (87.50%)**; they do not guarantee every live answer.

Both production datasets were verified end-to-end by the maintainer. This
documentation update did not rerun the live demo or benchmark questions.
