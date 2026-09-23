import sys
import ollama
from retrieve import retrieve

def ask(question, model="movie-buff", k=3, show_context=True):
    hits = retrieve(question, k=k)

    if show_context:
        print("--- Retrieved ---")
        for h in hits:
            print(f"  {h['title']}  (distance={h['distance']:.4f})")
        print()

    context = "\n\n---\n\n".join(h["text"] for h in hits) if hits else "(nothing matched in the database)"

    prompt = f"""Here is information on movies that might be relevant to the question:

{context}

Question: {question}

Answer using the information above. If none of it actually answers the question, say so honestly instead of guessing."""

    response = ollama.chat(model=model, messages=[{"role": "user", "content": prompt}])
    return response["message"]["content"]

if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "What's that Scorsese movie with DiCaprio playing an undercover cop?"
    print(ask(q))
