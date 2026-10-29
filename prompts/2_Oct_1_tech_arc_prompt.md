primarily, now that ideas.md and the BRD document are created for this LegalLens, my next step is to come up with technical architecture. In the technical architecture, I want you to create one thing for now: a flow diagram of what is happening. I want an Excalidraw-based diagram here.
Because this is technical, be more clear about how the flow looks from:
- front end to back end
- back end to orchestration
- orchestration to the AI layer
Have all those components as well. If possible, try including APIs.
Primarily, because this is a chatbot, we would be needing:
- a sidebar
- a prompt bar
- inside the bar, all the past conversations
- an NCC API endpoint as well
Have all these endpoints, have that flow from front end to backend, and also try to have more details about databases. Primarily, use Escalate for now for storing all those conversations as a conversation DB and all the transactions whatever users are making.
With respect to the RAG component, primarily here we need to have an LLM that decides the intent based on the user question. If the intent is:
- ask, you need to have an ask workflow.
- draft, you need to have a draft workflow.
- summary, you need to have a summary workflow because you would be taking documents into consideration.
Have an end-to-end RAG workflow. I don't want a simple nav RAG. Have:
- a query editor based on the conversation
- hybrid search
- all those things
Whenever you are doing scan documents or an ingestion pipeline, make sure you use layout, layout checks, or OCRs, whatever is needed. Please include all those in the technical diagram in Excalidraw. Whenever you are creating this, have arrows. I don't want it to be clumsy, so make sure you draw without clumsiness. Draw as big as you can, but I want Flow to be very nice.
At any point in time, if you have any questions regarding any technical details, feel free to ask. Don't hesitate to ask any questions.