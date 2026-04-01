from sentence_transformers import SentenceTransformer

_model = None

def _get_model():
    global _model
    if _model is None:
        # multi-qa-MiniLM is better for Q&A retrieval than all-MiniLM
        _model = SentenceTransformer("multi-qa-MiniLM-L6-cos-v1")
    return _model

def get_embeddings(texts: list) -> list:
    return _get_model().encode(texts, show_progress_bar=False, batch_size=32)

def get_embedding(text: str):
    return _get_model().encode(text)
