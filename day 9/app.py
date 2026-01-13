from langchain_community.document_loaders import TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_ollama import OllamaLLM
from langchain_classic.chains import RetrievalQA



# 1. Load document
loader = TextLoader("D:/Internship/Workouts/day 9/document/sample.txt")
docs = loader.load()

# 2. Split document
text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=80,
    chunk_overlap=20
)
chunks = text_splitter.split_documents(docs)

# 3. Embeddings
embeddings = HuggingFaceEmbeddings(
    model_name="sentence-transformers/all-MiniLM-L6-v2"
)

# 4. Vector DB
vector_db = FAISS.from_documents(chunks, embeddings)

# 5. Local LLM (Ollama)
llm = OllamaLLM(model="mistral")

# 6. RAG Chain
qa_chain = RetrievalQA.from_chain_type(
    llm=llm,
    retriever=vector_db.as_retriever(),
    return_source_documents=True
)

# 7. Ask question
# query = "how old am i?"
# query = "What am i currently studying?"
query = "What are my hobbies?"

result = qa_chain.invoke({"query": query})

print("\nAnswer:\n", result["result"])


