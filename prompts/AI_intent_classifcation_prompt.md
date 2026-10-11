now that the frontend and backend are in good shape, let's start focusing on AI right now. Whenever you are building AI, I want you to be super cautious about what decisions you make. Let's start building the orchestrator for now.

From the API/conversation/conversation ID/messages, you would be getting the message. Once you get that message, you would need to identify the intent in the orchestrator, right? Build that orchestrator layer for now as an independent module. Let's not integrate with the backend now. Just implement this orchestration layer.

After implementing this orchestration layer, I would be testing that. At this moment in time, I want to build this project with the Claude models. Can we use the Haiku model because the task itself is just to identify the intent? I just want to be very specific about using the Haiku model here for intent classification. Create a any file, with  prompt.md as postfix. You can draft me that file. It needs to have information about how we classify a question. How do we classify the intent given a question? It's basically it's just like an subject matter expert information on how we classify a question as ask or draft or summarize.  

I want to build this using LangChain, so have all the components for an updated LangChain and just implement this. I need to test this once. If everything is good, we can build the workflow at a later point in time. Let me know if you have any questions. If you are good without any questions, you can start implementing it. If not, you can ask me any questions at any point in time.


can you create me an .env file such I can store api key thir and start testing this intentation classifcation.. with out depending on any other thing