now that all the pieces are implemented separately or independently, what I want to implement is to stitch all those things with the backend. Whenever a user asks a question or sends a message via the turns API, we need to:
- Take that question and send it to an orchestrator.
- From the orchestrator, invoke the workflow. If it's an ask workflow, try to send it to the ask workflow.
- Whatever the response is, send that response to the user.
If there are any missing pieces that we haven't implemented yet in this whole architecture, try to start implementing them as well. Complete these two tasks:
1. Complete the integration between the backend and the AI layer.
2. Identify if there are any opportunities or gaps that we haven't implemented yet in this AI layer, and then start picking them and implementing them end to end.
Show less