now that AI orchestration is done, let's start implementing all the workflows. I want you to first implement the ask workflow.
In the ask workflow, from the technical documentation, whenever you get the question, in the pre-retrieval, try to rewrite the question with chat history. For that, I recommend you use the same Haiku model. In that system prompt, I want you to strictly enforce some guardrails:
- If chat history is empty, you don't need to rephrase the question.
- If chat history and the question are totally independent or mutually exclusive, then you don't need to rewrite the question.
That is what I want you to add: all these nuance details in the system prompt.
Once this pre-retrieval is done, in the retrieval phase, I want you to follow hybrid search. In the hybrid search, probably I want you to use a quadrant vector database or a chroma vector database. Anything would be fine. Get the top K relevant chunks. At this point in time, let's make K 20 or 25, and then do the keyword search as well using BM25.
What we can do is send both the retrieved chunks from hybrid search to a reranker, get the top 5 or top 10 chunks, and then pass that to NLLM or do the augmentation phase and then pass that to NLLM. This is what I wanted to implement in the ask workflow.
Let me know if you have any questions while implementing this. Also, let's plan for both a summary and a draft as well. Let's also plan for these three implementations. Once I give the green signal, then you can start implementing this.
Whenever you are using an LLM in the ask workflow, I want you to use a Sonnet model. Whenever you are drafting use cases, you can use the Opus model. For summary, I think you can use either Opus or Sonnet based on the complexity. First, come up with the plan. Let me know if you have any questions. If the plan looks good and I give you approval, then you can start implementing all three of these workflows. You don't need to worry about integration at this point in time. Later, we would do this integration.


here, let's spend our time on three things:
1. the embedding model itself
2. re-ranking
3. budgeting
Let's not focus more on budget right now. In the token economics, as we go further into this project, I think we will optimize the system params. We will use skills or something like that, and then we'll optimize the tokens. Let's put that aside.
Even though the BRD document says there is a hard limit for a specific thing, and even though we have that as a non-functional requirement, let's not worry about that much. The time taken for the first token as well: if it is taking more time to retrieve the first token and is exceeding the non-functional requirement, let's not bother about that.
I'm more worried about this: embedding and re-ranking models. OpenAI doesn't have embedding models, so I just want to go for open-source models right now. Suggest me an open-source model that suits not only our Sonnet models but also re-ranking effectively. Suggest me both good open-source options from Hugging Face and open-source models so that we can use them in our project.