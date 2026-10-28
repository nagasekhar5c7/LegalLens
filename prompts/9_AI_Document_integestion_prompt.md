parallelly, whenever a user uploads the document, you can also implement the ingestion pipeline. I know there would be two processes:
- If it is a scanned document, use OCR, then do chunking, followed by embedding, and then store it in the vector database.
- If it is an unscanned document, use a loader, then do chunking, followed by embedding, and then store it in the vector database.